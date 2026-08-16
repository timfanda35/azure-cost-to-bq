import logging
from datetime import datetime, timezone

from src.bigquery import SCHEMA_MAP, run_load_job
from src.gcs import upload_to_gcs
from src.merge_config import MergeTargetConfig, SourceConfig
from src.pipeline import _basename, _build_source, _join

logger = logging.getLogger(__name__)

_SOURCES = ("a", "b")


def run_merge(partition: str, label: str | None = None) -> dict:
    """Merge two Azure Cost Management export sources into one BQ month partition.

    Both sources' Parquet files are staged under the same GCS folder and loaded
    together in a single BigQuery load job (WRITE_TRUNCATE), since two sequential
    loads into the same partition would not be additive. If either source's
    manifest isn't ready, the whole merge is aborted before anything is uploaded
    or loaded.
    """
    cfgs = {"a": SourceConfig.from_env("SOURCE_A_"), "b": SourceConfig.from_env("SOURCE_B_")}
    target = MergeTargetConfig.from_env()

    month = datetime.strptime(partition, "%Y-%m").date().replace(day=1)
    partition_str = month.strftime("%Y-%m")
    schema_config = SCHEMA_MAP[target.billing_schema]

    label = label or f"merge-{cfgs['a'].export_name}-{cfgs['b'].export_name}"

    now = datetime.now(timezone.utc)
    run_id = f"merge-{now.strftime('%Y%m%d')}-{int(now.timestamp())}"

    logger.info("merge.period.started", extra={
        "log_event": "merge.period.started",
        "run_id": run_id,
        "label": label,
        "partition": partition_str,
        "source_a_export": cfgs["a"].export_name,
        "source_b_export": cfgs["b"].export_name,
    })

    sources = {key: _build_source(cfg) for key, cfg in cfgs.items()}

    runs = {}
    for key in _SOURCES:
        cfg, source = cfgs[key], sources[key]
        try:
            run = source.latest_run(cfg.azure_root_folder_path, cfg.export_name, month)
        except FileNotFoundError as exc:
            return _skip(run_id, label, partition_str, key, cfg, "no_export_files", str(exc))
        if not run.blobs:
            return _skip(run_id, label, partition_str, key, cfg, "empty_manifest")
        runs[key] = run

    gcs_base = _join(target.gcs_destination_prefix, label, "data", run_id, f"month={partition_str}")

    wildcards = []
    files_by_source = {}
    for key in _SOURCES:
        cfg, source, run = cfgs[key], sources[key], runs[key]
        sub_base = _join(gcs_base, f"source={key}")
        for blob_name in run.blobs:
            dest = f"{sub_base}/{_basename(blob_name)}"
            upload_to_gcs(
                source.stream(blob_name), target.gcs_bucket, dest,
                run_id=run_id, export_name=cfg.export_name,
                partition=partition_str, source_blob=blob_name,
            )
        wildcards.append(f"gs://{target.gcs_bucket}/{sub_base}/*.parquet")
        files_by_source[key] = len(run.blobs)

    bq_table = f"{target.bq_project_id}.{target.bq_dataset_id}.{target.bq_table_id}"
    run_load_job(
        wildcards, target.bq_project_id, target.bq_dataset_id, target.bq_table_id,
        partition_date=month, schema_config=schema_config,
        run_id=run_id, export_name=label, partition_label=partition_str,
        kms_key_name=target.bq_cmek_key_name,
    )

    total_files = files_by_source["a"] + files_by_source["b"]
    logger.info("merge.period.complete", extra={
        "log_event": "merge.period.complete",
        "run_id": run_id,
        "label": label,
        "partition": partition_str,
        "files_a": files_by_source["a"],
        "files_b": files_by_source["b"],
        "files": total_files,
        "bq_table": bq_table,
    })

    return {
        "run_id": run_id,
        "label": label,
        "partition": partition_str,
        "skipped": False,
        "files_a": files_by_source["a"],
        "files_b": files_by_source["b"],
        "files": total_files,
        "bq_table": bq_table,
    }


def _skip(run_id, label, partition_str, source_key, cfg, reason, detail=None) -> dict:
    extra = {
        "log_event": "merge.source.missing",
        "run_id": run_id,
        "label": label,
        "source": source_key,
        "export_name": cfg.export_name,
        "partition": partition_str,
        "reason": reason,
    }
    if detail:
        extra["detail"] = detail
    logger.warning("merge.source.missing", extra=extra)
    return {
        "run_id": run_id,
        "label": label,
        "partition": partition_str,
        "skipped": True,
        "reason": reason,
        "source": source_key,
        "files": 0,
    }
