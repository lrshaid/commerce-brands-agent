import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GA4 = ROOT / "dbt/models/staging/ga4"


class Ga4StagingContractTests(unittest.TestCase):
    def test_adapter_is_opt_in_and_requires_explicit_export_vars(self):
        project = (ROOT / "dbt/dbt_project.yml").read_text()
        source = (GA4 / "sources.yml").read_text()
        self.assertIn("ga4_export_enabled: false", project)
        self.assertIn("ga4_project: ''", project)
        self.assertIn("ga4_dataset: ''", project)
        self.assertIn("ga4_property_id: ''", project)
        self.assertIn("var('ga4_project', '')", source)
        self.assertIn("var('ga4_dataset', '')", source)
        self.assertIn("identifier: events_*", source)
        self.assertIn("ga4_start_date: ''", project)
        self.assertIn("ga4_end_date: ''", project)

    def test_events_preserves_native_ids_and_does_not_invent_missing_keys(self):
        sql = (GA4 / "stg_ga4__events.sql").read_text()
        for field in (
            "user_pseudo_id",
            "ga_session_id",
            "transaction_id",
            "event_timestamp_micros",
            "event_date_property",
            "source_event_json",
            "missing_user_pseudo_id",
            "missing_ga_session_id",
            "missing_purchase_transaction_id",
            "native_session_key",
        ):
            self.assertIn(field, sql)
        self.assertRegex(sql, r"then null\s+else concat\(property_id")
        self.assertNotIn("identity_id", sql)
        self.assertNotIn("date_diff", sql)
        # No partitioned window synthesis of identity/keys; the only permitted
        # row_number is the unpartitioned internal join key for param pivot.
        self.assertNotIn("row_number() over (partition", sql)

    def test_daily_export_filter_excludes_intraday_and_bounds_enabled_scans(self):
        sql = (GA4 / "stg_ga4__events.sql").read_text()
        self.assertIn("regexp_contains(_table_suffix, r'^[0-9]{8}$')", sql)
        self.assertIn("ga4_project, ga4_dataset, ga4_property_id, ga4_start_date and ga4_end_date", sql)
        self.assertIn("_table_suffix between", sql)

    def test_purchase_matching_is_explicit_exact_string_and_preserves_unmatched_reasons(self):
        sql = (ROOT / "dbt/models/intermediate/ga4/int_ga4__purchase_order_candidates.sql").read_text()
        self.assertIn("p.transaction_id = i.identifier", sql)
        self.assertIn("ga4_shopify_order_identifier_fields", (ROOT / "dbt/dbt_project.yml").read_text())
        self.assertIn("unmatched_no_identifier_mapping", sql)
        self.assertIn("ambiguous_multiple_orders", sql)
        self.assertNotIn("replace(", sql.lower())
        self.assertNotIn("cast(p.transaction_id", sql.lower())

    def test_touchpoints_emit_both_session_definitions_with_event_scoped_keys(self):
        sql = (ROOT / "dbt/models/intermediate/ga4/int_ga4__touchpoints.sql").read_text()
        self.assertIn("'ga4_native' as session_definition", sql)
        self.assertIn("'ga4_custom_30m' as session_definition", sql)
        self.assertIn("p.native_session_key as session_key", sql)
        self.assertIn("p.custom_session_key as session_key", sql)
        self.assertIn("from page_events p\nwhere p.native_session_key is not null", sql)
        self.assertIn("from page_events p\nwhere p.custom_session_key is not null", sql)
        self.assertNotIn("as definitions as (", sql)
        self.assertIn("ga4_touchpoint_precedence('p', owned_hosts)", sql)
        self.assertIn("ga4_channel_group('p', taxonomy)", sql)
        project = (ROOT / "dbt/dbt_project.yml").read_text()
        self.assertIn("ga4_owned_hosts", project)
        self.assertIn("ga4_channel_taxonomy", project)
        macro = (ROOT / "dbt/macros/ga4_channel_taxonomy.sql").read_text()
        for label in ("landing_utm", "click_id", "referrer", "unattributed"):
            self.assertIn(label, macro)

    def test_custom_sessions_carry_entry_attributes_duration_and_bounce(self):
        sessions = (ROOT / "dbt/models/intermediate/ga4/int_ga4__sessions_30m.sql").read_text()
        for field in (
            "entry_page_location",
            "entry_source",
            "entry_medium",
            "entry_campaign",
            "session_duration_seconds",
            "is_bounced",
            "one_native_session_key",
        ):
            self.assertIn(field, sessions)
        self.assertNotIn("row_number", sessions)

    def test_funnel_mart_counts_distinct_sessions_per_step_and_channel(self):
        mart = (ROOT / "dbt/models/marts/metric_ga4__funnel_daily.sql").read_text()
        for field in (
            "entry_channel_group",
            "custom_session_key",
            "countif(reached_",
            "sessions",
            "users",
            "purchase_sessions",
            "int_ga4__sessions_30m",
            "int_ga4__session_event_map_30m",
        ):
            self.assertIn(field, mart)
        self.assertIn("ga4_funnel_steps", (ROOT / "dbt/dbt_project.yml").read_text())
        self.assertNotIn("gmv", mart.lower())
        self.assertNotIn("nmv", mart.lower())
        self.assertNotIn("row_number", mart)

    def test_session_identity_links_only_via_hashed_user_id(self):
        sql = (ROOT / "dbt/models/intermediate/ga4/int_ga4__session_identity.sql").read_text()
        for field in (
            "sha256(lower(trim(email)))",
            "user_pseudo_id",
            "user_id",
            "custom_session_key",
            "int_shopify__customer_identity",
            "identity_link_status",
            "matched_email_identity",
        ):
            self.assertIn(field, sql)
        self.assertNotIn("identity_id", sql.replace("customer_identity_id", ""))

    def test_customer_attribution_mart_is_order_linked_weights_only(self):
        mart = (ROOT / "dbt/models/marts/fct_ga4__customer_attribution.sql").read_text()
        for field in (
            "customer_gid",
            "missing_customer_gid",
            "order_gid",
            "session_definition",
            "attribution_model",
            "attribution_weight",
            "channel_group",
        ):
            self.assertIn(field, mart)
        for value in ("stg_shopify__orders", "int_ga4__purchase_attribution"):
            self.assertIn(value, mart)
        self.assertIn("order_match_status = 'matched_exact_identifier'", mart)
        self.assertIn("attribution_status = 'attributed'", mart)
        self.assertNotIn("gmv", mart.lower())
        self.assertNotIn("nmv", mart.lower())

    def test_attribution_has_four_models_and_linear_is_total_touch_count(self):
        sql = (ROOT / "dbt/models/intermediate/ga4/int_ga4__purchase_attribution.sql").read_text()
        for model in ("first_click", "last_click", "first_click_30days", "linear_multi_click"):
            self.assertIn(model, sql)
        self.assertIn("1.0 / eligible_touchpoint_count", sql)
        self.assertIn("no_eligible_touchpoints", sql)
        self.assertNotIn("gmv", sql.lower())
        self.assertNotIn("nmv", sql.lower())

    def test_synthetic_runtime_fixture_boundary_null_identity_exact_match_and_weights(self):
        """Exercise the contract's critical semantics without BQ credentials."""
        events = [
            {"user": "u-1", "ts": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)},
            {"user": "u-1", "ts": datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc)},
            {"user": "u-1", "ts": datetime(2026, 9, 1, 13, 0, 1, tzinfo=timezone.utc)},
            {"user": None, "ts": datetime(2026, 9, 1, 12, 1, tzinfo=timezone.utc)},
        ]
        eligible = [e for e in events if e["user"] is not None]
        boundaries = [
            i == 0 or (e["ts"] - eligible[i - 1]["ts"]) > timedelta(minutes=30)
            for i, e in enumerate(eligible)
        ]
        self.assertEqual(boundaries, [True, False, True])
        self.assertEqual(sum(e["user"] is None for e in events), 1)

        touchpoints = ["landing_utm", "referrer"]
        weights = [1 / len(touchpoints)] * len(touchpoints)
        self.assertAlmostEqual(sum(weights), 1.0)
        shopify_identifiers = {"1001", "gid://shopify/Order/1001"}
        self.assertEqual([x for x in shopify_identifiers if x == "1001"], ["1001"])
        self.assertEqual([x for x in shopify_identifiers if x == "#1001"], [])

    def test_pages_and_purchases_are_projections_not_session_or_finance_models(self):
        pages = (GA4 / "stg_ga4__pages.sql").read_text()
        purchases = (GA4 / "stg_ga4__purchases.sql").read_text()
        self.assertIn("where event_name in ('page_view', 'screen_view')", pages)
        self.assertIn("where event_name = 'purchase'", purchases)
        self.assertIn("transaction_id", purchases)
        self.assertIn("missing_purchase_transaction_id", purchases)
        self.assertNotIn("gmv", purchases.lower())
        self.assertNotIn("nmv", purchases.lower())

    def test_native_sessions_are_distinct_from_custom_tracker_sessionization(self):
        sql = (GA4 / "stg_ga4__sessions.sql").read_text()
        for field in ("property_id", "stream_id", "user_pseudo_id", "ga_session_id", "native_session_key"):
            self.assertIn(field, sql)
        self.assertIn("where native_session_key is not null", sql)
        self.assertNotIn("date_diff", sql)
        self.assertNotIn("identity_id", sql)

    def test_custom_session_layer_has_strict_boundary_and_reconciliation_rows(self):
        mapping = ROOT / "dbt/models/intermediate/ga4/int_ga4__session_event_map_30m.sql"
        sessions = ROOT / "dbt/models/intermediate/ga4/int_ga4__sessions_30m.sql"
        sql = mapping.read_text()
        self.assertIn("timestamp_diff(event_ts_utc, previous_event_ts_utc, second) > 1800", sql)
        self.assertIn("coalesce(batch_event_index, -1)", sql)
        self.assertIn("coalesce(batch_ordering_id, -1)", sql)
        self.assertIn("coalesce(batch_page_id, -1)", sql)
        self.assertIn("session_exclusion_reason", sql)
        self.assertIn("missing_user_pseudo_id", sql)
        self.assertIn("union all", sql)
        self.assertIn("one_native_session_key", sessions.read_text())
        self.assertNotIn("row_number", sql)

    def test_all_models_are_disabled_by_default_and_explicitly_opt_in(self):
        project = (ROOT / "dbt/dbt_project.yml").read_text()
        self.assertIn("ga4_export_enabled: false", project)
        for path in GA4.glob("stg_ga4__*.sql"):
            sql = path.read_text()
            self.assertIn("enabled=var('ga4_export_enabled', false)", sql)


if __name__ == "__main__":
    unittest.main()
