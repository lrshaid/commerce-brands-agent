# Warehouse configuration inputs

No target business configuration is supplied in this repository. `warehouse.template.yaml`
is deliberately null-valued and is never auto-loaded. Historical knowledge and `.env.example`
are not target config. No defaults are applied unless the **boolean**
`warehouse.allow_defaults: true` is explicitly provided.

The offline loader reads these user-owned, gitignored inputs:

- `config/warehouse.yaml`: scalar config conforming to `schema.yaml`.
- `config/tables.yaml`: optional local snapshot of user-supplied `cfg` rows, shaped as
  `tables: {cfg_table_name: [rows]}`. It does not substitute for deploying/loading `cfg`.
  Missing local input means remote existence **NOT_CHECKED**, not that a remote table is absent.
- `config/raw_contracts.yaml`: approved entity-to-raw mappings, described below.

Do not commit target identifiers or config rows. Use `--config-dir` for an input directory
outside the repository. No loader reads credentials, queries a source, or writes a dataset.

```sh
python3 -m agent.warehouse check --format markdown
python3 -m agent.warehouse --config-dir /path/to/private/config check --as-of-date YYYY-MM-DD
python3 -m agent.warehouse cfg-ddl
```

Exit code 2 means incomplete or invalid. The first two commands report missing prerequisites
per model and propagate upstream blockers. Even with all inputs supplied, `not_started`
models do not become implemented. DDL output is schema-only, contains no rows, and is never executed.

`tables.schema.yaml` preserves the ten specified table shapes. It also lists six table
names referenced later in the request whose schemas were not supplied. Uniqueness of
`cfg_metric_targets` includes `metric`, since a plan row otherwise collides with the next
metric at the same date/key/version. Mapping priorities, shop scope, unit-style taxonomy
and daily FX remain explicit unresolved contracts, not silently added columns.

Validation checks YAML duplicate keys, scalar types/enums/bounds, conditional config,
table columns/types/enums, and declared primary-key uniqueness. It **does not** prove
fiscal continuity, FX coverage, rule coverage/non-overlap, currency correctness, or remote
freshness. Those require approved contracts and downstream data-quality tests.

## Raw entity mappings

The renderer supports an explicitly approved **current-state JSON payload envelope** only.
Columnar or versioned raw landing requires a separate mapping/dedup adapter; it is rejected,
not guessed. The 33 source names are inventoried in `warehouse/contracts/raw_sources.yaml`.

`raw_contracts.yaml` has a top-level `entities` mapping keyed by the staging model name.
Each entity requires:

| Field | Contract |
|---|---|
| `source` | Declared raw object; must match the model inventory |
| `payload_column` | Raw JSON column name, explicitly supplied |
| `shop_key_column` | Raw shop key column name, explicitly supplied |
| `current_unique` | Must be true only after the owner confirms current-state uniqueness |
| `array_path` | Optional JSON dot path for one nested array; edges/node shape must be explicit |
| `primary_key` | Output column list including `shop_key` and entity/event identifier |
| `fields` | List of `{name, kind, path, root}`; root is `payload` or `entity` |

Kinds: `gid`, `string`, `enum`, `int64`, `numeric`, `timestamp`, `bool`, `json`.
GIDs produce an INT64 and a retained STRING `_gid` twin; enums are lowercased. No business
exclusions, signs, FX or dates are inferred. Missing paths can still yield NULL: generated
key/type assertions are necessary but not sufficient for contract completeness.

```sh
python3 -m agent.warehouse render-staging --name stg_orders
python3 -m agent.warehouse render-staging --name stg_orders --key-test
python3 -m agent.warehouse render-staging --name stg_orders --type-test
```

These commands emit SQL only. They never execute it or write it into a model directory.
No target staging SQL can currently be rendered: the required raw mappings are absent.
