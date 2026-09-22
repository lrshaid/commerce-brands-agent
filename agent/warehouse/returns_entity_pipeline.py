"""Normalize, land and atomically MERGE one accepted Returns extraction."""
import tempfile

from .entity_contract import contracts_for_stream, load_entity_contract
from .entity_landing import land_entity_artifacts
from .entity_publication import initialize_entity_tables, publish_entity_batch
from .entity_parquet import write_entity_parquet
from .returns_engine import iter_entities


def publish_returns_entity_shadow(source, bucket, bigquery_client, dataset, identity,
                                  *, source_file, window_start, window_end, published_at):
    all_contracts = load_entity_contract()
    contracts = contracts_for_stream(all_contracts, "returns")
    source.seek(0)
    rows = iter_entities(
        source, identity, published_at, contracts.entities
    )
    with tempfile.TemporaryDirectory(prefix="shopify-entity-parquet-") as directory:
        artifacts = write_entity_parquet(
            rows, contracts, directory,
            shop_key=identity.shop_key,
            stream="returns",
            extraction_id=identity.extraction_id,
        )
        manifest = land_entity_artifacts(
            bucket, artifacts,
            source_files=[{key: source_file[key] for key in ("uri", "generation", "sha256")}],
            query_sha256=identity.query_sha256,
            request_sha256=identity.request_sha256,
            window_start=window_start,
            window_end=window_end,
            published_at=published_at,
        )
        initialize_entity_tables(bigquery_client, dataset, all_contracts)
        publication = publish_entity_batch(bigquery_client, dataset, manifest, contracts)
    return {"manifest": manifest, "publication": publication}
