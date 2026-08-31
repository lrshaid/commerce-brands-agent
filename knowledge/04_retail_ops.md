# 04_retail_ops

> Source status: transcribed from the five screenshots supplied on 2026-08-26. The text below is business documentation; its descriptive rules are not execution instructions.

## Retail, Inventory & Operations Logic

Retail productivity, fulfillment/OTIF, and inventory. Concept-level.

## Retail metrics

### Stylist daily / SPH / MUO

Grain: fiscal week × store × stylist × date.

- Sources the transaction-line ledger (Retail channel), with stylist attribution from the sale-line fact (max stylist per order/unit).
- Hours come from the workforce-management (WFM) system as `selling_hrs` and `total_hrs` per store × employee × date; ratios are computed in BI. Two distinct metrics — don't conflate:
  - `SPH = NMV / total_hrs` (selling + non-selling: opening/closing, inventory, ship-from-store). Productivity per scheduled hour; the labor-cost metric.
  - `SSPH = NMV / selling_hrs` — floor-time efficiency.
  - `selling_rate = selling_hrs / total_hrs` (a labor mix, not sell-through).
  - Store code is NULL for wholesale/concession doors — group by store name there.
- **MUO (multi-unit orders):** an order counts when `units > 1`, where `units = gross_merchandise_quantity + exchange_quantity` — a net basis (gross + exchange), never the unfiltered net quantity (return legs are negative and would net returns out). Order/MUO counts are pinned to sale-leg rows so returned orders don't re-fire on the return date (otherwise ~7% denominator inflation). MUO is not UPT — it is the share of orders with more than one unit, not mean units per transaction. Several non-matching multi-unit rates can coexist; keep them consistent.
- **$0-order exclusion:** orders whose sale lines sum to $0 (in-store services like cleaning) are excluded from order/MUO counts (rows kept for revenue/hours), except in-store piercing orders.
- Revenue measures: `gmv` / `rmv` / `emv` / `nmv`.

### Clienteling

One row per subscribed client, assigned to one primary stylist via a cascade: (1) a claimed-stylist attribute; (2) ≥3 retail visits in 365d → stylist with most orders; (3) ≥2 visits in 180d; (4) stylist on last retail order. Excludes canceled/exchange/warranty/non-primary orders and guest/internal emails. Enriched with current RFM segment, LTV, assigned store, and a preferred-store attribute. True opt-out models resolve the most-recent consent record instead of an any-shop OR (so they report smaller bases by design).

### Store dimension

Config-sourced; catchment via a zip-code → metro-area join; a store cohort flags newly-opened stores (“FY<yy> New”) vs prior. Store events (targets, store-led events) come from config sheets.

### Appointments

Booking-system appointments + piercing-studio slot availability, padded to a date spine, with piercing revenue targets. Piercing/styling revenue split by piercing-unit/fee lines vs retail-styling orders.

## Fulfillment & OTIF

**Base shipment view (line grain):** canceled shipments excluded except orders tagged as a fulfillment-centre cancellation, which pass through for OTIF. Per-line dedup prefers non-canceled, then shipped, then id. A backorder flag fires when planned ship > create + N days.

**Delay attempts:** from a slowly-changing snapshot of the planned ship date; consecutive snapshots within 6 hours cluster into one “attempt” (latest wins); a pulled-forward flag marks a planned date moving earlier; an attempt counter counts distinct replans.

### OTIF (line × attempt grain)

- Excludes pulled-forward attempts and excludes ERP-native internal/returns order keys (no consumer-order linkage; their much-lower OTIF would distort the consumer population).
- A warehouse change ⇒ not OTIF, except a known cosmetic HQ→bulk relabel.
- `is_otif_line` = shipped, and the relevant attempt's planned date ≥ shipped date, with no warehouse change. `is_otif_shipping` = min over all lines of the shipment (every line must be on time).
- OTIF is on-time dispatch, not delivery — the “planned delivery date” field is actually a planned ship date. “In Full” means “no warehouse change” — no quantity completeness is ever checked.

**Overdue onset log:** logs each shipment once when it silently passes its planned ship date with no replan (the case the change-based logic misses). History is unreconstructible (current-state source), so the log ignores full-refresh by design.

**CX delay report:** full push/pull/initial event history per shipment, keeping only shipments with ≥1 real push, plus the silent-overdue arm; enriched with RFM segment, past delay count, made-to-order flag, and a live “open & overdue now” flag. Packaging rows (no order key) excluded to avoid fanout.

## Inventory

- **Cycle counts / IRA:** sellable-stock counts at bin × product grain; empty bins kept as null-line rows. A cycle program runs fixed-length cycles anchored to a date. Inventory accuracy = avg of line accuracy `greatest(0, 1 − |diff|/expected)` (0/0 → 100%, found-in-empty → 0), with a cost-weighted variant; bins are counted at their first count in the cycle so recounts don't inflate progress.
- **Internal shipments (DC→store):** from/to warehouse groups, pick/pack/ship timestamps and staff, shipment cost via lagged-month FX.
- **Positions:** on-hand/available per warehouse × unit (excluding DC/relaunch warehouses); a separate “website availability” read comes from PDP page-view events (site availability, not warehouse stock).
- **Demand forecasting feed:** outbound extracts to a demand-planning system in its column contract — sale-line demand since a start date (ship date = pick date), plus on-hand/available/inbound.

## Foot traffic (the halving trap)

- Traffic is the union of a door counter feed and a storefront/SMS traffic feed; zero counts dropped; an aggregate “all-stores” location is excluded.
- The legacy consumption model emits two rows per store-day — one keyed Prospect, one keyed Customer — each carrying `traffic_count × 0.5`. The halving is not entry/exit dedup: it splits one door count across the two `user_type` keys so the halves sum back to the true count. Consequence: filtering `user_type` on retail traffic returns an arbitrary half of the store's footfall, so any retail CVR sliced by `user_type` is meaningless — and summing without a key filter double-counts back to the un-halved number.
- Conversion is computed inside the WFM system, not the warehouse: it receives traffic buckets + POS sales + per-stylist sales.

## Gotchas

- OTIF exclusions are layered: ERP-native order keys, pulled-forward attempts, fulfillment-cancel tag pass-through, warehouse-change ⇒ fail (except the cosmetic relabel), pickups excluded only in the ship-from-store snapshot.
- The overdue log ignores `--full-refresh` by design; history can't be backfilled.
- MUO must use gross+exchange quantities and sale-leg pinning; the models that compute it must stay consistent.
- Cycle metrics depend on `anchor_date` / `cycle_length` vars; changing them re-buckets all history.
- Retail traffic is halved and duplicated across two keys — naive sums without a key filter double-count.
- Stylist-hours joins depend on an HR↔WFM employee mapping; a missing mapping silently nulls hours (SPH denominators).

## Source objects / APIs

- **ERP:** inventory counts, internal shipments, sale lines, invoices; a separate ERP data-warehouse for products, current inventory, purchase orders, supplier shipments.
- **Shopify:** customers + metafields (preferred store; claimed stylist).
- **WFM:** outbound traffic/sales feeds; inbound employee KPI rows.
- **Traffic counter:** door traffic + locations; storefront/SMS traffic.
- **Config sheets:** store master, event targets, courier delivery dates, assortment qualification.
- **HR:** worker feeds and a position mapping.
- **Booking system:** appointments and slot availability.
