"""Shared Parquet landing and BigQuery MERGE for a validated Shopify stream."""
import tempfile

from .entity_contract import contracts_for_stream, load_entity_contract
from .entity_landing import land_entity_artifacts
from .entity_parquet import write_entity_parquet
from .entity_publication import initialize_entity_tables, publish_entity_batch
from .shopify_entities import iter_stream_entities


def publish_stream_entity_shadow(record_factory, bucket, bigquery_client, dataset,
                                 identity, *, stream, source_files, window_start,
                                 window_end, published_at):
    all_contracts = load_entity_contract()
    contracts = contracts_for_stream(all_contracts, stream)
    rows = iter_stream_entities(
        stream, record_factory, source_files, identity, published_at,
        contracts.entities,
    )
    with tempfile.TemporaryDirectory(prefix="shopify-entity-parquet-") as directory:
        artifacts = write_entity_parquet(
            rows, contracts, directory, shop_key=identity.shop_key,
            stream=stream, extraction_id=identity.extraction_id,
        )
        manifest = land_entity_artifacts(
            bucket, artifacts, source_files=source_files,
            query_sha256=identity.query_sha256,
            request_sha256=identity.request_sha256,
            window_start=window_start, window_end=window_end,
            published_at=published_at,
        )
        # Create/validate the complete shadow surface so the shared dbt shadow
        # build remains runnable after any one family lands first.
        initialize_entity_tables(bigquery_client, dataset, all_contracts)
        publication = publish_entity_batch(bigquery_client, dataset, manifest, contracts)
    return {"manifest": manifest, "publication": publication}
