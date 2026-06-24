import os
from dataclasses import dataclass


# Report types this app understands. Each maps to its env var names + default
# BigQuery table. The schema for each report type is resolved in src.bigquery
# (SCHEMA_MAP) so this module stays free of the BigQuery client dependency.
REPORT_TYPES: dict[str, dict] = {
    "actual": {
        "export_env": "EXPORT_ACTUAL_NAME",
        "table_env": "BQ_TABLE_ACTUAL",
        "default_table": "azure_cost_actual",
    },
    "amortized": {
        "export_env": "EXPORT_AMORTIZED_NAME",
        "table_env": "BQ_TABLE_AMORTIZED",
        "default_table": "azure_cost_amortized",
    },
    "focus": {
        "export_env": "EXPORT_FOCUS_NAME",
        "table_env": "BQ_TABLE_FOCUS",
        "default_table": "azure_cost_focus",
    },
}


@dataclass(frozen=True)
class ExportSpec:
    """One Cost Management export to sync: its report type, the Azure export
    name (a path segment in blob storage), and the destination BigQuery table."""
    report_type: str
    export_name: str
    bq_table_id: str


class Config:
    def __init__(self):
        # ── Azure blob auth ──────────────────────────────────────────────
        self.azure_storage_account_url = self._require("AZURE_STORAGE_ACCOUNT_URL")
        self.azure_blob_endpoint_url = os.environ.get("AZURE_BLOB_ENDPOINT_URL") or None
        self.azure_storage_container = self._require("AZURE_STORAGE_CONTAINER")
        self.azure_root_folder_path = os.environ.get("AZURE_ROOT_FOLDER_PATH", "")

        self.azure_storage_connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or None
        self.azure_storage_sas_token = os.environ.get("AZURE_STORAGE_SAS_TOKEN") or None
        self.azure_tenant_id = os.environ.get("AZURE_TENANT_ID") or None
        self.azure_client_id = os.environ.get("AZURE_CLIENT_ID") or None
        self.azure_client_secret = os.environ.get("AZURE_CLIENT_SECRET") or None

        # Auth mode: connection string > SAS token > service principal.
        if self.azure_storage_connection_string:
            self.blob_auth_mode = "connection_string"
        elif self.azure_storage_sas_token:
            self.blob_auth_mode = "sas"
        else:
            self.blob_auth_mode = "service_principal"
            missing = [
                name for name, val in (
                    ("AZURE_TENANT_ID", self.azure_tenant_id),
                    ("AZURE_CLIENT_ID", self.azure_client_id),
                    ("AZURE_CLIENT_SECRET", self.azure_client_secret),
                )
                if not val
            ]
            if missing:
                raise ValueError(
                    "Blob auth requires a connection string, a SAS token, or a service "
                    f"principal. Missing for service principal: {', '.join(missing)}"
                )

        # ── GCS staging ──────────────────────────────────────────────────
        self.gcs_bucket = self._require("GCS_BUCKET")
        self.gcs_destination_prefix = os.environ.get("GCS_DESTINATION_PREFIX", "")

        # ── BigQuery target ──────────────────────────────────────────────
        self.bq_project_id = self._require("BQ_PROJECT_ID")
        self.bq_dataset_id = self._require("BQ_DATASET_ID")
        self.bq_cmek_key_name = os.environ.get("BQ_CMEK_KEY_NAME") or None
        self.bq_enforce_schema = _env_bool("BQ_ENFORCE_SCHEMA", default=False)

        # ── Report exports (configured = export name present) ────────────
        self.exports: list[ExportSpec] = []
        for report_type, meta in REPORT_TYPES.items():
            export_name = os.environ.get(meta["export_env"], "").strip()
            if not export_name:
                continue
            table_id = os.environ.get(meta["table_env"], "").strip() or meta["default_table"]
            self.exports.append(ExportSpec(report_type, export_name, table_id))
        if not self.exports:
            raise ValueError(
                "No exports configured. Set at least one of: "
                + ", ".join(m["export_env"] for m in REPORT_TYPES.values())
            )

        # ── Sync window / runtime filter ─────────────────────────────────
        self.previous_months = _env_int("PREVIOUS_MONTHS", default=1, minimum=0)
        self.report_filter = (os.environ.get("REPORT") or "").strip() or None
        if self.report_filter and self.report_filter not in REPORT_TYPES:
            raise ValueError(
                f"REPORT must be one of {list(REPORT_TYPES)}, got {self.report_filter!r}"
            )

    def selected_exports(self, report: str | None = None) -> list[ExportSpec]:
        """Exports to process this run, narrowed by an explicit ``report`` arg
        (CLI) or the REPORT env filter. ``report`` takes precedence."""
        chosen = report or self.report_filter
        if chosen is None:
            return list(self.exports)
        if chosen not in REPORT_TYPES:
            raise ValueError(f"Unknown report {chosen!r}; expected one of {list(REPORT_TYPES)}")
        return [e for e in self.exports if e.report_type == chosen]

    def __repr__(self) -> str:
        return (
            f"Config(account_url={self.azure_storage_account_url!r}, "
            f"container={self.azure_storage_container!r}, "
            f"auth={self.blob_auth_mode!r}, "
            f"reports={[e.report_type for e in self.exports]!r}, "
            f"gcs_bucket={self.gcs_bucket!r})"
        )

    @staticmethod
    def _require(name: str) -> str:
        val = os.environ.get(name)
        if not val:
            raise ValueError(f"Required env var {name!r} is not set or is empty")
        return val


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, *, default: int, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}")
    if minimum is not None and val < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {val}")
    return val
