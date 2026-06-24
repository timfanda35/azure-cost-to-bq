import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from google.cloud import bigquery


@dataclass(frozen=True)
class SchemaConfig:
    schema_path: Path
    partition_field: str
    cluster_fields: tuple[str, ...]


# EA "cost and usage details" schema, shared by the actual and amortized
# exports (same columns; they differ only in cost values).
EA_USAGE_SCHEMA = SchemaConfig(
    schema_path=Path(__file__).parent / "bq_schema" / "azure-ea-usage.json",
    partition_field="BillingPeriodStartDate",
    cluster_fields=("Date", "SubscriptionName"),
)

# FOCUS 1.2-preview schema.
FOCUS12_SCHEMA = SchemaConfig(
    schema_path=Path(__file__).parent / "bq_schema" / "azure-focus-1.2.json",
    partition_field="BillingPeriodStart",
    cluster_fields=("ChargePeriodStart", "SubAccountName"),
)

# Maps a BILLING_SCHEMA value (see src.config.BILLING_SCHEMAS) to its BigQuery schema.
SCHEMA_MAP: dict[str, SchemaConfig] = {
    "actual": EA_USAGE_SCHEMA,
    "amortized": EA_USAGE_SCHEMA,
    "focus": FOCUS12_SCHEMA,
}

logger = logging.getLogger(__name__)


def run_load_job(
    gcs_uri: str,
    project_id: str,
    dataset_id: str,
    table_id: str,
    partition_date: date | None = None,
    *,
    schema_config: SchemaConfig,
    run_id: str = "",
    export_name: str = "",
    partition_label: str = "",
    kms_key_name: str | None = None,
    enforce_schema: bool = False,
) -> None:
    """Load parquet file(s) from GCS into BigQuery (WRITE_TRUNCATE).

    Parquet is self-describing, so by default the column schema is taken from
    the files themselves — the most robust option for Azure exports whose exact
    physical types we don't control. Set ``enforce_schema=True`` to instead apply
    the explicit JSON schema in ``schema_config.schema_path`` (once verified
    against a real export).

    When ``partition_date`` is given the load targets that specific month
    partition using a decorator (``table$YYYYMM``), replacing only that partition
    rather than the entire table. The partition and clustering fields must exist
    in the parquet data.
    """
    client = bigquery.Client(project=project_id)
    if partition_date:
        table_ref = f"{project_id}.{dataset_id}.{table_id}${partition_date.strftime('%Y%m')}"
    else:
        table_ref = f"{project_id}.{dataset_id}.{table_id}"

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        time_partitioning=bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.MONTH,
            field=schema_config.partition_field,
        ),
        clustering_fields=list(schema_config.cluster_fields),
    )
    if enforce_schema:
        job_config.schema = client.schema_from_json(schema_config.schema_path)
    if kms_key_name:
        job_config.destination_encryption_configuration = bigquery.EncryptionConfiguration(
            kms_key_name=kms_key_name
        )

    job = client.load_table_from_uri(gcs_uri, table_ref, job_config=job_config)
    logger.info("bq.job.submitted", extra={
        "log_event": "bq.job.submitted",
        "run_id": run_id,
        "export_name": export_name,
        "partition": partition_label,
        "job_id": job.job_id,
        "gcs_uri": gcs_uri,
        "bq_table": table_ref,
    })

    job.result(timeout=3300)  # blocks until complete

    if job.errors:
        logger.error("bq.job.failed", extra={
            "log_event": "bq.job.failed",
            "run_id": run_id,
            "export_name": export_name,
            "partition": partition_label,
            "job_id": job.job_id,
            "errors": job.errors,
        })
        raise RuntimeError(f"BigQuery load job failed: {job.errors}")

    logger.info("bq.job.complete", extra={
        "log_event": "bq.job.complete",
        "run_id": run_id,
        "export_name": export_name,
        "partition": partition_label,
        "job_id": job.job_id,
        "output_rows": job.output_rows,
        "output_bytes": job.output_bytes,
    })
