"""Executable Shopify entity contracts shared by Parquet and BigQuery."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from google.cloud import bigquery
import pyarrow as pa
import yaml


DEFAULT_CONTRACT = (
    Path(__file__).resolve().parents[2] / "warehouse/contracts/shopify_entities_v1.yaml"
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SCALAR_ARROW_TYPES = {
    "STRING": pa.string(),
    "INT64": pa.int64(),
    "NUMERIC": pa.decimal128(38, 9),
    "BOOL": pa.bool_(),
    "TIMESTAMP": pa.timestamp("us", tz="UTC"),
    # BigQuery's Parquet loader receives JSON as a UTF-8 string in the temp
    # table. The generated MERGE performs PARSE_JSON into the target JSON field.
    "JSON": pa.string(),
}
_BQ_TYPES = set(_SCALAR_ARROW_TYPES) | {"RECORD"}


@dataclass(frozen=True)
class ColumnContract:
    name: str
    type: str
    mode: str = "NULLABLE"
    required: bool = False
    source: str | None = None
    generated: str | None = None
    fields: tuple["ColumnContract", ...] = ()

    @property
    def is_generated(self):
        return self.generated is not None

    def arrow_type(self):
        if self.type == "RECORD":
            value = pa.struct([pa.field(field.name, field.arrow_type(), nullable=not field.required)
                               for field in self.fields])
        else:
            value = _SCALAR_ARROW_TYPES[self.type]
        return pa.list_(value) if self.mode == "REPEATED" else value

    def bigquery_field(self, *, temporary=False):
        field_type = "STRING" if temporary and self.type == "JSON" else self.type
        children = [field.bigquery_field(temporary=temporary) for field in self.fields]
        mode = "REPEATED" if self.mode == "REPEATED" else (
            "REQUIRED" if self.required else "NULLABLE"
        )
        return bigquery.SchemaField(self.name, field_type, mode=mode, fields=children)


@dataclass(frozen=True)
class EntityContract:
    name: str
    source_stream: str
    key: tuple[str, ...]
    cluster_by: tuple[str, ...]
    columns: tuple[ColumnContract, ...]

    @property
    def source_columns(self):
        return tuple(column for column in self.columns if not column.is_generated)

    @property
    def generated_columns(self):
        return tuple(column for column in self.columns if column.is_generated)

    def arrow_schema(self):
        return pa.schema([
            pa.field(column.name, column.arrow_type(), nullable=not column.required)
            for column in self.source_columns
        ])

    def bigquery_schema(self, *, temporary=False):
        columns = self.source_columns if temporary else self.columns
        return [column.bigquery_field(temporary=temporary) for column in columns]


@dataclass(frozen=True)
class EntityContractSet:
    version: int
    digest: str
    entities: dict[str, EntityContract]
    temporary_table_ttl_hours: int


def contracts_for_stream(contracts: EntityContractSet, stream: str):
    """Return the exact entity subset owned by one ingestion stream."""
    selected = {
        name: contract for name, contract in contracts.entities.items()
        if contract.source_stream == stream
    }
    if not selected:
        raise ValueError(f"Entity contract has no entities for stream: {stream}")
    return EntityContractSet(
        version=contracts.version,
        digest=contracts.digest,
        entities=selected,
        temporary_table_ttl_hours=contracts.temporary_table_ttl_hours,
    )


def _column(name, document):
    if not _IDENTIFIER.fullmatch(name) or not isinstance(document, dict):
        raise ValueError(f"Invalid entity column contract: {name}")
    column_type = document.get("type")
    mode = document.get("mode", "NULLABLE")
    required = document.get("required", False)
    if column_type not in _BQ_TYPES or mode not in ("NULLABLE", "REPEATED"):
        raise ValueError(f"Unsupported entity column type or mode: {name}")
    if not isinstance(required, bool) or required and mode == "REPEATED":
        raise ValueError(f"Invalid required/mode combination: {name}")
    fields_document = document.get("fields", {})
    if column_type == "RECORD":
        if not isinstance(fields_document, dict) or not fields_document:
            raise ValueError(f"RECORD column requires fields: {name}")
        fields = tuple(_column(field_name, value) for field_name, value in fields_document.items())
    else:
        if fields_document:
            raise ValueError(f"Scalar column cannot declare fields: {name}")
        fields = ()
    source, generated = document.get("source"), document.get("generated")
    if (source is None) == (generated is None):
        raise ValueError(f"Column requires exactly one source or generator: {name}")
    if generated is not None and generated != "merge_timestamp":
        raise ValueError(f"Unsupported generated column: {name}")
    return ColumnContract(name, column_type, mode, required, source, generated, fields)


def load_entity_contract(path=DEFAULT_CONTRACT):
    path = Path(path)
    body = path.read_bytes()
    document = yaml.safe_load(body)
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Expected Shopify entity contract version 1")
    publication = document.get("publication")
    entities_document = document.get("entities")
    if not isinstance(publication, dict) or not isinstance(entities_document, dict):
        raise ValueError("Entity contract requires publication and entities")
    ttl = publication.get("temporary_table_ttl_hours")
    if not isinstance(ttl, int) or isinstance(ttl, bool) or not 1 <= ttl <= 168:
        raise ValueError("Temporary table TTL must be between 1 and 168 hours")
    entities = {}
    for name, entity_document in entities_document.items():
        if not _IDENTIFIER.fullmatch(name) or not isinstance(entity_document, dict):
            raise ValueError(f"Invalid entity contract: {name}")
        columns_document = entity_document.get("columns")
        if not isinstance(columns_document, dict) or not columns_document:
            raise ValueError(f"Entity requires columns: {name}")
        columns = tuple(_column(column_name, value)
                        for column_name, value in columns_document.items())
        column_names = {column.name for column in columns}
        key = tuple(entity_document.get("key", ()))
        cluster_by = tuple(entity_document.get("cluster_by", ()))
        if (not key or not set(key) <= column_names or not set(cluster_by) <= column_names
                or any(not next(c for c in columns if c.name == key_name).required
                       for key_name in key)):
            raise ValueError(f"Entity key/cluster columns are invalid: {name}")
        generated = [column for column in columns if column.is_generated]
        if [(column.name, column.generated) for column in generated] != [
                ("extracted_at", "merge_timestamp")]:
            raise ValueError(f"Entity must generate only extracted_at at merge time: {name}")
        entities[name] = EntityContract(
            name=name,
            source_stream=entity_document.get("source_stream"),
            key=key,
            cluster_by=cluster_by,
            columns=columns,
        )
    return EntityContractSet(
        version=1,
        digest=hashlib.sha256(body).hexdigest(),
        entities=entities,
        temporary_table_ttl_hours=ttl,
    )
