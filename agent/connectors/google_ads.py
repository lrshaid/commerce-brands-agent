from __future__ import annotations

from typing import Any, Dict

from .base import read_only_request, required_env


def google_ads_gaql(query: str) -> Dict[str, Any]:
    try:
        env = required_env(
            "GOOGLE_ADS_CUSTOMER_ID",
            "GOOGLE_ADS_DEVELOPER_TOKEN",
            "GOOGLE_ADS_ACCESS_TOKEN",
            "GOOGLE_ADS_API_VERSION",
        )
        customer = env["GOOGLE_ADS_CUSTOMER_ID"].replace("-", "")
        return read_only_request(
            "POST",
            (
                "https://googleads.googleapis.com/"
                f"{env['GOOGLE_ADS_API_VERSION']}/customers/{customer}/"
                "googleAds:searchStream"
            ),
            headers={
                "Authorization": f"Bearer {env['GOOGLE_ADS_ACCESS_TOKEN']}",
                "developer-token": env["GOOGLE_ADS_DEVELOPER_TOKEN"],
            },
            json_body={"query": query},
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
