"""Immutable read-only paginated capture for the Klaviyo metric event streams.

Klaviyo is account-scoped: the credential is one account API key.  Following the
repo's raw-envelope convention, ``shop_key`` carries the stable account-scoped
registry identity (for Shopify it happens to be a Shop gid); for Klaviyo it is
the configured ``account_key`` and the concrete account is pinned by
``api_key_sha256`` inside the capture binding, so a different key can never
replay the same extraction identity.  No Klaviyo mutations exist here: GET only,
no profile writes, no deletions.  Generation+checksum pinned objects, a
create-only binding and a completion seal (written only once every configured
metric stream finished, in priority order) follow the catalog capture exactly.
"""
from datetime import datetime, timezone
import email.utils
import re
import time

import requests
from google.api_core.exceptions import PreconditionFailed

from .refund_capture import CaptureError, decode, digest, encoded
from .klaviyo_queries import (API_REVISION, EVENTS_BASE_URL, KlaviyoRequestError,
                              compile_klaviyo_event_plans)

_BACKOFF_CAP_SECONDS = 120
_MAX_BACKOFF_ATTEMPTS = 5


class KlaviyoCapture:
    def __init__(self, *, bucket, token, account_key, extraction_id, metrics,
                 window_start, window_end, page_size=200, timeout_seconds=900,
                 max_pages=2000, max_attempts=5, max_bytes=256 * 1024 * 1024,
                 max_page_bytes=8 * 1024 * 1024, read_only=False):
        if not isinstance(account_key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", account_key):
            raise CaptureError("Invalid Klaviyo account identity")
        if not extraction_id or (not read_only and not token.strip()):
            raise CaptureError("Explicit extraction identity and credential are required")
        if (not 1 <= page_size <= 200 or not 1 <= timeout_seconds <= 7200
                or not 1 <= max_pages <= 100000 or not 1 <= max_attempts <= 10
                or not 64 * 1024 <= max_page_bytes <= 32 * 1024 * 1024
                or not 1 <= max_bytes <= 2 * 1024 ** 3):
            raise CaptureError("Invalid capture bounds")
        plans = compile_klaviyo_event_plans(metrics, window_start, window_end, page_size)
        self.bucket, self._token = bucket, token.strip()
        self.account_key, self.page_size = account_key, page_size
        self.plans = plans
        self.start_iso = plans[0].window_start_iso
        self.end_iso = plans[0].window_end_iso
        self.binding = {
            "format_version": 1, "stream": "events", "account_key": account_key,
            "revision": API_REVISION, "extraction_id": extraction_id,
            "api_key_sha256": digest(self._token.encode()),
            "metrics": [{"metric_id": plan.metric_id, "event_type": plan.event_type}
                        for plan in plans],
            "plan_sha256": digest(encoded({plan.metric_id: plan.first_params for plan in plans})),
            "scope_sha256": digest(encoded({"window": [self.start_iso, self.end_iso],
                                            "page[size]": page_size})),
        }
        key = digest(encoded([account_key, extraction_id, "events"]))
        self.prefix = f"pages/v1/klaviyo_events/{key}"
        self.read_only = read_only
        self.deadline = time.monotonic() + timeout_seconds
        self.max_pages, self.max_bytes = max_pages, max_bytes
        self.max_attempts = max_attempts
        self.max_page_bytes = max_page_bytes
        self.pages, self.bytes, self._request_keys, self._finished = [], 0, set(), False
        self._page_counts = {plan.metric_id: 0 for plan in plans}
        self._bind()

    def _bind(self):
        blob = self.bucket.blob(f"{self.prefix}/intent.json")
        if self.read_only:
            existing = self.bucket.get_blob(blob.name)
            if existing is None or decode(existing.download_as_bytes(if_generation_match=int(existing.generation))) != self.binding:
                raise CaptureError("Missing or conflicting read-only capture binding")
            return
        try:
            blob.upload_from_string(encoded(self.binding), content_type="application/json", if_generation_match=0)
        except PreconditionFailed:
            blob.reload()
            if decode(blob.download_as_bytes(if_generation_match=int(blob.generation))) != self.binding:
                raise CaptureError("Extraction identity is already bound to another Klaviyo plan")

    def _headers(self):
        return {"Authorization": f"Klaviyo-API-Key {self._token}",
                "revision": API_REVISION, "accept": "application/vnd.api+json"}

    def _retry_after_seconds(self, headers):
        value = headers.get("Retry-After") if headers else None
        if value is None:
            raise CaptureError("Klaviyo 429 response without Retry-After")
        try:
            seconds = int(value)
            if seconds < 0:
                raise ValueError()
        except ValueError:
            try:
                at = email.utils.parsedate_to_datetime(value)
                if at is None:
                    raise ValueError()
                seconds = max(0, (at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                raise CaptureError("Klaviyo 429 Retry-After is unparseable") from None
        if seconds > 3600:
            raise CaptureError("Klaviyo 429 Retry-After exceeds the capture bound")
        return seconds

    def _http(self, url, params):
        if self.read_only:
            raise CaptureError("Read-only capture cannot call Klaviyo")
        if not isinstance(url, str) or not url.startswith(EVENTS_BASE_URL):
            raise CaptureError("Klaviyo cursor left the Events API origin")
        backoff = 0
        while True:
            if time.monotonic() >= self.deadline:
                raise CaptureError("Klaviyo capture deadline reached")
            status = None
            try:
                with requests.Session() as session:
                    session.trust_env = False
                    with session.get(url, params=params, headers=self._headers(), stream=True,
                                     allow_redirects=False, timeout=(10, 30)) as response:
                        if response.status_code == 429:
                            # Throttling reads Retry-After and waits without consuming
                            # the backoff retry budget.
                            time.sleep(self._retry_after_seconds(response.headers))
                            continue
                        if response.status_code != 200:
                            status = response.status_code
                        else:
                            body = bytearray()
                            for chunk in response.iter_content(chunk_size=65536):
                                if time.monotonic() >= self.deadline:
                                    raise CaptureError("Klaviyo capture deadline reached")
                                body.extend(chunk)
                                if len(body) > self.max_page_bytes:
                                    raise CaptureError("HTTP response exceeds capture limit")
                            return bytes(body)
            except CaptureError:
                raise
            except Exception:
                status = None
            if status is None or 500 <= status < 600:
                backoff += 1
                if backoff > self.max_attempts:
                    raise CaptureError("Klaviyo events transport exhausted its bounded backoff")
                time.sleep(min(5 * 2 ** backoff, _BACKOFF_CAP_SECONDS))
                continue
            raise CaptureError(f"Klaviyo events page request failed ({status})")

    def _request_variables(self, params, url):
        return dict(params) if params is not None else {"cursor": url}

    def _request_hash(self, metric_id, params, url):
        return digest(encoded({"metric_id": metric_id, **self._request_variables(params, url)}))

    def fetch(self, metric_id, url, params):
        if self._finished:
            raise CaptureError("Capture is already sealed")
        if metric_id not in self._page_counts:
            raise CaptureError("Unknown Klaviyo metric stream")
        if self._page_counts[metric_id] >= self.max_pages:
            raise CaptureError("Klaviyo capture page limit reached")
        if time.monotonic() >= self.deadline:
            raise CaptureError("Klaviyo capture deadline reached")
        request_hash = self._request_hash(metric_id, params, url)
        if request_hash in self._request_keys:
            raise CaptureError("Duplicate Klaviyo page request within traversal")
        name = f"{self.prefix}/{request_hash}.json"
        existing = self.bucket.get_blob(name)
        if existing is None:
            if self.read_only:
                raise CaptureError("Missing Klaviyo page in read-only capture")
            body = self._http(url, params)
            blob = self.bucket.blob(name)
            blob.metadata = {"response_sha256": digest(body), "request_sha256": request_hash,
                             "revision": API_REVISION,
                             "captured_at": datetime.now(timezone.utc).isoformat()}
            try:
                blob.upload_from_string(body, content_type="application/json", if_generation_match=0)
                existing = blob
            except PreconditionFailed:
                existing = self.bucket.get_blob(name)
        if existing is None or existing.generation is None or existing.size > self.max_page_bytes:
            raise CaptureError("Missing or oversized captured Klaviyo page")
        body = existing.download_as_bytes(if_generation_match=int(existing.generation))
        metadata = existing.metadata or {}
        if (metadata.get("response_sha256") != digest(body)
                or metadata.get("request_sha256") != request_hash
                or metadata.get("revision") != API_REVISION):
            raise CaptureError("Captured Klaviyo page checksum or identity mismatch")
        try:
            if datetime.fromisoformat(metadata["captured_at"]).utcoffset() is None:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise CaptureError("Captured Klaviyo page timestamp is missing or invalid") from None
        self.bytes += len(body)
        if self.bytes > self.max_bytes:
            raise CaptureError("Total Klaviyo capture size limit reached")
        data = decode(body)
        if not isinstance(data.get("data"), list) or data.get("errors"):
            raise CaptureError("Captured Klaviyo response is incomplete or failed")
        links = data.get("links", {})
        if links is not None and not isinstance(links, dict):
            raise CaptureError("Invalid Klaviyo pagination response")
        reference = {"uri": f"gs://{self.bucket.name}/{name}", "generation": str(existing.generation),
                     "sha256": digest(body), "request_sha256": request_hash,
                     "operation": metric_id, "variables": self._request_variables(params, url),
                     "captured_at": metadata.get("captured_at")}
        self.pages.append(reference)
        self._request_keys.add(request_hash)
        self._page_counts[metric_id] += 1
        return reference, data

    def _page_events(self, metric_id, payload):
        included = payload.get("included", [])
        if not isinstance(included, list):
            raise CaptureError("Invalid Klaviyo included collection")
        profiles = {}
        for item in included:
            if not isinstance(item, dict) or item.get("type") != "profile":
                raise CaptureError("Klaviyo page contains an unexpected included resource")
            if not isinstance(item.get("id"), str) or not item["id"]:
                raise CaptureError("Klaviyo included profile is missing its identity")
            profiles[item["id"]] = item
        events = []
        for event in payload["data"]:
            if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not event["id"]:
                raise CaptureError("Invalid Klaviyo event identity")
            relationships = event.get("relationships")
            if not isinstance(relationships, dict):
                raise CaptureError("Klaviyo event is missing its relationships")
            metric = relationships.get("metric")
            metric_data = metric.get("data") if isinstance(metric, dict) else None
            if not isinstance(metric_data, dict) or metric_data.get("id") != metric_id:
                raise CaptureError("Klaviyo page contains an event outside its filtered metric")
            profile = relationships.get("profile")
            profile_data = profile.get("data") if isinstance(profile, dict) else None
            # Historical events may reference profiles deleted from Klaviyo
            # (GDPR deletions): the relationship id survives but the included
            # profile never resolves. The event is real; keep it with its
            # profile_gid and let the email projection be NULL instead of
            # losing the event or aborting historical backfills.
            if (isinstance(profile_data, dict) and profile_data.get("id") is not None
                    and not isinstance(profile_data.get("id"), str)):
                raise CaptureError("Klaviyo event profile identity is invalid")
            if not isinstance(event.get("attributes"), dict):
                raise CaptureError("Klaviyo event is missing its attributes")
            yield event, profiles

    def walk(self, plan):
        """Yield one (event, profiles) pair per event, in the page order served."""
        metric_id = plan.metric_id
        url, params, cursors, identifiers = EVENTS_BASE_URL, plan.first_params, set(), set()
        while True:
            reference, data = self.fetch(metric_id, url, params)
            events = list(self._page_events(metric_id, data))
            next_url = (data.get("links") or {}).get("next")
            if not isinstance(next_url, (str, type(None))):
                raise CaptureError("Invalid Klaviyo pagination cursor")
            if not events:
                if next_url:
                    raise CaptureError("Nonadvancing Klaviyo pagination cursor")
                return
            for event, profiles in events:
                if event["id"] in identifiers:
                    raise CaptureError("Duplicate Klaviyo event across pages")
                identifiers.add(event["id"])
                yield event, profiles
            if next_url is None:
                return
            if next_url in cursors:
                raise CaptureError("Nonadvancing Klaviyo pagination cursor")
            # Cursor chains keep following links.next; params go only on the
            # first request and the cursor must stay on the Events API origin.
            try:
                plan.request_params(cursor_url=next_url)
            except KlaviyoRequestError:
                raise CaptureError("Klaviyo cursor left the Events API origin") from None
            url, params = next_url, None

    def collect(self):
        counts = {}
        for plan in self.plans:
            count = 0
            for _ in self.walk(plan):
                count += 1
            counts[plan.metric_id] = count
        seal = {"binding": self.binding, "status": "captured", "pages": self.pages,
                "counts": counts, "response_bytes": self.bytes,
                "consistency": "multi_request_observations_not_transactional_snapshot"}
        content = encoded(seal)
        blob = self.bucket.blob(f"{self.prefix}/complete.json")
        if self.read_only:
            existing = self.bucket.get_blob(blob.name)
            if existing is None or existing.download_as_bytes(if_generation_match=int(existing.generation)) != content:
                raise CaptureError("Missing or conflicting Klaviyo completion seal")
        else:
            try:
                blob.upload_from_string(content, content_type="application/json", if_generation_match=0)
            except PreconditionFailed:
                blob.reload()
                if blob.download_as_bytes(if_generation_match=int(blob.generation)) != content:
                    raise CaptureError("Conflicting completed Klaviyo capture")
        self._finished = True
        return seal
