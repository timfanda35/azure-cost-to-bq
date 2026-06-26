# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

- Always create a new branch before developing any feature or fix
- Always use `python3` instead of `python` when running Python commands (e.g. `python3 main.py`; `python3 -m pytest`)

## Commands

```bash
# Install dependencies
pip install -r requirements-dev.txt

# Run the server locally
python3 main.py

# Run the sync job once (Cloud Run Job mode)
python3 run_job.py
python3 run_job.py --partition 2026-05   # ad-hoc single billing period

# Run all tests (note: local env may need protobuf workaround)
PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python python3 -m pytest

# Single file / test
PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python python3 -m pytest tests/test_pipeline.py
```

## Architecture

A FastAPI service that also runs as a Cloud Run Job (triggered daily by Cloud Scheduler). It syncs **manually-created** Azure Cost Management exports from Azure Blob Storage to BigQuery. The app does **not** create or manage export definitions — the team creates them in the Azure portal at the EA enrollment (billing account) scope.

```
Azure Blob (Cost Management exports, Parquet)  →  GCS (staging)  →  BigQuery (month-partitioned WRITE_TRUNCATE)
```

**One job = one report.** `BILLING_SCHEMA` selects which report type the job syncs (run one job per type to cover all three):

| `BILLING_SCHEMA` | Export env var | BQ table env var | Schema |
|---|---|---|---|
| `actual` | `EXPORT_NAME` | `BQ_TABLE_ID` | EA cost-and-usage details |
| `amortized` | `EXPORT_NAME` | `BQ_TABLE_ID` | EA cost-and-usage details (same columns as actual) |
| `focus` | `EXPORT_NAME` | `BQ_TABLE_ID` | FOCUS 1.2-preview |

**Flow:** `POST /run` or `run_job.py` → `src/pipeline.py::run_pipeline(partition)` → for each billing period: `AzureBlobSource.latest_run()` → `AzureBlobSource.stream()` → `upload_to_gcs()` → `run_load_job(partition_date=...)`. `BILLING_SCHEMA` maps to the BigQuery schema/clustering via `SCHEMA_MAP` in `src/bigquery.py`.

**Key behaviors:**
- By default each run syncs the **current month + previous month** (`PREVIOUS_MONTHS`, default 1). Re-syncing the previous month each day absorbs Azure's restatements (Azure re-states open-month costs for ~5 days after month close) via partition overwrite.
- `EXPORT_NAME`, `BQ_TABLE_ID`, and `BILLING_SCHEMA` are all required; `BILLING_SCHEMA` must be one of `actual`/`amortized`/`focus` (see `config.BILLING_SCHEMAS`).
- Ad-hoc backfill: `--partition YYYY-MM` (or `PARTITION` env, or `POST {partition}`) processes that single billing period.
- **Manifest is the readiness gate** (`src/sources/azure_blob.py`): only run folders containing a `manifest.json`/`_manifest.json` are ingested; an in-progress run (parquet but no manifest) is ignored. The manifest's `blobs[]` is the authoritative file list — never glob. When multiple runs exist for a month (`CreateNewReport` mode), the latest by `runInfo.submittedTime` wins. No manifest under the month folder → `period.skipped`, retried next run.
- Blob layout read: `{AZURE_ROOT_FOLDER_PATH}/{exportName}/{YYYYMMDD-YYYYMMDD}/{runId}/part*.parquet` (+ manifest). The `YYYYMMDD-YYYYMMDD` folder is the full month range.
- BigQuery loads target a month partition decorator (`table$YYYYMM`) with `WRITE_TRUNCATE`, MONTH partitioning, and per-schema clustering. **Loads always apply the explicit JSON schema in `src/bq_schema/`** (parquet source format) so column types are deterministic regardless of the physical types a given export emits.
- GCS staging path includes a `run_id` timestamp: `{GCS_DESTINATION_PREFIX}/{exportName}/data/{run_id}/month=YYYY-MM/`.
- Blob auth (`Config.blob_auth_mode`): connection string > SAS token > service principal (`ClientSecretCredential`). `AZURE_BLOB_ENDPOINT_URL` overrides the blob client URL for private endpoints (Azure analog of aws `S3_ENDPOINT_URL`).
- `BQ_CMEK_KEY_NAME` (optional) attaches a Cloud KMS key to the load job.

**Schema files** (`src/bq_schema/azure-ea-usage.json` 57 cols / `azure-focus-1.2.json` 105 cols) are authored from the Microsoft dataset-schema docs and applied to every load. They're also covered by a freeze test (`tests/test_bq_schema.py`). When a real export's manifest `dataVersion` or columns change, update the matching schema file.

## Logging

Structured JSON to stdout via `python-json-logger`. Every line has `log_event`, `run_id`, and usually `report`/`export_name`/`partition`.

| `log_event` | Level | When |
|---|---|---|
| `request.received` | INFO | Start of `POST /run` |
| `pipeline.started` | INFO | run_id generated; includes `report`, `periods` |
| `period.started` / `period.complete` | INFO | Per (report, billing period) |
| `blob.run.selected` | INFO | Latest manifest chosen; includes `run_id`, `data_version`, `blob_count` |
| `period.skipped` | WARNING | No manifest (`reason: no_export_files`) or empty manifest (`empty_manifest`) |
| `blob.manifest.unreadable` | WARNING | A manifest failed to parse (that run is ignored) |
| `gcs.file.uploaded` | INFO | After each part uploaded; includes `source_blob`, `gcs_uri` |
| `bq.job.submitted` / `bq.job.complete` | INFO | BQ load; complete includes `output_rows`, `output_bytes` |
| `bq.job.failed` | ERROR | Before `RuntimeError` |
| `pipeline.complete` | INFO | `periods_loaded`, `periods_skipped`, `duration_seconds` |
| `job.started` / `job.complete` / `job.failed` | INFO/ERROR | `run_job.py` CLI lifecycle |

```
# Full timeline for one run
jsonPayload.run_id="20260623-xxx"
# Every BQ partition loaded
jsonPayload.log_event="bq.job.complete"
# Skipped periods
jsonPayload.log_event="period.skipped"
```

## Notes

- Tests mock at the module boundary (`patch("src.pipeline._build_source")`, `patch("main.run_pipeline")`) and never touch Azure/GCP. `AzureBlobSource` accepts an injected `container_client` for unit testing.
- Modeled on the sibling project `aws-cost-to-bq` (same logging, dual-mode Docker, CI).
