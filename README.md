# azure-cost-to-bq

Sync Azure Cost Management exports (Parquet) from Azure Blob Storage into Google BigQuery. The Azure counterpart of [aws-cost-to-bq](https://github.com/timfanda35/aws-cost-to-bq).

```
Azure Blob (Cost Management exports)  →  GCS (staging)  →  BigQuery (month-partitioned)
```

The team creates the exports manually in the Azure portal at the **EA enrollment (billing account)** scope. This app only **reads** the files those exports produce — it does not create or manage export definitions.

## What it does

- Syncs one report type per job into its own BigQuery table — **actual**, **amortized**, or **FOCUS 1.2-preview** (`BILLING_SCHEMA`). Run one job per report type to cover all three.
- Each scheduled run loads the **current month + previous month** (configurable). Re-loading the previous month each day picks up Azure's late restatements automatically (`WRITE_TRUNCATE` per month partition).
- Drives discovery from each export run's `manifest.json` — only complete runs are ingested, and the latest run per month wins.
- Supports ad-hoc loads of a specific billing period.

## Prerequisites

1. **Azure exports** (created manually, daily, Parquet) at the EA enrollment scope, writing to a storage account container:
   - Cost and usage details (actual)
   - Cost and usage details (amortized)
   - Cost and usage details (FOCUS), version 1.2-preview
2. **Azure service principal** with **Storage Blob Data Reader** on the storage account (or use a SAS token / connection string).
3. **GCP**: a GCS staging bucket and a BigQuery dataset. The runtime service account needs `roles/storage.objectAdmin` on the bucket and `roles/bigquery.dataEditor` + `roles/bigquery.jobUser` on the project/dataset.

## Configuration

Copy `.env.example` to `.env` and fill it in. One job syncs one report type. Required: `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_STORAGE_CONTAINER`, blob auth (SP/SAS/connection string), `EXPORT_NAME`, `BILLING_SCHEMA`, `BQ_TABLE_ID`, `GCS_BUCKET`, `BQ_PROJECT_ID`, `BQ_DATASET_ID`. See the full reference below and `CLAUDE.md` for behavior details.

## Environment Variables

**Azure Blob — connection**

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `AZURE_STORAGE_ACCOUNT_URL` | Yes | — | Storage account base URL (`https://acct.blob.core.windows.net`) |
| `AZURE_STORAGE_CONTAINER` | Yes | — | Container holding the Cost Management exports |
| `AZURE_BLOB_ENDPOINT_URL` | No | (account URL) | Override blob endpoint (e.g. private link) |
| `AZURE_ROOT_FOLDER_PATH` | No | `""` | Path prefix inside the container where exports live |

**Azure Blob — auth** (one method required; priority: connection string > SAS > service principal)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `AZURE_STORAGE_CONNECTION_STRING` | One-of | — | Full connection string (priority 1) |
| `AZURE_STORAGE_SAS_TOKEN` | One-of | — | SAS token (priority 2) |
| `AZURE_TENANT_ID` | One-of | — | Service principal tenant (priority 3) |
| `AZURE_CLIENT_ID` | One-of | — | Service principal app/client ID (priority 3) |
| `AZURE_CLIENT_SECRET` | One-of | — | Service principal secret — store in Secret Manager (priority 3) |

**Report export** (one report per job)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `EXPORT_NAME` | Yes | — | Azure Cost Management export name (a path segment in blob storage) |
| `BILLING_SCHEMA` | Yes | — | Report type / schema: `actual`, `amortized`, or `focus` (FOCUS 1.2-preview) |

**GCS staging**

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GCS_BUCKET` | Yes | — | Staging bucket for parquet before BQ load |
| `GCS_DESTINATION_PREFIX` | No | `""` | Path prefix inside the staging bucket |

**BigQuery target**

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `BQ_PROJECT_ID` | Yes | — | Project holding the dataset/tables |
| `BQ_DATASET_ID` | Yes | — | Target dataset |
| `BQ_TABLE_ID` | Yes | — | Destination table for this report (e.g. `azure_cost_focus`) |
| `BQ_CMEK_KEY_NAME` | No | — | Cloud KMS key resource name for the load job |

**Run window**

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `PREVIOUS_MONTHS` | No | `1` | Previous months to sync alongside the current month |
| `PARTITION` | No | — | Single billing period `YYYY-MM` to load; `--partition` CLI arg wins |

**Runtime**

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `LOG_LEVEL` | No | `INFO` | Logging level (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`) |
| `PORT` | No | `8080` | HTTP port when running `main.py` (server mode) |

`PARTITION` is overridden by the `--partition` CLI equivalent.

**Merge sources** (`merge_sources.py` only — ad-hoc, for an EA export migration that doesn't
land on a billing-period boundary)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SOURCE_A_AZURE_STORAGE_ACCOUNT_URL` / `SOURCE_B_AZURE_STORAGE_ACCOUNT_URL` | Yes | — | Per-source storage account base URL |
| `SOURCE_A_AZURE_STORAGE_CONTAINER` / `SOURCE_B_AZURE_STORAGE_CONTAINER` | Yes | — | Per-source container |
| `SOURCE_A_AZURE_ROOT_FOLDER_PATH` / `SOURCE_B_AZURE_ROOT_FOLDER_PATH` | No | `""` | Per-source path prefix |
| `SOURCE_A_EXPORT_NAME` / `SOURCE_B_EXPORT_NAME` | Yes | — | Per-source export name |
| `SOURCE_A_AZURE_BLOB_ENDPOINT_URL` / `SOURCE_B_...` | No | (account URL) | Per-source private-link override |
| `SOURCE_A_AZURE_STORAGE_CONNECTION_STRING` / `SOURCE_B_...` | One-of | — | Per-source auth, priority 1 |
| `SOURCE_A_AZURE_STORAGE_SAS_TOKEN` / `SOURCE_B_...` | One-of | — | Per-source auth, priority 2 |
| `SOURCE_A_AZURE_TENANT_ID`+`CLIENT_ID`+`CLIENT_SECRET` / `SOURCE_B_...` | One-of | — | Per-source auth, priority 3 |

The BigQuery/GCS destination (`GCS_BUCKET`, `GCS_DESTINATION_PREFIX`, `BQ_PROJECT_ID`,
`BQ_DATASET_ID`, `BQ_TABLE_ID`, `BQ_CMEK_KEY_NAME`, `BILLING_SCHEMA`) is shared with the
normal job's env vars above — no `SOURCE_A_`/`SOURCE_B_` prefix. Both sources' Parquet
files are staged together and loaded in a single job so the partition ends up with both
sources' rows instead of the second load truncating the first. This does **not**
deduplicate rows: it assumes the two sources' date coverage for the billing period
doesn't overlap. Before trusting the merged partition, confirm both sources' `data_version`
(logged in `blob.run.selected`) match — the load applies one explicit schema to both.

**Important:** if the merged month is still inside the daily job's sync window
(current month, or one of the `PREVIOUS_MONTHS` previous months), the next scheduled
run will `WRITE_TRUNCATE` that partition with the single configured export's data
and silently drop the other source's rows. Either re-run `merge_sources.py` after
each daily sync until the month ages out of the window, or set `PREVIOUS_MONTHS`
low enough (e.g. `0`) that the daily job no longer touches that month once it's closed.

**Append source** (`append.py` — a simpler alternative to `merge_sources.py` for the same
migration scenario, run as two separate steps instead of one combined load)

Uses the same env vars as the normal job — no `SOURCE_A_`/`SOURCE_B_` prefix. Run
`backfill.py`/`run_job.py` first with source A's env vars (normal `WRITE_TRUNCATE`
load), then run `append.py --partition YYYY-MM` with the env vars pointed at source B:
it loads into the same partition with `WRITE_APPEND` instead of truncating, so B's rows
land alongside A's rather than replacing them. Like merge, this does **not** deduplicate
rows and assumes the two sources' date coverage doesn't overlap.

**Important:** the same sync-window caveat as merge applies — if the appended month is
still inside the daily job's sync window, the next scheduled run will `WRITE_TRUNCATE`
that partition and drop the appended source's rows. Re-run `append.py` after each daily
sync, or lower `PREVIOUS_MONTHS`, until the month ages out of the window. Unlike merge,
`WRITE_APPEND` is not idempotent: re-running `append.py` for a month *without* an
intervening truncate (e.g. two manual runs in the same day) duplicates source B's rows
for that partition.

## Run locally

```bash
pip install -r requirements-dev.txt

# One-off sync (current + previous month, for this job's configured report)
python3 run_job.py

# Ad-hoc: a specific billing period
python3 run_job.py --partition 2026-05

# Backfill: a range of billing periods, one run_pipeline call per month
python3 backfill.py --start 2026-01 --end 2026-05

# Merge two export sources (e.g. an EA export migration mid-month) into one partition
python3 merge_sources.py --partition 2026-06

# Append a second source into a partition another job already loaded (WRITE_APPEND)
python3 append.py --partition 2026-06

# As an HTTP server
python3 main.py
curl -X POST localhost:8080/run -H 'content-type: application/json' \
  -d '{"partition": "2026-05"}'
```

## Tests

```bash
PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python python3 -m pytest
```

## Deploy (Cloud Run Job + Scheduler)

The image is dual-mode (see `Dockerfile`): default `CMD` runs the job (`run_job.py`); override `CMD` with uvicorn args for the HTTP service.

```bash
# Build & push (or use the GitHub Actions workflow → GHCR)
IMAGE=ghcr.io/<owner>/azure-cost-to-bq:latest

# Deploy as a Cloud Run Job
gcloud run jobs deploy azure-cost-to-bq \
  --image "$IMAGE" \
  --service-account "$SERVICE_ACCOUNT" \
  --set-env-vars "AZURE_STORAGE_ACCOUNT_URL=...,AZURE_STORAGE_CONTAINER=exports,EXPORT_NAME=...,BILLING_SCHEMA=focus,BQ_TABLE_ID=azure_cost_focus,GCS_BUCKET=...,BQ_PROJECT_ID=...,BQ_DATASET_ID=billing" \
  --set-secrets "AZURE_CLIENT_SECRET=azure-cost-sp-secret:latest" \
  --set-env-vars "AZURE_TENANT_ID=...,AZURE_CLIENT_ID=..."

# Trigger daily (after Azure's overnight export window, ~08:00 UTC)
gcloud scheduler jobs create http azure-cost-to-bq-daily \
  --schedule "0 8 * * *" \
  --uri "https://<region>-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/<project>/jobs/azure-cost-to-bq:run" \
  --http-method POST --oauth-service-account-email "$SCHEDULER_SA"
```

Store the SP secret in Secret Manager; never bake credentials into the image.

## Schema handling

BigQuery loads always apply the **explicit JSON schema** for the dataset, so column types are deterministic regardless of the physical types a given export emits. The schemas for both datasets live in `src/bq_schema/` (authored from the Microsoft dataset-schema docs and verified against real exports). When a new export's manifest `dataVersion` or columns change, update the matching schema file in `src/bq_schema/`.

## Notes & caveats

- **EA only**: FOCUS/actual/amortized exports are supported at the EA enrollment scope. Management-group-scoped exports (a different, more limited path) are not used here.
- Open-month data is an estimate until the invoice is issued; the current+previous month re-sync keeps BigQuery aligned with Azure's restatements.
- First real run: confirm the FOCUS export's `dataVersion` is `1.2-preview` and that the EA usage export version matches `src/bq_schema/azure-ea-usage.json` (version 2024-08-01, 57 columns) if you plan to enforce schemas.
