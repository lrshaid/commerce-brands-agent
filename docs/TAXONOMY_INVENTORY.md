# Shopify Product Taxonomy Inventory — store "habibi"

Shop: `gid://shopify/Shop/12345794` · Extraction: `hbny-orders-2025-v2-20260913T053517Z` (only extraction in `raw_shopify.orders`, verified). Research-only snapshot; all numbers from SELECT-only queries on `commerce-agents-dev.raw_shopify.orders` run 2026-09-13.

Base grain: one row per bulk record. `JSON_EXTRACT_SCALAR(payload,"$.__typename")` distribution:

| __typename | rows |
|---|---|
| LineItem | 28,974 |
| Order | 14,976 |
| ShippingLine | 13,812 |
| ScriptDiscountApplication | 8,234 |
| DiscountCodeApplication | 3,464 |
| ManualDiscountApplication | 2,307 |
| AutomaticDiscountApplication | 2,029 |

Total 73,796 rows. Line-level totals: 28,974 lines, $1,492,649.51 line GMV (sum of `discountedTotalSet.shopMoney.amount`), touching all 14,976 order gids. Note: no standalone `RefundLineItem` typename rows exist in `orders`; the 465 refunds are nested `refunds` objects inside Order payloads with **0** nested `refundLineItems` in this extraction (`raw_shopify.order_refunds` and `raw_shopify.products` are both empty).

```sql
SELECT JSON_EXTRACT_SCALAR(payload,"$.__typename") tn, COUNT(*) n
FROM `commerce-agents-dev.raw_shopify.orders` GROUP BY 1 ORDER BY n DESC
```

---

## 1. Vendor fragmentation

```sql
SELECT IFNULL(JSON_EXTRACT_SCALAR(payload,"$.vendor"),"[NULL]") vendor,
       COUNT(*) lines,
       COUNT(DISTINCT JSON_EXTRACT_SCALAR(payload,"$.product.id")) products
FROM `commerce-agents-dev.raw_shopify.orders`
WHERE JSON_EXTRACT_SCALAR(payload,"$.__typename")="LineItem" GROUP BY 1
```

| vendor | lines | distinct products | note |
|---|---|---|---|
| HABIBI | 17,947 | 133 | all lines in 2025 |
| Habibi NY | 8,349 | 100 | 2024–2025 |
| Habibi Parfums | 1,251 | 26 | 2018–2019 + 2024–2025 |
| Habibi New York | 996 | 20 | 2024–2025 |
| (empty string) | 377 | 0 | no product id attached |
| Habibi Lux Products | 29 | 4 | 2024–2025 |
| String & Thread | 13 | 8 | 2025 only |
| NULL | 12 | 0 | |

**Same product id sold under multiple vendors: 90 of 192.** The "vendors" are timestamped edits of one brand field, not real brands:

```sql
-- 90/192 product ids appear under 2+ vendor values
WITH li AS (SELECT JSON_EXTRACT_SCALAR(payload,"$.product.id") pid,
                   JSON_EXTRACT_SCALAR(payload,"$.vendor") vendor
            FROM `...orders` WHERE JSON_EXTRACT_SCALAR(payload,"$.__typename")="LineItem"
              AND JSON_EXTRACT_SCALAR(payload,"$.product.id") IS NOT NULL)
SELECT COUNTIF(pairs>1) pids_multiple_vendors, COUNT(DISTINCT pid) products
FROM (SELECT pid, COUNT(DISTINCT vendor) pairs FROM li GROUP BY 1)
```

Drift evidence (order joined via `__parentId`, min/max `createdAt` per pid+vendor): e.g. `Product/1421351125069` sold as "Habibi Parfums" in 2018 → "HABIBI" in 2025; `Product/6701599031373` "Habibi NY" 2024-11-30 → "HABIBI" 2025-12-18.

**String & Thread is NOT a separate brand.** Its 13 lines / 8 product ids are the same Habibi catalog: every one of its pids also sells under HABIBI (2,484 lines) / Habibi NY (477) / Habibi New York (230) / Habibi Parfums (42). Titles: Argan & Olive Oil - Shampoo & Conditioner, Urban Oud, Sahara Rose 10ml, Women's/Men's/Custom Sample Set, The Full Collection Sample Sets, Turkish Rose & Oud Body Lotion.

**Habibi Lux Products is also not a separate catalog**: its 29 lines / 4 pids (Beard Oil Set For Him, Body Lotion Gift Set For Her, Jasmine Oud & Body Lotion, Gift Card) all also sell under HABIBI.

**Proposed canonical mapping:**

| raw vendor | canonical | rule |
|---|---|---|
| HABIBI, Habibi NY, Habibi New York, Habibi Parfums, Habibi Lux Products | `habibi` | same product ids across spellings; 90/192 pids multi-vendor proves drift, not brand families |
| String & Thread | `habibi` | all 8 pids identical to HABIBI catalog products — flag for merchant confirmation (could be a legacy sub-brand name on the same catalog) |
| empty string / NULL | `habibi` (unclassified) | 389 lines, all custom-sale / ops lines without product ids (see below); vendor is unpopulated on manual items |
| — | `string_and_thread` | rejected unless merchant confirms |

The 377 empty + 12 NULL lines are ops/custom lines (top titles: Custom sale 16, "BLOOUS-74 Men's Little Freebies - Desert Oud Habibi NY" 13, Magnificent Rose 15, "Amazon shipment" 5, "Desert Oud for FBA" 3, "empty boxes for photo" 3, reed diffuser free gifts, candles) — GMV is real on several (Custom sale $2,227, Magnificent Rose $2,210) so they can't be dropped silently; they need a vendor backfill rule.

---

## 2. SKU taxonomy

```sql
SELECT CASE WHEN sku IS NULL THEN 'null' WHEN sku='' THEN 'empty_string'
            WHEN REGEXP_CONTAINS(sku,r'^[0-9]{12}$') THEN 'upc_12digit'
            WHEN REGEXP_CONTAINS(sku,r'^00[0-9]{12}$') THEN 'upc_14digit_00pad'
            ... END fmt, COUNT(*) lines, COUNT(DISTINCT sku) n_skus,
       ROUND(SUM(CAST(JSON_EXTRACT_SCALAR(payload,"$.discountedTotalSet.shopMoney.amount") AS FLOAT64)),2) gmv
FROM `...orders` WHERE JSON_EXTRACT_SCALAR(payload,"$.__typename")="LineItem" GROUP BY 1
```

80 distinct non-blank SKUs (+1 empty-string value = 81 total). Format distribution:

| format | lines | n_skus | GMV | examples (titles) |
|---|---|---|---|---|
| UPC 12-digit (`850027035512`) | 12,785 | 54 | $1,068,977.37 | AMBRE OF THE SEA Extrait, Cashmere Oud, 141 titles |
| UPC-13 padded to 14 with `00` (`00850027035079`) | 1,986 | 3 | (in legacy bucket below) | White Moroccan Leather (910 lines), Men's & Oud Sample Sets (625), Honeyed Tobacco & Oud (451) |
| Legacy alphanumerics (`DesertOudSampleVial`, `AmbreOfTheSeaVial`, `PalaceGroveVial`, `PenthouseSuedeVial`, `RareWoodsElixirVial`) | 1,410 | 5 | — | RESERVE vials + "Desert Oud" title |
| Set/bundle codes | 217+32 | 14 | $20,632 + $10,108 | COMBODS (Men's & Women's Sample Sets), BOSET (Beard Oil Set For Him), `bundle-jasmineoud` (Jasmine Oud & Body Lotion), SWC-1/SWC-2 (Sweet Confessions sets), SRA-1/SRA-2 (Sahara Rose Absolute sets), USL-2/3/4 (Unspoken Love sets), `selfcarewoman`/`menselfcare` (Self Care Set for Her/Him), `DesertOud&JasmineOudSet`, `OUD Fragrance Set`, `RoseGardenSet`, `WhiteMorandVelvetRoyaleSet` |
| Oddball numeric | 283 | 2 | — | `86855200039` (11 digits; Turkish Rose & Oud Body Lotion, 228 lines), `51` (Scented Candle, 55 lines) |
| Empty string | 10,927 | 1 | $114,391.99 | Sample Bundle (1,378), Sample Vials (1,000+ each), Custom Sample Set (723) |
| NULL | 1,429 | 0 | $47,102.90 | 265 distinct titles: "10ml", "15 perfumes", custom sales, gift-card-adjacent ops lines |

**Legacy ↔ UPC overlap for the same title** (same title string, multiple sku values):

```sql
SELECT JSON_EXTRACT_SCALAR(payload,"$.title") title, IFNULL(JSON_EXTRACT_SCALAR(payload,"$.sku"),"[blank]") sku, COUNT(*) lines
FROM `...orders` WHERE JSON_EXTRACT_SCALAR(payload,"$.__typename")="LineItem"
AND title IN ('Desert Oud','Desert Oud Sample Vial','Sample Bundle','Cashmere Oud') GROUP BY 1,2
```

| title | sku values (lines) |
|---|---|
| Desert Oud (full bottle) | `868552000318` (759), NULL (16), `DesertOudSampleVial` (3) |
| Desert Oud Sample Vial | `DesertOudSampleVial` (363), empty (765) |
| Sample Bundle | empty (1,378), `850027035505` (184) |
| Cashmere Oud | `850027035512` (373), empty (1) |
| Iris Bloom Sample Vial | empty (217), `850027035116` (119) |
| Velvet Royale Sample Vial | `850027035123` (188), empty (42) |

Lines with no SKU (12,356 = 42.6% of all lines) are dominated by sample vials and the Sample Bundle — i.e., the highest-frequency catalog items have no stable key; title is currently the only join surface.

---

## 3. Product-family clusters from titles

192 distinct product ids; product-id counts across clusters sum to 203 because some pids carry titles matching more than one pattern (e.g., a "set" title and a "bundle" title).

```sql
SELECT CASE WHEN isGiftCard OR title LIKE '%Gift Card%' THEN 'gift_card'
            WHEN REGEXP_CONTAINS(title,r'Sample Vial|Vial') THEN 'sample_vial'
            WHEN REGEXP_CONTAINS(title,r'Sample Set|Discovery') THEN 'sample_set'
            WHEN REGEXP_CONTAINS(title,r'Set|Bundle|Pair|Collection') THEN 'bundle_set'
            WHEN REGEXP_CONTAINS(title,r'Candle|Lotion|Oil|Diffuser|Shampoo|Conditioner|Body|Face|Skin|Hair') THEN 'selfcare_home'
            ELSE 'other_full_bottle_like' END cluster,
       COUNT(*) lines, COUNT(DISTINCT product.id) n_products,
       ROUND(SUM(CAST(discountedTotalSet.shopMoney.amount AS FLOAT64)),2) gmv
FROM `...orders` WHERE __typename='LineItem' GROUP BY 1 ORDER BY gmv DESC
```

| cluster | lines | products | GMV | share |
|---|---|---|---|---|
| full_bottle-like (no pattern keyword) | 8,471 | 95 | $1,069,980.98 | 71.7% |
| sample_set | 6,333 | 13 | $248,915.39 | 16.7% |
| bundle_set | 2,005 | 28 | $107,573.68 | 7.2% |
| selfcare_home | 1,614 | 29 | $53,555.52 | 3.6% |
| sample_vial | 10,530 | 37 | $9,919.44 | 0.7% |
| gift_card | 21 | 1 | $2,704.50 | 0.2% |

Top full-bottle titles: White Moroccan Leather $119,073 / Desert Oud $100,833 / Ambre of the Sea $79,556 / Honeyed Tobacco & Oud $56,213 / Jasmine Oud $50,664 / Cashmere Oud $49,603 / Palace Grove $49,471 / Urban Oud $43,360 / Rose Amor Eau de Parfum $42,592 / Sweet Confessions $42,164. The bucket also contains 10ml travel sizes ("Sweet Confession 10ml" 225 lines, "Unspoken Love 10ml" 128) and ICNA/POS-suffixed titles ("AMBRE OF THE SEA Extrait - ICNA" 16 lines / $3,600) — classification rule for `full_bottle` must not be purely keyword-negative; see §7.

Sample vials: 10,530 lines, 37 pids, only $9,919 GMV — the acquisition funnel (also the main free-gift vehicle, §5).

---

## 4. Gift cards

```sql
SELECT title, variantTitle, COUNT(*) lines,
       ROUND(SUM(CAST(discountedTotalSet.shopMoney.amount AS FLOAT64)),2) gmv
FROM `...orders` WHERE JSON_EXTRACT_SCALAR(payload,"$.isGiftCard")='true' GROUP BY 1,2
```

- 21 lines, 1 product (`gid://shopify/Product/4461848789069`), GMV $2,704.50, 14,976-order window.
- Single title "Gift Card"; 7 variant titles ($50/$100/$150/$200 denominations, some written "$100.00 USD / $200", "$200 / 200").
- SKU: **blank on every line**. `fulfillmentService.type` = GIFT_CARD distinguishes them structurally.

---

## 5. Bundle / add-on signals

customAttributes key distribution (rows across LineItems):

```sql
SELECT JSON_EXTRACT_SCALAR(ca,"$.key") k, COUNT(*) n
FROM `...orders`, UNNEST(JSON_EXTRACT_ARRAY(payload,"$.customAttributes")) ca
WHERE JSON_EXTRACT_SCALAR(payload,"$.__typename")="LineItem" GROUP BY 1 ORDER BY n DESC
```

| key | rows | values (examples) |
|---|---|---|
| `Product` | 7,027 | "Free Gift" (6,978 lines — sample vials at $0–$18 GMV), "20% off" (28), "15% off" (15), "10% off" (5), "5% off" (1) |
| `_sub products` | 6,977 | bundle-subcomponent list (Rebundle-style) |
| `_bundle products` | 5,115 | `bundle-2696390488`, `bundle-3017911624`, … (bundle-definition ids, ≤4 lines each) |
| `_attribution` / `_source` | 1,769 / 1,769 | `_source` = "Rebuy" on all 1,769 → Rebuy add-on engine lines |
| `_tier` | 1,250 | 5 Rebuy tier uuids (top tier 970 lines) |
| `Product 1..5` | 1,089 each | custom sample-set builder slots ("Product 1"…"Product 5") |
| `samples` | 483 | — |
| `_widget_id` | 519 | Rebuy widget ids (181284, 226996, 211370, …) |
| `__as_offer_id` | 244 | AfterSell offer uuids |
| `_shopify_item_type` | 132 | "custom_sale" |
| `Discount` | 73 | "28%" |
| `Gift Purchase` | 60 | Yes 50 / No 10 |
| `Signature Fragrance` / `Home Aroma` / `Body Care` | 14 each | scent quiz / bundle picker attributes (Desert Oud, Reed Diffusers, Body Lotion…) |
| `Shopify Collective Retailer Line Item ID` | 13 | Collective resale lines (13 lines under sourceName `shopify-collective-automatic-payments`) |

fulfillmentService (JSON object per line, not array): MANUAL 28,953 lines ($1,489,945.01), GIFT_CARD 21 lines ($2,704.50) — no 3PL/other service types. `isGiftCard` true on exactly the 21 gift-card lines.

Signals usable for taxonomy: `_bundle products`/`_sub products` = bundle membership; `Product 1..5` = built-a-box sample sets; `_source=Rebuy` + `Product=Free Gift` = free vial add-ons (should map to sample_vial, not separate units).

---

## 6. Channels (sourceName on Order)

```sql
SELECT IFNULL(JSON_EXTRACT_SCALAR(payload,"$.sourceName"),'[null]') src, COUNT(*) orders,
       ROUND(SUM(CAST(JSON_EXTRACT_SCALAR(payload,"$.totalPriceSet.shopMoney.amount") AS FLOAT64)),2) total_price
FROM `...orders` WHERE JSON_EXTRACT_SCALAR(payload,"$.__typename")='Order' GROUP BY 1 ORDER BY 2 DESC
```

| sourceName | orders | totalPrice $ | identified as |
|---|---|---|---|
| web | 11,656 | 1,074,911.47 | online store |
| tiktok | 973 | 38,731.89 | TikTok Shop |
| shopify_draft_order | 701 | 26,845.17 | draft orders |
| pos | 576 | 73,309.12 | retail POS |
| `2329312` | 318 | 34,386.25 | numeric app id; AfterSell Checkout tags on some (4); unknown app otherwise |
| `3890849` | 302 | 35,900.80 | **Shop app**: 163 orders tagged "Acquisition, Shop Cash offers acquired" |
| subscription_contract | 147 | 3,106.61 | subscriptions |
| `88312` | 123 | 4,675.55 | numeric app id; spans 2018→2025; "AfterSell Checkout" on 6 |
| subscription_contract_checkout_one | 47 | 1,699.20 | Checkout One subscriptions |
| checkout_next | 42 | 2,457.47 | legacy 2018–2024 |
| `1424624` | 33 | 0.04 | numeric; "AfterSell Upsell" on 1; $0.04 total → test/gift lines |
| `75919917057` | 17 | 0.00 | numeric; all tagged "Insense" (UGC/affiliate app) |
| `2775569` | 14 | 0.00 | numeric; all tagged "Collab Gift" |
| `580111` | 13 | 367.90 | numeric, only 2019; unknown |
| shopify-collective-automatic-payments | 13 | 448.85 | Shopify Collective |
| `3624803` | 1 | 0.00 | numeric; tag "Affiliate gift - Social Snowball" |

`registeredSourceUrl` and `sourceIdentifier` are **null on every sampled numeric-sourceName order**, so identification relies on tags: the evidence (Shop Cash, Insense, Collab Gift, Social Snowball, AfterSell) says numeric sourceName = app id of the app that created the order, but the mapping per id is **unknown, needs confirmation** (pull app ids from the merchant's installed-apps list).

---

## 7. Taxonomy gaps & proposed `cfg_product_taxonomy` mapping

**Cannot be derived without the products stream** (`raw_shopify.products` is empty):
- `productType`, `tags`, `collections` (taxonomy buckets and collection membership)
- canonical title (line `title`/`name` drift over time)
- product status / archived, real variant metadata (option names, images)
- the 90-pids-multi-vendor canonical `vendor` value (would come from products if normalized)

A `cfg_product_taxonomy` mapping keyed on product id (fallback: title/variant) needs: `shop_key`, `product_gid`, `variant_gid`, `canonical_title`, `canonical_vendor`, `unit_style_type`, `rule_priority`, `tie_policy` (per the open `merchandise_taxonomy` decision).

Candidate `unit_style_type` buckets and their derivation rules from available fields (rule priority order as listed):

| unit_style_type | mapping rule (needs) |
|---|---|
| `gift_card` | `fulfillmentService.type='GIFT_CARD'` OR `isGiftCard='true'` — structural, deterministic (21 lines) |
| `sample_vial` | title contains `Sample Vial`/`Vial`; also catch lines with `customAttributes Product='Free Gift'` and legacy SKUs `*Vial` (DesertOudSampleVial, AmbreOfTheSeaVial…) — 10,530 lines |
| `sample_set` | title contains `Sample Set`/`Discovery (Set)` OR SKU prefix COMBODS or `Product 1..5` customAttributes — 6,333 lines |
| `bundle` | `_bundle products` / `_sub products` attributes OR SKU BOSET/bundle-*/SWC/SRA/USL or title contains Set/Bundle/Pair/Collection — 2,005 lines |
| `selfcare` | title contains Candle/Lotion/Oil/Diffuser/Shampoo/Conditioner/Body/Face/Skin/Hair (and not matching earlier rules) — 1,614 lines |
| `full_bottle` | everything else with a real product id (default bucket) — 8,471 lines; includes 10ml travel sizes that may warrant their own `travel_size` split later |
| `other` | no product id (custom sales, ops lines, 389 lines) — exclude from product metrics |

Tie policy suggestion: first match wins in the order above (gift_card → sample_vial → sample_set → bundle → selfcare → full_bottle → other), since vial/set patterns are high-precision keywords and `full_bottle` is the residual. Ambiguities to resolve with the merchant: (a) do 10ml travel sizes stay in `full_bottle` or split out; (b) "String & Thread" final call; (c) whether ICNA/POS-suffixed titles are wholesale re-skins of the same products.

**Open gap:** without `products`, `productType`/tags/collections cannot backfill — recommend enabling the products stream in the extraction before finalizing `cfg_product_taxonomy`; the title/keyword rules above are the interim mapping.
