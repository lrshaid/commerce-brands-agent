"""Immutable read-only paginated capture for the Klaviyo campaigns snapshot.

Mirrors the events capture contract exactly: GET-only, no Klaviyo writes or
deletions, generation+checksum pinned GCS pages, a create-only binding and a
completion seal written once the single cursor chain finished.  Campaigns are
account-scoped configuration objects, so the capture is a full snapshot of the
archived-filtered list (no time window); the account is pinned by
``api_key_sha256`` inside the binding.  The four-level resource hierarchy
(campaign -> audience -> message -> variation) is validated fail-closed per
page: every included child must reference a parent present on the same page.
"""
from datetime import datetime, timezone
import email.utils
import re
import time

import requests
from google.api_core.exceptions import PreconditionFailed

from .refund_capture import CaptureError, decode, digest, encoded
from .klaviyo_campaigns_queries import (API_REVISION, CAMPAIGNS_BASE_URL,
                                        KlaviyoCampaignsRequestError,
                                        compile_klaviyo_campaigns_plan)

_BACKOFF_CAP_SECONDS = 120
_MAX_BACKOFF_ATTEMPTS = 5
_INCLUDED_TYPES = ("campaign-audience", "campaign-message", "campaign-variation")


class KlaviyoCampaignsCapture:
    def __init__(self, *, bucket, token, account_key, extraction_id, archived=False,
                 page_size=100, timeout_seconds=900, max_pages=2000, max_attempts=5,
                 max_bytes=256 * 1024 * 1024, max_page_bytes=8 * 1024 * 1024, read_only=False):
        if not isinstance(account_key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", account_key):
            raise CaptureError("Invalid Klaviyo account identity")
        if not extraction_id or (not read_only and not token.strip()):
            raise CaptureError("Explicit extraction identity and credential are required")
        if (not 1 <= page_size <= 100 or not 1 <= timeout_seconds <= 7200
                or not 1 <= max_pages <= 100000 or not 1 <= max_attempts <= 10
                or not 64 * 1024 <= max_page_bytes <= 32 * 1024 * 1024
                or not 1 <= max_bytes <= 256 * 1024 * 1024):
            raise CaptureError("Invalid capture bounds")
        plan = compile_klaviyo_campaigns_plan(archived, page_size)
        self.bucket, self._token = bucket, token.strip()
        self.account_key, self.page_size = account_key, page_size
        self.plan = plan
        self.binding = {
            "format_version": 1, "stream": "campaigns", "account_key": account_key,
            "revision": API_REVISION, "extraction_id": extraction_id,
            "api_key_sha256": digest(self._token.encode()),
            "archived": plan.archived,
            "plan_sha256": digest(encoded(plan.first_params)),
            "scope_sha256": digest(encoded({"archived": plan.archived, "page[size]": page_size})),
        }
        key = digest(encoded([account_key, extraction_id, "campaigns"]))
        self.prefix = f"pages/v1/klaviyo_campaigns/{key}"
        self.read_only = read_only
        self.deadline = time.monotonic() + timeout_seconds
        self.max_pages, self.max_bytes = max_pages, max_bytes
        self.max_attempts = max_attempts
        self.max_page_bytes = max_page_bytes
        self.pages, self.bytes, self._request_keys, self._finished = [], 0, set(), False
        self._page_counts = {"campaigns": 0}
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
                raise CaptureError("Extraction identity is already bound to another Klaviyo campaigns plan")

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
        if not isinstance(url, str) or not url.startswith(CAMPAIGNS_BASE_URL):
            raise CaptureError("Klaviyo cursor left the Campaigns API origin")
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
                    raise CaptureError("Klaviyo campaigns transport exhausted its bounded backoff")
                time.sleep(min(5 * 2 ** backoff, _BACKOFF_CAP_SECONDS))
                continue
            raise CaptureError(f"Klaviyo campaigns page request failed ({status})")

    def _request_variables(self, params, url):
        return dict(params) if params is not None else {"cursor": url}

    def _request_hash(self, params, url):
        return digest(encoded(self._request_variables(params, url)))

    def fetch(self, url, params):
        if self._finished:
            raise CaptureError("Capture is already sealed")
        if self._page_counts["campaigns"] >= self.max_pages:
            raise CaptureError("Klaviyo capture page limit reached")
        if time.monotonic() >= self.deadline:
            raise CaptureError("Klaviyo capture deadline reached")
        request_hash = self._request_hash(params, url)
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
                     "operation": "campaigns", "variables": self._request_variables(params, url),
                     "captured_at": metadata.get("captured_at")}
        self.pages.append(reference)
        self._request_keys.add(request_hash)
        self._page_counts["campaigns"] += 1
        return reference, data

    def _page_campaigns(self, payload):
        """Validate one JSON:API page of the campaign hierarchy, fail closed."""
        included = payload.get("included", [])
        if not isinstance(included, list):
            raise CaptureError("Invalid Klaviyo included collection")
        campaigns, audiences, messages = {}, {}, {}
        for item in included:
            if not isinstance(item, dict) or item.get("type") not in _INCLUDED_TYPES:
                raise CaptureError("Klaviyo page contains an unexpected included resource")
            if not isinstance(item.get("id"), str) or not item["id"]:
                raise CaptureError("Klaviyo included resource is missing its identity")
            if not isinstance(item.get("relationships"), dict):
                raise CaptureError("Klaviyo included resource is missing its relationships")
            if item["type"] == "campaign-audience":
                audiences[item["id"]] = item
            elif item["type"] == "campaign-message":
                messages[item["id"]] = item
        for campaign in payload["data"]:
            if not isinstance(campaign, dict) or campaign.get("type") != "campaign" \
                    or not isinstance(campaign.get("id"), str) or not campaign["id"]:
                raise CaptureError("Invalid Klaviyo campaign identity")
            if not isinstance(campaign.get("attributes"), dict):
                raise CaptureError("Klaviyo campaign is missing its attributes")
            campaigns[campaign["id"]] = campaign
        for audience in audiences.values():
            parent = audience["relationships"].get("campaign")
            parent_data = parent.get("data") if isinstance(parent, dict) else None
            if not isinstance(parent_data, dict) or parent_data.get("id") not in campaigns:
                raise CaptureError("Klaviyo audience references a campaign missing from the page")
        for message in messages.values():
            parent = message["relationships"].get("campaign-audience")
            parent_data = parent.get("data") if isinstance(parent, dict) else None
            if not isinstance(parent_data, dict) or parent_data.get("id") not in audiences:
                raise CaptureError("Klaviyo message references an audience missing from the page")
            parent = message["relationships"].get("campaign")
            parent_data = parent.get("data") if isinstance(parent, dict) else None
            if not isinstance(parent_data, dict) or parent_data.get("id") not in campaigns:
                raise CaptureError("Klaviyo message references a campaign missing from the page")
        for item in included:
            if item["type"] != "campaign-variation":
                continue
            parent = item["relationships"].get("campaign-message")
            parent_data = parent.get("data") if isinstance(parent, dict) else None
            if not isinstance(parent_data, dict) or parent_data.get("id") not in messages:
                raise CaptureError("Klaviyo variation references a message missing from the page")
        return list(campaigns.values()), included

    def walk(self):
        """Yield one (campaign, included) pair per campaign, in page order."""
        url, params, cursors, identifiers = CAMPAIGNS_BASE_URL, self.plan.first_params, set(), set()
        while True:
            reference, data = self.fetch(url, params)
            campaigns, included = self._page_campaigns(data)
            next_url = (data.get("links") or {}).get("next")
            if not isinstance(next_url, (str, type(None))):
                raise CaptureError("Invalid Klaviyo pagination cursor")
            if not campaigns:
                if next_url:
                    raise CaptureError("Nonadvancing Klaviyo pagination cursor")
                return
            for campaign in campaigns:
                if campaign["id"] in identifiers:
                    raise CaptureError("Duplicate Klaviyo campaign across pages")
                identifiers.add(campaign["id"])
                yield campaign, included
            if next_url is None:
                return
            if next_url in cursors:
                raise CaptureError("Nonadvancing Klaviyo pagination cursor")
            # Cursor chains keep following links.next; params go only on the
            # first request and the cursor must stay on the Campaigns API origin.
            try:
                self.plan.request_params(cursor_url=next_url)
            except KlaviyoCampaignsRequestError:
                raise CaptureError("Klaviyo cursor left the Campaigns API origin") from None
            url, params = next_url, None

    def collect(self):
        count = 0
        for _ in self.walk():
            count += 1
        seal = {"binding": self.binding, "status": "captured", "pages": self.pages,
                "counts": {"campaigns": count}, "response_bytes": self.bytes,
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
