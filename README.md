# azure-cost-to-bq

Sync Azure Cost Management exports (Parquet) from Azure Blob Storage into Google BigQuery. The Azure counterpart of [aws-cost-to-bq](https://github.com/timfanda35/aws-cost-to-bq).

```
Azure Blob (Cost Management exports)  →  GCS (staging)  →  BigQuery (month-partitioned)
```

The team creates the exports manually in the Azure portal at the **EA enrollment (billing account)** scope. This app only **reads** the files those exports produce — it does not create or manage export definitions.

## What it does

- Syncs three report types, each into its own BigQuery table: **actual**, **amortized**, and **FOCUS 1.2-preview**.
- Each scheduled run loads the **current month + previous month** (configurable). Re-loading the previous month each day picks up Azure's late restatements automatically (`WRITE_TRUNCATE` per month partition).
- Drives discovery from each export run's `manifest.json` — only complete runs are ingested, and the latest run per month wins.
- Supports ad-hoc loads of a specific billing period and/or a single report type.

## Prerequisites

1. **Azure exports** (created manually, daily, Parquet) at the EA enrollment scope, writing to a storage account container:
   - Cost and usage details (actual)
   - Cost and usage details (amortized)
   - Cost and usage details (FOCUS), version 1.2-preview
2. **Azure service principal** with **Storage Blob Data Reader** on the storage account (or use a SAS token / connection string).
3. **GCP**: a GCS staging bucket and a BigQuery dataset. The runtime service account needs `roles/storage.objectAdmin` on the bucket and `roles/bigquery.dataEditor` + `roles/bigquery.jobUser` on the project/dataset.

## Configuration

Copy `.env.example` to `.env` and fill it in. Required: `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_STORAGE_CONTAINER`, blob auth (SP/SAS/connection string), at least one `EXPORT_*_NAME`, `GCS_BUCKET`, `BQ_PROJECT_ID`, `BQ_DATASET_ID`. See `.env.example` for the full list and `CLAUDE.md` for behavior details.

## Run locally

```bash
pip install -r requirements-dev.txt

# One-off sync (current + previous month, all configured reports)
python3 run_job.py

# Ad-hoc: a specific billing period and/or a single report type
python3 run_job.py --partition 2026-05
python3 run_job.py --partition 2026-05 --report focus

# As an HTTP server
python3 main.py
curl -X POST localhost:8080/run -H 'content-type: application/json' \
  -d '{"report": "focus", "partition": "2026-05"}'
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
  --set-env-vars "AZURE_STORAGE_ACCOUNT_URL=...,AZURE_STORAGE_CONTAINER=exports,EXPORT_ACTUAL_NAME=...,EXPORT_AMORTIZED_NAME=...,EXPORT_FOCUS_NAME=...,GCS_BUCKET=...,BQ_PROJECT_ID=...,BQ_DATASET_ID=billing" \
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

Parquet is self-describing, so by default BigQuery loads use the **files' embedded schema** — robust against the exact physical types Azure emits. Explicit schemas for both datasets live in `src/bq_schema/` (authored from the Microsoft dataset-schema docs) and are applied only when `BQ_ENFORCE_SCHEMA=true`. Verify a real export's manifest `dataVersion` and columns before enabling enforcement.

## Notes & caveats

- **EA only**: FOCUS/actual/amortized exports are supported at the EA enrollment scope. Management-group-scoped exports (a different, more limited path) are not used here.
- Open-month data is an estimate until the invoice is issued; the current+previous month re-sync keeps BigQuery aligned with Azure's restatements.
- First real run: confirm the FOCUS export's `dataVersion` is `1.2-preview` and that the EA usage export version matches `src/bq_schema/azure-ea-usage.json` (version 2024-08-01, 57 columns) if you plan to enforce schemas.
