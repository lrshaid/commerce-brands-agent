"""Bounded-memory Parquet artifacts for canonical entity batches."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote

import pyarrow as pa
import pyarrow.parquet as pq

from .entity_contract import EntityContractSet
from .orders_entities import EntityRow


@dataclass(frozen=True)
class EntityArtifact:
    entity: str
    path: str
    row_count: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class EntityBatchArtifacts:
    version: int
    contract_sha256: str
    shop_key: str
    stream: str
    extraction_id: str
    created_at: str
    files: tuple[EntityArtifact, ...]
    manifest_path: str

    def counts(self):
        return {artifact.entity: artifact.row_count for artifact in self.files}


class _EntityWriter:
    def __init__(self, contract, path, contract_digest, batch_rows, row_group_size):
        self.contract = contract
        self.path = path
        self.batch_rows = batch_rows
        self.row_group_size = row_group_size
        self.buffer = []
        self.row_count = 0
        schema = contract.arrow_schema().with_metadata({
            b"entity": contract.name.encode(),
            b"contract_sha256": contract_digest.encode(),
        })
        self.writer = pq.ParquetWriter(
            path, schema, compression="zstd", version="2.6",
            use_dictionary=True, write_statistics=True,
        )

    def add(self, values):
        self.buffer.append(dict(values))
        if len(self.buffer) >= self.batch_rows:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        table = pa.Table.from_pylist(self.buffer, schema=self.writer.schema)
        self.writer.write_table(table, row_group_size=self.row_group_size)
        self.row_count += len(self.buffer)
        self.buffer.clear()

    def close(self):
        self.flush()
        self.writer.close()


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_entity_parquet(rows, contracts: EntityContractSet, output_root,
                         *, shop_key, stream, extraction_id,
                         batch_rows=5000, row_group_size=5000):
    """Write one Parquet file per entity and an atomic compact manifest."""
    if (not isinstance(batch_rows, int) or isinstance(batch_rows, bool) or batch_rows < 1
            or not isinstance(row_group_size, int) or isinstance(row_group_size, bool)
            or row_group_size < 1):
        raise ValueError("Parquet batch and row-group sizes must be positive integers")
    safe_extraction = quote(extraction_id, safe="")
    base = Path(output_root) / "shopify" / "entities" / f"v{contracts.version}"
    writers = {}
    try:
        for entity, contract in contracts.entities.items():
            folder = base / entity / f"extraction_id={safe_extraction}"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "part-00000.parquet"
            writers[entity] = _EntityWriter(
                contract, path, contracts.digest, batch_rows, row_group_size
            )
        for row in rows:
            if not isinstance(row, EntityRow) or row.entity not in writers:
                raise ValueError("Normalizer emitted an unknown entity row")
            writers[row.entity].add(row.values)
        for writer in writers.values():
            writer.close()
    except Exception:
        for writer in writers.values():
            try:
                writer.writer.close()
            except Exception:
                pass
        raise

    files = tuple(EntityArtifact(
        entity=entity,
        path=str(writer.path),
        row_count=writer.row_count,
        size_bytes=writer.path.stat().st_size,
        sha256=_sha256(writer.path),
    ) for entity, writer in writers.items())
    manifest_folder = base / "manifests" / f"extraction_id={safe_extraction}"
    manifest_folder.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_folder / "manifest.json"
    created_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "version": contracts.version,
        "contract_sha256": contracts.digest,
        "shop_key": shop_key,
        "stream": stream,
        "extraction_id": extraction_id,
        "created_at": created_at,
        "files": [asdict(artifact) for artifact in files],
    }
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, manifest_path)
    return EntityBatchArtifacts(
        version=contracts.version,
        contract_sha256=contracts.digest,
        shop_key=shop_key,
        stream=stream,
        extraction_id=extraction_id,
        created_at=created_at,
        files=files,
        manifest_path=str(manifest_path),
    )
