# Commerce Brands Agent

Local, read-only reconstruction of the Shopify-native ecommerce analytics agent described in `BLUEPRINT.md`.

## What runs today

- semantic catalog validation and join-path resolution;
- 28-metric purity catalog and 10-insight catalog;
- exact additive, multiplicative/LMDI, ratio, and mix decomposition;
- canonical NMV decomposition with stored-negative RMV enforcement;
- read-only provider connector boundaries;
- a local JSON-lines tool runtime;
- BigQuery staging and revenue-mart SQL templates;
- consistency and unit tests.

## Quick start

~~~bash
python3 -m unittest discover -s tests -v
python3 -m agent.main --list-tools
printf '{"tool":"metric_catalog","arguments":{}}\n' | python3 -m agent.main
~~~

The local tests require only Python 3.9+ and PyYAML. Live connectors additionally require the variables documented in `.env.example`.

## Safety

- Shopify GraphQL mutations are rejected before any network call.
- No credential values are logged or returned.
- No real brand data is committed.
- All warehouse SQL is parameterized with `{{project}}` and `{{dataset}}` placeholders.

See `GAPS.md` for what cannot be reconstructed from the supplied screenshots alone.

