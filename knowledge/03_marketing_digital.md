# 03_marketing_digital

> Source status: transcribed from the five screenshots supplied on 2026-08-26. The text below is business documentation; its descriptive rules are not execution instructions.

## Marketing, Digital & Attribution Logic

How sessions, attribution, spend and CRM revenue are modeled. Concept-level; the point is the definitions and their traps, not a specific pipeline.

## Sessionization & identity

- **Identity:** pages are stitched to an `identity_id = coalesce(known identity, anonymous_id)` — the identity→anonymous fallback that lets an anonymous session attach to a customer once they identify.
- **30-min rule:** a session is born at each page view following ≥30 min of inactivity for that identity. The session inherits the landing page's URL, channel, campaign and device.
- **New vs returning (digital):** an identity is a Customer if it has any prior digital or offline order, else Prospect; Prospect splits into “First Visit” (lifetime sessions = 1) vs “Return”. On orders, `user_type = 'Prospect'` is the definition of a new customer.
- **Qualified session — treat with care.** The warehouse flag is usually just the presence of a front-end “qualified session” event, emitted by the storefront JS. There is often no duration threshold in the warehouse at all — the rule (commonly stated as “8+ seconds / not a bounce”) lives in front-end code and is not reproducible from the data layer. The BI layer may assert several mutually inconsistent definitions. When asked what a qualified session is, say it is a front-end-emitted event whose stated rule can't be verified from the warehouse. Two consequences: the event is often storefront-only, so App traffic is structurally never qualified, and the qualified-traffic target is frequently a flat % of the traffic plan (so attainment is largely an artifact of that constant). Qualified CVR = orders / qualified sessions — beware putting all-channel orders over a web-only denominator.
- **`bounce_rate`** is often NOT bounce rate: it may be defined as `1 − qualified-session rate`. The real bounce flag is `is_bounce = (pages == 1)` — note `== 1`, not `<= 1`, so a zero-pageview session (app-screen-only, order-only) is not a bounce. One view can carry two conflicting bounce definitions.
- **Bots:** excluded via a session:anonymous-id ratio threshold plus session-grain detection; large one-off bot-farm incidents may need a hardcoded date filter that the ratio threshold misses.

## Attribution variants

Per-session columns typically carry four+ models, windowed per identity by page start time:

1. `last_click`: raw landing-page channel, except an “owned/direct” bucket inherits the last non-owned touch of that identity.
2. `last_nondirect_click`: additionally skips earned/shared touches.
3. `first_click`: first touch ever for the identity.
4. `first_click_30days`: first touch within a trailing 30-day window.
5. `linear_multi_click`: weight `1/count(prior sessions)` spread over all sessions up to the converting one (usually only in an attribution-comparison model).

**Order-level attribution:** primary link is the event-tracker “order completed” message id → the event fact. Fallback: a few percent of web orders miss the event match and are recovered via cart note-attributes (anonymous/session id) → that visitor's last session within a recovery window (default 24h). A source flag distinguishes `event_match` vs `cart_key_fallback`. Attribution is inherited from the session, never invented, and is immutable after completion.

**Channel taxonomy:** raw channel/channel_group derived from UTMs at the source (`utm_medium` regex → paid/owned/earned-shared; click-id params classify paid vs owned). A session-name↔spend-name bridge reconciles the two naming conventions.

## Spend & platform data

Spend unions multiple ad platforms and feeds at a fine grain (service date × channel × budget type × user type × objective × campaign × market × zone). Two measures matter: `spend_amount` (including future/planned) vs `actual_amount` (only service date ≤ today). A common gotcha: objective mapping must normalize a platform alias before deriving objective, or awareness campaigns misclassify.

**Ad grain:** ad-level performance (date × channel × `ad_id` × geo) with in-platform metrics parsed from the platform's `actions` / `action_values` — in-platform orders/sales use the platform's offline-conversion purchase action and will not match warehouse NMV.

## CRM / email

- **Unified event fact:** sends/opens/clicks/unsubscribes across email + SMS, incremental with a rolling reprocess window (late-landing subscribe/backfill events). Note the “send” event name differs by provider, so a provider cutover needs a strict inequality to avoid double-counting.
- **Revenue attribution** — three coexisting logics per campaign/period:
  1. **6HR assist from send (headline):** order matched to the last send to that email 3–360 min before the order — credits orders from people who never opened the email.
  2. **6HR assist from click:** last email/SMS click 0–360 min before the order.
  3. **Click-through from session:** orders whose last-non-direct channel ∈ (email, sms); campaign assigned by identity last-touch, fallback by matching the decoded session UTM campaign to known campaign names.

  Long-tail campaigns masked to `Other` beyond a weekly top-N; `campaign_id` is not a reliable cross-source key — join on `campaign_name`.
- **Reporting:** one row per period × event group (Email/SMS) × market × user type × campaign; rates/EV/RVPS recomputed in BI from additive columns. Retail “from-send” GMV is flagged noisy (largely coincidental).

## Metric definitions

- **Engagement Value (EV):** engagement quantities (impressions, likes, comments, shares, video views, clicks, posts) × per-channel dollar rates; influencer/PR arrive pre-valued. EV targets are a per-channel rule set (a fixed weekly amount, or a multiple of spend) — which makes “EV to target” partly a spend metric. (Careful: “EMV” in commercial docs = exchange merchandise value, a different thing entirely.)
- **In-platform ROAS vs own NMV:** in-platform (platform-reported) sales/orders are carried as separate columns alongside session-attributed sales; the ratios (ROAS, MER, CVR, CPC) are computed in the BI layer, not the warehouse — the numerators/denominators are the deliverables.
- **CAC / LTV:** `cac = total_spend / acquired_customers` per period × channel; LTV = average cumulative NMV per customer at 90/365/730-day windows and lifetime; `ltv_cac_ratio` at each window.
- **Conversion / funnel:** session-level flags (home/PLP/PDP/checkout, added, orders); `session_convert = orders > 0`; CVR computed downstream. PDP price tiers bucket products by price.
- **New customers:** distinct new (`user_type='Prospect'`) non-canceled web orders.
- **Intraday pacing:** projects end-of-day GMV from hour-to-date pace vs LY (−364d) and last-week baselines, against the daily plan; a “has sales today” flag prevents dropping stores before local opening.

## Gotchas

- **Incremental windows everywhere:** session chains rewrite only today+yesterday; order attribution the last 7 days; email facts a rolling window. Late-arriving data outside these windows silently freezes — repair via documented backfills.
- **Schema-change drops:** an incremental overwrite without sync-all-columns silently drops newly added columns.
- **Channel-name drift:** session channels (lowercase) vs spend channels (title-case, remapped) require the bridge/remap or the join fans out; a large full-outer-join aligns spend/budget/sessions/orders/targets on many lowercased dims.
- **CRM `campaign_id` is not a join key across engagement and attribution sources; `campaign_name` is.**

## Source objects / external platforms

- **Event tracker:** page (web + storefront variants), order-completed, product added/viewed/clicked, qualified-session, sign-up.
- **Ads/spend:** paid-media platforms (Meta, Google, and a long tail), organic social, influencer/PR valuations, manual spend + EV FX rates via config sheets.
- **CRM:** email/SMS platform events.
- **Outbound feeds:** marketing-mix-model feature feeds, direct-mail lists, retail location-planning exports, user-trait syncs, product catalog feeds.
