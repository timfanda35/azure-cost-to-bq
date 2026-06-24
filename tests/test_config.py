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
        "EXPORT_ACTUAL_NAME": "act-export",
        "EXPORT_AMORTIZED_NAME": "amort-export",
        "EXPORT_FOCUS_NAME": "focus-export",
    }
    if extra:
        base.update(extra)
    return base


def _setenv(monkeypatch, env):
    # clear any report/auth env that could leak from the real environment
    for k in (
        "REPORT", "PREVIOUS_MONTHS", "BQ_ENFORCE_SCHEMA", "AZURE_BLOB_ENDPOINT_URL",
        "AZURE_STORAGE_CONNECTION_STRING", "AZURE_STORAGE_SAS_TOKEN",
        "BQ_TABLE_ACTUAL", "BQ_TABLE_AMORTIZED", "BQ_TABLE_FOCUS",
        "EXPORT_ACTUAL_NAME", "EXPORT_AMORTIZED_NAME", "EXPORT_FOCUS_NAME",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_config_loads_three_exports(monkeypatch):
    _setenv(monkeypatch, _common())
    c = cfg.Config()
    assert c.blob_auth_mode == "service_principal"
    assert [e.report_type for e in c.exports] == ["actual", "amortized", "focus"]
    assert {e.bq_table_id for e in c.exports} == {
        "azure_cost_actual", "azure_cost_amortized", "azure_cost_focus"
    }
    assert c.previous_months == 1


def test_custom_table_names_and_root_folder(monkeypatch):
    _setenv(monkeypatch, _common({
        "BQ_TABLE_FOCUS": "focus_custom",
        "AZURE_ROOT_FOLDER_PATH": "azure-cost/ea",
    }))
    c = cfg.Config()
    focus = [e for e in c.exports if e.report_type == "focus"][0]
    assert focus.bq_table_id == "focus_custom"
    assert c.azure_root_folder_path == "azure-cost/ea"


def test_only_focus_configured(monkeypatch):
    env = _common()
    env.pop("EXPORT_ACTUAL_NAME")
    env.pop("EXPORT_AMORTIZED_NAME")
    _setenv(monkeypatch, env)
    c = cfg.Config()
    assert [e.report_type for e in c.exports] == ["focus"]


def test_no_exports_configured_raises(monkeypatch):
    env = _common()
    for k in ("EXPORT_ACTUAL_NAME", "EXPORT_AMORTIZED_NAME", "EXPORT_FOCUS_NAME"):
        env.pop(k, None)
    _setenv(monkeypatch, env)
    with pytest.raises(ValueError, match="No exports configured"):
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


def test_selected_exports_filters_by_arg(monkeypatch):
    _setenv(monkeypatch, _common())
    c = cfg.Config()
    sel = c.selected_exports("focus")
    assert [e.report_type for e in sel] == ["focus"]


def test_report_env_filter(monkeypatch):
    _setenv(monkeypatch, _common({"REPORT": "amortized"}))
    c = cfg.Config()
    assert [e.report_type for e in c.selected_exports()] == ["amortized"]
    # explicit arg overrides env filter
    assert [e.report_type for e in c.selected_exports("focus")] == ["focus"]


def test_invalid_report_filter_raises(monkeypatch):
    _setenv(monkeypatch, _common({"REPORT": "bogus"}))
    with pytest.raises(ValueError, match="REPORT must be one of"):
        cfg.Config()


def test_previous_months_parsed(monkeypatch):
    _setenv(monkeypatch, _common({"PREVIOUS_MONTHS": "3"}))
    c = cfg.Config()
    assert c.previous_months == 3


def test_repr_hides_secret(monkeypatch):
    _setenv(monkeypatch, _common())
    c = cfg.Config()
    assert "secret-value" not in repr(c)
