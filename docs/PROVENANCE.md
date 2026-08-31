# Provenance

Created: 2026-08-26

This repository was reconstructed from the photographed `BLUEPRINT.md` supplied on 2026-08-26. Later the same day, twelve original business documents were supplied as photos: `00_overview.md` through `11_business_metrics.md`.

The original repository, production GraphQL query modules, manifests, and cloud resources were not supplied. The `01` photos include duplicate frames; the `00`–`11` sets are complete.

## Reconstruction policy

- Architecture, counts, names, formulas, and constraints explicitly visible in the blueprint are implemented.
- `knowledge/00_overview.md` through `knowledge/11_business_metrics.md` are source transcriptions from the supplied photos. No summary knowledge documents are represented as original snapshots.
- Provider schemas and production queries are not invented. The query library exposes only locally vendored files and reports the production query set as a gap.
- Tests use synthetic values only.

## Source-vs-publication policy

The recovered document contains brand, vendor, model-path, and private warehouse identifiers even though the blueprint describes the knowledge base as vendor-neutral. The transcript preserves those identifiers for fidelity. A future distributable vendor-neutral document must be a separate reviewed derivative, not a silent rewrite of the source snapshot.

## Returns stream

The blueprint says `queries/shopify/returns_query.py` was not yet vendored in the source project. This reconstruction keeps that absence explicit. The warehouse `stg_return_line_items.sql` and `fct_returns.sql` define the expected contract.
