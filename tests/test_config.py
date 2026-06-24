import pytest
import src.config as cfg


def _common(extra=None):
    base = {
        "AZURE_STORAGE_ACCOUNT_URL": "https://acct.blob.core.windows.net",
        "AZURE_STORAGE_CONTAINER": "exports",
        "AZURE_TENANT_ID": "tid",
        "AZURE_CLIENT_ID": "cid",
        "AZURE_CLIENT_SECRET": "secret-value",
        "GCS_BUCKET": "gcs-bucket",
        "BQ_PROJECT_ID": "my-project",
        "BQ_DATASET_ID": "billing",
        "EXPORT_NAME": "focus-export",
        "BQ_TABLE": "azure_cost_focus",
        "BILLING_SCHEMA": "focus",
    }
    if extra:
        base.update(extra)
    return base


def _setenv(monkeypatch, env):
    # clear any report/auth env that could leak from the real environment
    for k in (
        "PREVIOUS_MONTHS", "BQ_ENFORCE_SCHEMA", "AZURE_BLOB_ENDPOINT_URL",
        "AZURE_STORAGE_CONNECTION_STRING", "AZURE_STORAGE_SAS_TOKEN",
        "EXPORT_NAME", "BQ_TABLE", "BILLING_SCHEMA",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_config_loads_single_report(monkeypatch):
    _setenv(monkeypatch, _common())
    c = cfg.Config()
    assert c.blob_auth_mode == "service_principal"
    assert c.export_name == "focus-export"
    assert c.bq_table_id == "azure_cost_focus"
    assert c.billing_schema == "focus"
    assert c.previous_months == 1


def test_root_folder_and_table(monkeypatch):
    _setenv(monkeypatch, _common({
        "EXPORT_NAME": "act-export",
        "BQ_TABLE": "azure_cost_actual",
        "BILLING_SCHEMA": "actual",
        "AZURE_ROOT_FOLDER_PATH": "azure-cost/ea",
    }))
    c = cfg.Config()
    assert c.billing_schema == "actual"
    assert c.bq_table_id == "azure_cost_actual"
    assert c.azure_root_folder_path == "azure-cost/ea"


def test_missing_export_name_raises(monkeypatch):
    env = _common()
    env.pop("EXPORT_NAME")
    _setenv(monkeypatch, env)
    with pytest.raises(ValueError, match="EXPORT_NAME"):
        cfg.Config()


def test_missing_bq_table_raises(monkeypatch):
    env = _common()
    env.pop("BQ_TABLE")
    _setenv(monkeypatch, env)
    with pytest.raises(ValueError, match="BQ_TABLE"):
        cfg.Config()


def test_missing_billing_schema_raises(monkeypatch):
    env = _common()
    env.pop("BILLING_SCHEMA")
    _setenv(monkeypatch, env)
    with pytest.raises(ValueError, match="BILLING_SCHEMA"):
        cfg.Config()


def test_invalid_billing_schema_raises(monkeypatch):
    _setenv(monkeypatch, _common({"BILLING_SCHEMA": "bogus"}))
    with pytest.raises(ValueError, match="BILLING_SCHEMA must be one of"):
        cfg.Config()


def test_missing_required_account_url_raises(monkeypatch):
    env = _common()
    env.pop("AZURE_STORAGE_ACCOUNT_URL")
    _setenv(monkeypatch, env)
    with pytest.raises(ValueError, match="AZURE_STORAGE_ACCOUNT_URL"):
        cfg.Config()


def test_service_principal_missing_secret_raises(monkeypatch):
    env = _common()
    env.pop("AZURE_CLIENT_SECRET")
    _setenv(monkeypatch, env)
    with pytest.raises(ValueError, match="AZURE_CLIENT_SECRET"):
        cfg.Config()


def test_sas_token_auth_mode_no_sp_required(monkeypatch):
    env = _common({"AZURE_STORAGE_SAS_TOKEN": "?sv=...&sig=..."})
    env.pop("AZURE_TENANT_ID")
    env.pop("AZURE_CLIENT_ID")
    env.pop("AZURE_CLIENT_SECRET")
    _setenv(monkeypatch, env)
    c = cfg.Config()
    assert c.blob_auth_mode == "sas"


def test_connection_string_auth_mode(monkeypatch):
    env = _common({"AZURE_STORAGE_CONNECTION_STRING": "DefaultEndpointsProtocol=https;..."})
    _setenv(monkeypatch, env)
    c = cfg.Config()
    assert c.blob_auth_mode == "connection_string"


def test_endpoint_url_override(monkeypatch):
    _setenv(monkeypatch, _common({
        "AZURE_BLOB_ENDPOINT_URL": "https://acct.privatelink.blob.core.windows.net",
    }))
    c = cfg.Config()
    assert c.azure_blob_endpoint_url == "https://acct.privatelink.blob.core.windows.net"


def test_previous_months_parsed(monkeypatch):
    _setenv(monkeypatch, _common({"PREVIOUS_MONTHS": "3"}))
    c = cfg.Config()
    assert c.previous_months == 3


def test_repr_hides_secret(monkeypatch):
    _setenv(monkeypatch, _common())
    c = cfg.Config()
    assert "secret-value" not in repr(c)
