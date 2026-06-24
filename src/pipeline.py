import logging
import time
from datetime import date, datetime, timezone

from src.config import Config
from src.sources.azure_blob import AzureBlobSource
from src.gcs import upload_to_gcs
from src.bigquery import run_load_job, SCHEMA_MAP

logger = logging.getLogger(__name__)


def billing_periods(today: date | None = None, previous_months: int = 1) -> list[date]:
    """First-of-month dates for the current month plus ``previous_months``
    earlier months, oldest first."""
    if today is None:
        today = date.today()
    periods: list[date] = []
    for offset in range(previous_months, -1, -1):
        month = today.month - offset
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        periods.append(date(year, month, 1))
    return periods


def _join(*parts: str) -> str:
    return "/".join(p.strip("/") for p in parts if p)


def _basename(blob_name: str) -> str:
    return blob_name.rsplit("/", 1)[-1]


def _build_source(cfg: Config) -> AzureBlobSource:
    if cfg.blob_auth_mode == "connection_string":
        return AzureBlobSource(
            cfg.azure_storage_account_url, cfg.azure_storage_container,
            connection_string=cfg.azure_storage_connection_string,
            endpoint_url=cfg.azure_blob_endpoint_url,
        )
    if cfg.blob_auth_mode == "sas":
        return AzureBlobSource(
            cfg.azure_storage_account_url, cfg.azure_storage_container,
            sas_token=cfg.azure_storage_sas_token,
            endpoint_url=cfg.azure_blob_endpoint_url,
        )
    from azure.identity import ClientSecretCredential
    credential = ClientSecretCredential(
        cfg.azure_tenant_id, cfg.azure_client_id, cfg.azure_client_secret
    )
    return AzureBlobSource(
        cfg.azure_storage_account_url, cfg.azure_storage_container,
        credential=credential, endpoint_url=cfg.azure_blob_endpoint_url,
    )


def run_pipeline(partition: str | None = None, report: str | None = None) -> dict:
    cfg = Config()
    source = _build_source(cfg)

    now = datetime.now(timezone.utc)
    run_id = f"{now.strftime('%Y%m%d')}-{int(now.timestamp())}"
    start_time = time.monotonic()

    if partition is not None:
        parsed = datetime.strptime(partition, "%Y-%m").date().replace(day=1)
        periods = [parsed]
    else:
        periods = billing_periods(now.date(), cfg.previous_months)

    specs = cfg.selected_exports(report)

    logger.info("pipeline.started", extra={
        "log_event": "pipeline.started",
        "run_id": run_id,
        "reports": [s.report_type for s in specs],
        "periods": [p.strftime("%Y-%m") for p in periods],
        "enforce_schema": cfg.bq_enforce_schema,
    })

    periods_loaded = 0
    periods_skipped = 0
    results = []

    for spec in specs:
        schema_config = SCHEMA_MAP[spec.report_type]
        for month in periods:
            result = _sync_one(cfg, source, spec, schema_config, month, run_id)
            results.append(result)
            if result["skipped"]:
                periods_skipped += 1
            else:
                periods_loaded += 1

    duration = time.monotonic() - start_time
    logger.info("pipeline.complete", extra={
        "log_event": "pipeline.complete",
        "run_id": run_id,
        "periods_loaded": periods_loaded,
        "periods_skipped": periods_skipped,
        "duration_seconds": round(duration, 2),
    })

    return {
        "run_id": run_id,
        "reports": [s.report_type for s in specs],
        "periods": [p.strftime("%Y-%m") for p in periods],
        "periods_loaded": periods_loaded,
        "periods_skipped": periods_skipped,
        "results": results,
    }


def _sync_one(cfg, source, spec, schema_config, month: date, run_id: str) -> dict:
    partition_str = month.strftime("%Y-%m")
    base_result = {
        "report": spec.report_type,
        "export_name": spec.export_name,
        "partition": partition_str,
    }

    logger.info("period.started", extra={
        "log_event": "period.started",
        "run_id": run_id,
        "report": spec.report_type,
        "export_name": spec.export_name,
        "partition": partition_str,
    })

    try:
        run = source.latest_run(cfg.azure_root_folder_path, spec.export_name, month)
    except FileNotFoundError as exc:
        logger.warning("period.skipped", extra={
            "log_event": "period.skipped",
            "run_id": run_id,
            "report": spec.report_type,
            "export_name": spec.export_name,
            "partition": partition_str,
            "reason": "no_export_files",
            "detail": str(exc),
        })
        return {**base_result, "skipped": True, "reason": "no_export_files", "files": 0}

    if not run.blobs:
        logger.warning("period.skipped", extra={
            "log_event": "period.skipped",
            "run_id": run_id,
            "report": spec.report_type,
            "export_name": spec.export_name,
            "partition": partition_str,
            "reason": "empty_manifest",
            "run_id_blob": run.run_id,
        })
        return {**base_result, "skipped": True, "reason": "empty_manifest", "files": 0}

    gcs_base = _join(
        cfg.gcs_destination_prefix, spec.export_name, "data", run_id, f"month={partition_str}"
    )
    gcs_uris = []
    for blob_name in run.blobs:
        dest = f"{gcs_base}/{_basename(blob_name)}"
        uri = upload_to_gcs(
            source.stream(blob_name), cfg.gcs_bucket, dest,
            run_id=run_id, export_name=spec.export_name,
            partition=partition_str, source_blob=blob_name,
        )
        gcs_uris.append(uri)

    wildcard = f"gs://{cfg.gcs_bucket}/{gcs_base}/*.parquet"
    bq_table = f"{cfg.bq_project_id}.{cfg.bq_dataset_id}.{spec.bq_table_id}"
    run_load_job(
        wildcard, cfg.bq_project_id, cfg.bq_dataset_id, spec.bq_table_id,
        partition_date=month, schema_config=schema_config,
        run_id=run_id, export_name=spec.export_name, partition_label=partition_str,
        kms_key_name=cfg.bq_cmek_key_name, enforce_schema=cfg.bq_enforce_schema,
    )

    logger.info("period.complete", extra={
        "log_event": "period.complete",
        "run_id": run_id,
        "report": spec.report_type,
        "export_name": spec.export_name,
        "partition": partition_str,
        "files": len(gcs_uris),
        "bq_table": bq_table,
    })

    return {
        **base_result,
        "skipped": False,
        "files": len(gcs_uris),
        "gcs_uris": gcs_uris,
        "bq_table": bq_table,
        "run_id_blob": run.run_id,
    }
