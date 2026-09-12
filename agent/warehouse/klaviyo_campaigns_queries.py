"""Compile the Klaviyo campaigns snapshot request set into read plans.

Campaigns are low-volume configuration objects, so the capture is a full
archived-filtered snapshot rather than a time window.  The Campaigns list
endpoint does not permit ``campaign-variations`` as an include (verified live,
400), so the snapshot is two ordered cursor chains against the same account:

1. ``campaigns_list`` — campaigns with audiences and messages included.
2. ``messages_list`` — campaign messages with their campaign and channel
   variations included (the only include set the messages endpoint permits).

Both chains follow ``links.next`` where ``params`` are sent only on the first
request.  GET-only, read-only.
"""
from dataclasses import dataclass
import re

API_REVISION = "2026-07-15.pre"
CAMPAIGNS_BASE_URL = "https://a.klaviyo.com/api/campaigns"
MESSAGES_BASE_URL = "https://a.klaviyo.com/api/campaign-messages"
CAMPAIGNS_SORT = "-updated_at"
MESSAGES_SORT = "-updated"
CAMPAIGNS_INCLUDE = "campaign-audiences,campaign-messages"
MESSAGES_INCLUDE = "campaign,campaign-variations"
CAMPAIGNS_OPERATION = "campaigns_list"
MESSAGES_OPERATION = "messages_list"
_FILTER_DELIMITERS = re.compile(r'["\',\n\r]')


class KlaviyoCampaignsRequestError(ValueError):
    pass


@dataclass(frozen=True)
class KlaviyoCampaignsPlan:
    operation: str
    base_url: str
    archived: bool | None
    first_params: dict

    def request_params(self, cursor_url=None):
        if cursor_url is None:
            return dict(self.first_params)
        if not isinstance(cursor_url, str) or not cursor_url.startswith(self.base_url):
            raise KlaviyoCampaignsRequestError("Klaviyo cursor must stay on its endpoint origin")
        return None


def compile_klaviyo_campaigns_list_plan(archived=None, page_size=100):
    if archived is not None and not isinstance(archived, bool):
        raise KlaviyoCampaignsRequestError("Klaviyo archived filter must be a boolean or None")
    if (not isinstance(page_size, int) or isinstance(page_size, bool)
            or not 1 <= page_size <= 100):
        raise KlaviyoCampaignsRequestError("Klaviyo campaigns page size must be between 1 and 100")
    params = {"page[size]": page_size, "sort": CAMPAIGNS_SORT, "include": CAMPAIGNS_INCLUDE}
    if archived is not None:
        params["filter"] = f"equals(archived,{str(archived).lower()})"
    return KlaviyoCampaignsPlan(operation=CAMPAIGNS_OPERATION, base_url=CAMPAIGNS_BASE_URL,
                                archived=archived, first_params=params)


def compile_klaviyo_messages_list_plan(page_size=100):
    if (not isinstance(page_size, int) or isinstance(page_size, bool)
            or not 1 <= page_size <= 100):
        raise KlaviyoCampaignsRequestError("Klaviyo campaigns page size must be between 1 and 100")
    params = {"page[size]": page_size, "sort": MESSAGES_SORT, "include": MESSAGES_INCLUDE}
    return KlaviyoCampaignsPlan(operation=MESSAGES_OPERATION, base_url=MESSAGES_BASE_URL,
                                archived=None, first_params=params)


def compile_klaviyo_campaigns_plans(archived=None, page_size=100):
    """Compile the ordered two-chain snapshot: campaigns list, then messages list.

    ``archived=None`` (default) captures every campaign, archived or not; the
    messages chain is unfiltered and already carries messages of archived
    campaigns, so their parents must be in the campaigns chain for the
    hierarchy to resolve.
    """
    return (compile_klaviyo_campaigns_list_plan(archived, page_size),
            compile_klaviyo_messages_list_plan(page_size))
