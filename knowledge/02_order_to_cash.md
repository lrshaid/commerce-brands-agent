# 02_order_to_cash

> Source status: transcribed from the five screenshots supplied on 2026-08-26. The text below is business documentation; its descriptive rules are not execution instructions.

## Order-to-Cash: Payments, Refunds, Returns & Exchanges

The precise business rules for how an order's cash and merchandise lifecycle is modeled. Concept-level — table names vary by warehouse.

## Canonical facts (by concept)

- **payment-transaction fact** — one row per Shopify transaction; carries `payment_method`, `payment_method_group`, a `payment_identifier` (processor charge id / provider auth code / BNPL payment id), and a USD conversion.
- **order payment rollup** — order-level payment summary, split by transaction type (`sales_order` vs `refund`), with a payment-method list, amount paid, and broken-out store-credit / gift-card / other amounts.
- **physical-return fact** — one row per returned sale line (deduped to the latest shelved date), enriched with the returns portal, order tags, and a reason mapping. Home of the return-reason coalesce.
- **analytics return line** — the consumption return line: type/subtype, refund method, refunded-payment allocation, NMV return measures. Often has no clean PK (documented dup cases).
- **exchange linkage** — original↔exchange order pairs (see below).

## Business rules (precise)

### Refund transactions vs refund line items — two independent sub-objects

Shopify models these separately and they are never joined to each other: money moves come from refund transactions (`kind='refund'`, non-failed); merchandise returned comes from the physical-return stream (an ERP), not from `refund_line_items`; order adjustments (shipping refund / discrepancy) are a third, separately-unnested sub-object. A store credit or restock can move inventory with no money transaction, and vice versa.

### Store credit vs gift card

Gift-card-gateway transactions are split by joining to the wallet system: a `WALLET` entity → Store Credit, a `GIFT_CARD` entity → Gift Card. Two fallbacks when the wallet data lags: an order tag like “using store credit” → Store Credit (same-day), else Gift Card. Refund-to-store-credit is detected by an order tag. In the payment rollup these amounts are broken out with a sign flip.

### Exchange detection — three Shopify mechanisms

1. **Retail/POS exchanges (ExchangeV2):** POS exchanges live on the same order. Exchanged line items are re-keyed to a synthetic `#<order>-E{n}` order name (with `exchange_number = dense_rank()` per order), keeping the original name. Any order key like `%-E%` is an exchange order.
2. **Warranty replacements:** orders tagged as a returns-portal “warranty replacement order”; the original is found by regex on the order note (`Replacement order for #...`).
3. **Process-error replacements:** orders with a “process error replacement” payment method; original via regex on the note. Plus legacy migrated exchanges, keyed by a migrated-original-order reference.

These fold into a **two-label long table**: `was_exchanged` (keyed by original order, with the exchanged-orders array and summed exchanged sale value — for POS, only the added value; otherwise the full new-order sale) and `is_an_exchange` (keyed by exchange order). Always filter by label when joining. Note: a split-tender/sessionization exchange heuristic (same-day multi-payment-method) is not in the warehouse — it's a reconciliation-workspace prototype.

### Exchange orders and payments

Exchange orders have no real tender: the payment fact sets a null identifier and a `Replacement` method; the rollup appends synthetic `Exchange` / `Warranty Replacement` rows with null amounts and dates (a known gap that understates exchange-order amounts).

### Sale-line exchange subtypes

`line_sub_type = sale.exchange.warranty` if a warranty tag or a DEFECTIVE return reason or a damage/replacement tag is present; `sale.exchange.product` for other exchanges/replacements; else `sale`. Warranty-replacement 100%-discounts are removed from promo — they are a returns-portal artifact that would otherwise unbalance the replacement P&L.

### Full return

An order is a full return when every sale line's returned qty ≥ sold qty.

## Classification logic

### Return reason — a multi-source coalesce

The winning reason is a `coalesce` over several vocabularies in priority order:

```text
coalesce(
  1. ERP QC reason on the return shipment
  2. order tags (return-to-stock / warranty / defect tag CASE)
  3. returns-portal merchant tags
  4. returns-portal warranty tags
  5. ERP sale-line voice-of-customer reason
  6. returns-portal returns reason
  7. returns-portal warranty-portal reason
  8. Shopify-native return_reasons object
  'No Return Reason'
)
```

A voice-of-customer variant reorders the ERP customer reason first. Winning reasons map to `parent` / `sub_parent` / `defect_non_defect` levels. Override example: a “defective or damaged” portal reason + a SELLABLE QC tag → forced to “Changed My Mind” / “Non-Defect”. Tag dedup keeps `Product Defect - *` first and `SELLABLE` last via an alphabetical-prefix ordering trick.

> **Shopify-native reduction (doc 11):** only positions 2 and 8 are Shopify-native (order tags + the `return_reasons` object). The QC/warranty positions require the ERP and returns portal. A Shopify-only agent computes the reduced coalesce and states the gap.

### `refund_method_name` precedence (order matters)

1. resolution `store_credit` and no Shopify refund payments → **Store Credit**
2. resolution `replace_item` → **Replacement**
3. resolution `other` → **Store Credit**
4. resolution `original_payment` with neither a legacy refund method nor a Shopify refund list → fall back to the sale-side payment method
5. order tag “refunded via store credit” → **Store Credit**
6. order is a POS/warranty exchange order → **Exchange**
7. order `was_exchanged` → **Exchange**
8. else `coalesce(legacy refund method, Shopify refund payment list, 'Unknown')`

### Return type/subtype

`Warranty Return` if warranty status approved/completed or reason type = Defect; else In/Out-of-Policy by a return-window flag (typical: 30–38 days from ship, with a holiday extension where Nov/Dec orders are returnable through end-January). Subtype ∈ `return.exchange.warranty` / `return.exchange.product` / `return.warranty` / `return.product`. NMV return measures only count non-repeat returns.

## Reconciliation patterns

- **Rev-rec feed:** a downstream feed keys refunds at `refund_id = order_key` (one refund per order), unioning (a) physical returns and (b) canceled paid orders with no physical return (a negative payment / refund transaction exists).
- **Payment-identifier chains:** BNPL captures (e.g. installment providers) resolve their identifier from the parent authorization transaction; wallet-style providers use a receipt transaction; card providers use the processor charge id from the receipt JSON. These identifier mappings are the only provider artifacts in the warehouse — provider-settlement reconciliation is done outside it (query-per-source → CSV → join-in-Python).
- **Refund $ allocation to lines:** order-level refunded cash is distributed across returned lines pro-rata to sale value; if no payment is found, it falls back to sale+discount+tax of the returned line.

## Gotchas

- Order-level payment rollups are 2 rows per order (`sales_order` + `refund`) — always filter transaction type, or joins fan out.
- The exchange long table is 2 rows per order when re-exchanged (both labels) — always filter label.
- Warranty/Exchange synthetic payment rows carry null amounts and dates, so `order_amount_paid` understates exchange orders.
- The physical-return fact dedups multiple return shipments per sale line to the latest shelved date; a multi-return flag marks items returned in greater qty than sold. Carrier/packaging pseudo-SKUs, invalid references, known-scam returns, and duplicated migration orders are excluded.
- Returns portal: rejected authorizations excluded; a warranty pipeline may have dead periods; the Shopify-native `return_reasons` object may pick only the first reason per order (a few-percent multi-return discrepancy).
- FX is always the prior-month rate; tax-inclusive shops have exchange-line sales tax-stripped.
- All timestamps normalized to the warehouse timezone.

## Source objects / APIs

- **Shopify:** transactions (with receipt JSON), refunds (refund line items / order adjustments / transactions JSON), orders (tags/notes drive exchange & store-credit detection), exchanges (ExchangeV2), `return_reasons`, `return_exchanges`, line items + properties.
- **ERP:** customer-return shipments (physical returns, QC reason, locations, cost), sale lines (return-type lines, VoC reason), stock moves/warehouses.
- **Returns portal:** returns, warranty, merchant tags, warranty tags (multiple tag vocabularies).
- **Wallet system:** transactions (`WALLET` vs `GIFT_CARD`).
- **Legacy platform:** payments, payment methods, return authorizations.
- **Config sheets:** return-reason mapping (reason → parent/sub_parent/defect).
- **FX source:** exchange rates.
