# 08_variance_decomposition

## Variance Decomposition — Attributing a Metric's Change to Its Drivers

How to answer “NMV is down 17% YoY — why, and how much of it is each driver”. Implemented in `agent/analysis/`, exposed as the `nmv_decomposition_tree` and `decompose_custom_tree` tools.

## The kinds of node, and why the math differs

**Additive nodes** — the parent is a sum of its children (`NMV = GMV + EMV + RMV`). A child contributes its own delta, expressed in percentage points of the parent's change as `Δchild / parent_prior`. These sum exactly, with no choices to make.

**Multiplicative nodes** — the parent is a product (`GMV = Traffic × CVR × AOV`). The naive approach is wrong: if traffic is +10% and AOV is +10%, GMV is +21%, not +20% — the extra point is an interaction term that belongs to neither factor alone.

| Method | Behaviour |
|---|---|
| Sequential / chained | Vary one factor at a time. Reconciles exactly but is order-dependent — whichever factor is varied last collects the interaction. Two analysts get two answers. |
| LMDI-I (used here) | Distributes the interaction exactly and symmetrically. Zero residual, order-independent. `ΔP = Σᵢ L(P₁,P₀) × ln(fᵢ₁/fᵢ₀)`, where `L(a,b) = (a−b)/ln(a/b)` is the logarithmic mean. Sums to exactly `P₁−P₀`. |
| Shapley | Also exact and symmetric, but requires 2ⁿ evaluations; equals LMDI closely for small n. |

In the +10%/+10% example LMDI gives each factor +10.5pp — the interaction point is split evenly. LMDI requires strictly positive values on both sides. When a factor is zero or negative (or the parent crosses zero), the code falls back to the chained method and tags the node `sequential (order-dependent)` so the attribution is never read as canonical.

**Ratio nodes** — the parent is a quotient (`CVR = orders / traffic`, `ROAS = revenue / spend`, `return rate = RMV / GMV`). Division needs no separate machinery: `ln(a/b) = ln a − ln b`, so a dividing factor is a factor with exponent −1 and LMDI handles it unchanged, still summing exactly. Use `op="ratio"` (first child multiplies, the rest divide). A denominator that falls pushes the ratio up and gets a positive contribution.

**Mix nodes** — a blended rate is a weighted average of segment rates:

```text
blended CVR = total orders / total traffic = Σ wᵢ rᵢ,  wᵢ = trafficᵢ / total
```

A blended rate can move with no segment's rate moving at all — volume shifts toward a segment with a different rate. Treating it as `op="ratio"` hides that. Each segment splits into two midpoint-symmetric effects:

```text
rate effectᵢ = w̄ᵢ · Δrᵢ       (its own rate moved)
mix effectᵢ  = r̄ᵢ · Δwᵢ       (its share of volume moved)
```

with `w̄ = (w₁+w₀)/2`, `r̄ = (r₁+r₀)/2`. Per segment these sum exactly to `w₁r₁ − w₀r₀`; across segments they reproduce `ΔR` with no residual. Because `Σ Δwᵢ = 0`, mix effects are a pure reallocation. Use `op="mix"` with `segment(name, rate, rate_prior, volume, volume_prior)`.

This matters when channels convert at very different rates: Retail and Web are the classic case. Any shift in traffic share moves blended CVR on its own, with no channel's conversion changing. The canonical NMV tree avoids this by splitting channels before the `Traffic × CVR × AOV` product. Reach for a mix node for company-level blended rates.

Illustrative synthetic shape:

```text
blended CVR: 0.0220 vs 0.0231 prior = -4.69% (-0.001083)

driver                       YoY    contribution   of total
blended CVR > HighRate > mix +20.0%    +9.93pp       -212%
blended CVR > LowRate  > rate -16.7%    -7.87pp        168%
blended CVR > HighRate > rate -10.3%    -5.96pp        127%
blended CVR > LowRate  > mix  -1.8%    -0.79pp         17%
sum of all 4 leaves                  -4.69pp        100%
reconciliation: EXACT (no residual)
```

Both segments' rates fell, but volume shifted toward the higher-converting one, offsetting most of the decline. Always split rate from mix before concluding anything about a company-level rate.

## Chaining to the root

Contributions propagate multiplicatively down the tree: if GMV accounts for −19.3pp of NMV's change, and Web traffic accounts for 78% of Web GMV's change, then Web traffic's share of the root is the fraction of the parent's slice. The leaves of the whole tree must sum exactly to the headline percentage change; this is the reconciliation check.

## The canonical NMV tree

```text
NMV                              (sum)
├─ GMV                            (sum over sales_channel)
│  ├─ GMV Retail = Traffic × CVR × AOV   (product, LMDI)
│  └─ GMV Web    = Traffic × CVR × AOV   (product, LMDI)
├─ EMV (exchanges)                (sum over channel)
└─ RMV (returns, stored negative) (sum over channel)
```

Three constraints are baked into that shape:

1. RMV keeps its stored negative sign. The documented `NMV = GMV + EMV − RMV` only holds if RMV is read as a positive magnitude; warehouse columns give `NMV = GMV + EMV + RMV`. The tool rejects a positive RMV rather than silently double-adding returns.
2. Traffic is split per channel, never blended. `traffic` is web sessions on Web rows and door footfall on Retail rows; a blended traffic × CVR is meaningless. Channels combine additively above the product node.
3. Do not filter `user_type` when pulling retail traffic. Footfall is stored halved across Prospect and Customer keys; summing all rows restores the true door count, while filtering one returns an arbitrary half.

Optional 4-factor variant: `Traffic × CVR × UPT × APP`, where `UPT = units/orders` and `APP = GMV/units`. Units usually are not on the commercial-health grain and must come from the transaction-line ledger at the same grain.

This tree uses the commercial-health AOV `AOV = GMV / orders`; there are at least five different AOVs, so always say which.

## Getting the inputs

Pull the two sides from the commercial-health grain, which already carries `_ly` twins on fiscally aligned rows (−364 days, day-of-week aligned), so summing both sides over the same row set is comparison-correct with no self-join. Use a complete fiscal period; a partial period compares partial TY against full LY and reads as a collapse.

## Reading the result — diagnostics per node

- GMV miss → upstream problem (traffic, conversion, basket). RMV spike → post-purchase problem (product-market fit, sizing, gifting returns).
- Digital funnel: strong %Qualified but low %PLP → navigation/home; strong %PDP but low %ATC → product page; strong %ATC but low %Checkout → cart friction; strong %Checkout but low %CVR → checkout friction (payment, shipping cost).
- Retail: `Store Traffic = Run Traffic × Capture Rate`. Low run traffic → macro/location (not controllable); low capture → store-level (window, visual merch, door presence — controllable).
- Cross-pod: strong GMV + weak NMV → post-purchase leakage; traffic up but orders flat → conversion, not demand; orders up but NMV flat → return rate, exchange rate, or discount depth.
- Traffic-led decline with AOV up is a mix shift: fewer, larger baskets. Decompose AOV further (`UPT × APP`) or segment by `user_type` before concluding.
- CVR moving opposite to traffic often means the lost traffic was low-intent; if CVR falls with traffic, the problem is on-site, not upstream.

## Worked example (synthetic)

```text
NMV: 970,000 vs 890,000 prior = +8.99% (80,000)

driver                                  YoY    contribution   of total
NMV > GMV > GMV Web > AOV              +10.0%    +11.24pp       125%
NMV > GMV > GMV Web > CVR               -9.1%    -11.24pp      -125%
NMV > GMV > GMV Web > Traffic          +10.0%    +11.24pp       125%
NMV > RMV (returns, stored negative) > Web -20.0% -3.37pp       -37%
NMV > EMV (exchanges) > Web            +25.0%     +1.12pp        12%
sum of all 5 leaves                              +8.99pp       100%
reconciliation: EXACT (no residual)
```

How to read it:

- Contribution, not YoY, ranks drivers. A factor can be down a lot and barely matter (small base), or be flat and dominate. The contribution column sums to the headline; YoY is context.
- Negative “of total” means the driver pushed against the headline; CVR and returns fought the increase and are offsets, not errors.
- A parent can contribute more than 100% when a sibling offsets it. Check the reconciliation line; if it is not EXACT, an upstream identity is broken and the attribution should not be quoted.
