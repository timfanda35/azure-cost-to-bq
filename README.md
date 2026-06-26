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

## Run locally

```bash
pip install -r requirements-dev.txt

# One-off sync (current + previous month, for this job's configured report)
python3 run_job.py

# Ad-hoc: a specific billing period
python3 run_job.py --partition 2026-05

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
