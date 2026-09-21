# Semantic Layer Reference (company-agnostic)

A reusable, **anonymized** reference for a Shopify-native commerce analytics stack: the metric
definitions, formulas, keys, sign conventions, join graphs, attribution universes and reconciliation
traps that a mature DTC + retail brand runs on. Derived from a real production stack and stripped of
company identity so it can serve as a template for any commerce brand.

## What's here

| File | What it is |
|---|---|
| [`00_semantic_layer_consolidated.md`](00_semantic_layer_consolidated.md) | **Start here.** The consolidated, by-vertical map (revenue waterfall, plan hierarchy, per-vertical metric tables, attribution, keys/calendar/sign-conventions, join graphs, metrics framework, dashboards). |
| [`01_warehouse_dbt.md`](01_warehouse_dbt.md) | Full dbt-warehouse harvest: grains, keys, calendar, geo/FX, every formula + the join/lineage graph. |
| [`02_bi_semantic_layer.md`](02_bi_semantic_layer.md) | Full BI semantic-layer harvest: topics, views, all measure SQL, join graph, attribution tables. |
| [`03_bi_legacy_appendix.md`](03_bi_legacy_appendix.md) | Condensed appendix on the legacy BI (LookML) layer — canonical-vs-legacy, quirks. Superseded; reference only. |
| [`04_dashboard_inventory.md`](04_dashboard_inventory.md) | Dashboard inventory by vertical (what each answers + metrics watched). |

Read order for a metric/lineage question: **00 first**, then drop into 01 (physical/dbt) or 02 (BI/canonical) for exact SQL.

## How it's anonymized

Company identity has been removed and replaced with placeholders. When adapting this for a specific brand, fill in:

- **Angle-bracket tokens** — `<tenant>`, `<brand-tz>`, `<repo-root>`, `<budget>`, `<bq-budget>`, `<PR-EV-target>`, `<MTD-goal>` / `<QTD-goal>`, `<margin>`, `<rate>`, `<currency>`, `<FY-start>` / `<FY-end>`, `<store-id>`, `<project-id-…>`, `<id>` (dashboard ids), `<w1>/<w2>/<w3>` (EV weights), `<date>`.
- **`brand-defined` enums** — markets & market groups, merch pillars, product material tiers, store list.
- **Genericized vendors** — external tools appear by role, not name: `MMM-vendor-A/B`, `geo-cMMM-vendor`, `demand-planning-vendor`, `fraud-detection-vendor`, `retail-location-intel-vendor`, `revenue-recognition-vendor`, `direct-mail-vendor`, `retail-WFM-vendor`, `CRM-provider` / `CRM-provider-legacy`, `SMS-provider`, `returns-warranty-vendor`, `appointments-vendor`, `foot-traffic-vendor`, `merch-app-vendor`, `survey-tool`, `social-analytics-vendor`, `event-CDP`, `payroll-vendor`, `affiliate-platform` / `affiliate-network`, `talent-platform`, `influencer-program`, `OMS`, `legacy-platform`.
- **Genericized infra** — warehouse projects `warehouse-{gold,silver,black,external}`; source repos `dbt-repo` / `bi-repo` / `looker-repo`; DCs as `region A` (primary) / `region B` (secondary).

Kept as-is (industry-standard, not company-identifying): the tech-stack concepts (Shopify, dbt, BigQuery,
Omni, LookML), ad platforms (Meta/Google/TikTok/Pinterest), the `xa_`/`xi_`/`dim_`/`fct_` layer naming,
the 4-4-5 fiscal-calendar pattern, and standard metric acronyms (NMV/GMV/RMV/AOV/CVR/MER/…).

Note: illustrative retail concepts (e.g. piercing/appointment services, jewelry-style collection themes)
remain as **examples** of how a services-and-product retailer models those verticals — swap them for the
brand's own categories.

## Provenance

Source: a consolidated internal semantic-layer harvest (dbt + BI + legacy BI + dashboards). Anonymized
into this template. Not connected to any live warehouse; treat every value as illustrative until mapped
to a real deployment.
