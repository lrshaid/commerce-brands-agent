"""Compile the Klaviyo campaigns snapshot request into a read plan.

Campaigns are low-volume configuration objects, so the capture is a full
archived-filtered snapshot rather than a time window: one cursor chain against
the Campaigns list endpoint with server-side filtering, following ``links.next``
where ``params`` are sent only on the first request.  GET-only, read-only.
"""
from dataclasses import dataclass
import re

API_REVISION = "2026-07-15.pre"
CAMPAIGNS_BASE_URL = "https://a.klaviyo.com/api/campaigns"
CAMPAIGNS_SORT = "-updated_at"
CAMPAIGNS_INCLUDE = "campaign-audiences,campaign-messages,campaign-variations"
_FILTER_DELIMITERS = re.compile(r'["\',\n\r]')


class KlaviyoCampaignsRequestError(ValueError):
    pass


@dataclass(frozen=True)
class KlaviyoCampaignsPlan:
    archived: bool
    first_params: dict

    def request_params(self, cursor_url=None):
        if cursor_url is None:
            return dict(self.first_params)
        if not isinstance(cursor_url, str) or not cursor_url.startswith(CAMPAIGNS_BASE_URL):
            raise KlaviyoCampaignsRequestError("Klaviyo cursor must stay on the Campaigns API origin")
        return None


def compile_klaviyo_campaigns_plan(archived=False, page_size=100):
    if not isinstance(archived, bool):
        raise KlaviyoCampaignsRequestError("Klaviyo archived filter must be a boolean")
    if (not isinstance(page_size, int) or isinstance(page_size, bool)
            or not 1 <= page_size <= 100):
        raise KlaviyoCampaignsRequestError("Klaviyo campaigns page size must be between 1 and 100")
    params = {"page[size]": page_size, "sort": CAMPAIGNS_SORT, "include": CAMPAIGNS_INCLUDE,
              "filter": f"equals(archived,{str(archived).lower()})"}
    return KlaviyoCampaignsPlan(archived=archived, first_params=params)
