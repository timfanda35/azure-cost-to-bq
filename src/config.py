import os


# Report types this app understands. One Cost Management export — and therefore
# one Cloud Run Job — maps to exactly one of these via the BILLING_SCHEMA env var.
# The value selects the BigQuery schema in src.bigquery (SCHEMA_MAP) so this module
# stays free of the BigQuery client dependency; keep this tuple in sync with
# SCHEMA_MAP's keys (guarded by a test).
BILLING_SCHEMAS: tuple[str, ...] = ("actual", "amortized", "focus")


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

        # ── Report export (one export = one job) ─────────────────────────
        self.export_name = self._require("EXPORT_NAME")
        self.bq_table_id = self._require("BQ_TABLE_ID")
        self.billing_schema = self._require("BILLING_SCHEMA").strip()
        if self.billing_schema not in BILLING_SCHEMAS:
            raise ValueError(
                f"BILLING_SCHEMA must be one of {list(BILLING_SCHEMAS)}, "
                f"got {self.billing_schema!r}"
            )

        # ── Sync window ──────────────────────────────────────────────────
        self.previous_months = _env_int("PREVIOUS_MONTHS", default=1, minimum=0)

    def __repr__(self) -> str:
        return (
            f"Config(account_url={self.azure_storage_account_url!r}, "
            f"container={self.azure_storage_container!r}, "
            f"auth={self.blob_auth_mode!r}, "
            f"report={self.billing_schema!r}, table={self.bq_table_id!r}, "
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
