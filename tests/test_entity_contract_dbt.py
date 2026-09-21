from pathlib import Path
import re

import yaml

from agent.warehouse.entity_contract import load_entity_contract


ROOT = Path(__file__).resolve().parents[1]
DBT_SCHEMA = ROOT / "dbt/models/staging/shopify/schema.yml"


def _normalized(value):
    return re.sub(r"\s+", "", value.lower()).replace("bool", "boolean")


def _dbt_type(column):
    scalar = {
        "STRING": "string", "INT64": "int64", "NUMERIC": "numeric",
        "BOOL": "boolean", "TIMESTAMP": "timestamp", "JSON": "json",
    }
    if column.type == "RECORD":
        value = "struct<" + ",".join(
            f"{field.name} {_dbt_type(field)}" for field in column.fields
        ) + ">"
    else:
        value = scalar[column.type]
    return f"array<{value}>" if column.mode == "REPEATED" else value


def test_executable_contract_and_dbt_contract_cannot_drift():
    contracts = load_entity_contract()
    schema = yaml.safe_load(DBT_SCHEMA.read_text())
    models = {model["name"]: model for model in schema["models"]}
    for entity, contract in contracts.entities.items():
        model = models[f"stg_shopify__{entity}"]
        dbt_columns = {column["name"]: column["data_type"] for column in model["columns"]}
        expected = {column.name: _dbt_type(column) for column in contract.columns}
        assert set(dbt_columns) == set(expected)
        assert {_normalized(name): _normalized(value) for name, value in dbt_columns.items()} == {
            _normalized(name): _normalized(value) for name, value in expected.items()
        }
