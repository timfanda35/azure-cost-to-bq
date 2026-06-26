import logging
from datetime import date
from unittest.mock import MagicMock, patch
import pytest
from google.cloud import bigquery
from src.bigquery import run_load_job, EA_USAGE_SCHEMA, FOCUS12_SCHEMA, SCHEMA_MAP


def _mock_job(error=None):
    job = MagicMock()
    job.job_id = "test-job-id-123"
    job.output_rows = 42000
    job.output_bytes = 1048576
    job.result.return_value = None
    job.errors = [{"message": error}] if error else []
    return job


def test_schema_map_covers_three_report_types():
    assert SCHEMA_MAP["actual"] is EA_USAGE_SCHEMA
    assert SCHEMA_MAP["amortized"] is EA_USAGE_SCHEMA
    assert SCHEMA_MAP["focus"] is FOCUS12_SCHEMA


def test_schema_map_keys_match_billing_schemas():
    from src.config import BILLING_SCHEMAS
    assert set(BILLING_SCHEMAS) == set(SCHEMA_MAP)


def test_load_job_applies_explicit_schema():
    job = _mock_job()
    bq_client = MagicMock()
    bq_client.load_table_from_uri.return_value = job

    with patch("src.bigquery.bigquery.Client", return_value=bq_client) as mock_client_cls:
        run_load_job(
            gcs_uri="gs://bucket/path/*.parquet",
            project_id="my-project",
            dataset_id="billing",
            table_id="azure_cost_actual",
            schema_config=EA_USAGE_SCHEMA,
        )
    mock_client_cls.assert_called_once_with(project="my-project")

    args, kwargs = bq_client.load_table_from_uri.call_args
    assert args[0] == "gs://bucket/path/*.parquet"
    assert args[1] == "my-project.billing.azure_cost_actual"
    jc = kwargs["job_config"]
    assert jc.source_format == bigquery.SourceFormat.PARQUET
    assert jc.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE
    assert jc.time_partitioning.type_ == bigquery.TimePartitioningType.MONTH
    assert jc.time_partitioning.field == "BillingPeriodStartDate"
    assert jc.clustering_fields == ["Date", "SubscriptionName"]
    # explicit JSON schema is always applied
    bq_client.schema_from_json.assert_called_once_with(EA_USAGE_SCHEMA.schema_path)
    job.result.assert_called_once_with(timeout=3300)


def test_load_job_partition_decorator():
    job = _mock_job()
    bq_client = MagicMock()
    bq_client.load_table_from_uri.return_value = job

    with patch("src.bigquery.bigquery.Client", return_value=bq_client):
        run_load_job(
            gcs_uri="gs://bucket/path/*.parquet",
            project_id="my-project",
            dataset_id="billing",
            table_id="azure_cost_actual",
            partition_date=date(2026, 2, 1),
            schema_config=EA_USAGE_SCHEMA,
        )
    args, _ = bq_client.load_table_from_uri.call_args
    assert args[1] == "my-project.billing.azure_cost_actual$202602"


def test_load_job_focus_partition_and_cluster_fields():
    job = _mock_job()
    bq_client = MagicMock()
    bq_client.load_table_from_uri.return_value = job

    with patch("src.bigquery.bigquery.Client", return_value=bq_client):
        run_load_job(
            gcs_uri="gs://bucket/path/*.parquet",
            project_id="my-project",
            dataset_id="billing",
            table_id="azure_cost_focus",
            schema_config=FOCUS12_SCHEMA,
        )
    _, kwargs = bq_client.load_table_from_uri.call_args
    assert kwargs["job_config"].time_partitioning.field == "BillingPeriodStart"
    assert kwargs["job_config"].clustering_fields == ["ChargePeriodStart", "SubAccountName"]


def test_load_job_sets_cmek_when_provided():
    job = _mock_job()
    bq_client = MagicMock()
    bq_client.load_table_from_uri.return_value = job
    kms_key = "projects/p/locations/us/keyRings/r/cryptoKeys/k"

    with patch("src.bigquery.bigquery.Client", return_value=bq_client):
        run_load_job(
            gcs_uri="gs://bucket/path/*.parquet",
            project_id="my-project",
            dataset_id="billing",
            table_id="azure_cost_actual",
            kms_key_name=kms_key,
            schema_config=EA_USAGE_SCHEMA,
        )
    _, kwargs = bq_client.load_table_from_uri.call_args
    assert kwargs["job_config"].destination_encryption_configuration.kms_key_name == kms_key


def test_load_job_no_cmek_by_default():
    job = _mock_job()
    bq_client = MagicMock()
    bq_client.load_table_from_uri.return_value = job

    with patch("src.bigquery.bigquery.Client", return_value=bq_client):
        run_load_job(
            gcs_uri="gs://bucket/path/*.parquet",
            project_id="my-project",
            dataset_id="billing",
            table_id="azure_cost_actual",
            schema_config=EA_USAGE_SCHEMA,
        )
    _, kwargs = bq_client.load_table_from_uri.call_args
    assert kwargs["job_config"].destination_encryption_configuration is None


def test_load_job_raises_and_logs_on_error(caplog):
    job = _mock_job(error="Bad schema")
    bq_client = MagicMock()
    bq_client.load_table_from_uri.return_value = job

    with caplog.at_level(logging.ERROR, logger="src.bigquery"), \
         patch("src.bigquery.bigquery.Client", return_value=bq_client):
        with pytest.raises(RuntimeError, match="BigQuery load job failed"):
            run_load_job(
                gcs_uri="gs://bucket/path/*.parquet",
                project_id="my-project",
                dataset_id="billing",
                table_id="azure_cost_actual",
                partition_label="2026-04",
                schema_config=EA_USAGE_SCHEMA,
            )
    failed = [r for r in caplog.records if getattr(r, "log_event", None) == "bq.job.failed"]
    assert len(failed) == 1
    assert any("Bad schema" in str(e) for e in failed[0].errors)


def test_load_job_logs_submitted_and_complete(caplog):
    job = _mock_job()
    bq_client = MagicMock()
    bq_client.load_table_from_uri.return_value = job

    with caplog.at_level(logging.INFO, logger="src.bigquery"), \
         patch("src.bigquery.bigquery.Client", return_value=bq_client):
        run_load_job(
            gcs_uri="gs://bucket/path/*.parquet",
            project_id="my-project",
            dataset_id="billing",
            table_id="azure_cost_focus",
            run_id="20260423-123",
            export_name="focus-export",
            partition_label="2026-04",
            schema_config=FOCUS12_SCHEMA,
        )
    submitted = [r for r in caplog.records if getattr(r, "log_event", None) == "bq.job.submitted"]
    complete = [r for r in caplog.records if getattr(r, "log_event", None) == "bq.job.complete"]
    assert len(submitted) == 1 and submitted[0].run_id == "20260423-123"
    assert len(complete) == 1 and complete[0].output_rows == 42000
