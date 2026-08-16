import os

from src.config import BILLING_SCHEMAS


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise ValueError(f"Required env var {name!r} is not set or is empty")
    return val


class SourceConfig:
    """One Azure Cost Management export source, read from env vars under ``prefix``.

    Attribute names match ``src.config.Config``'s Azure fields so an instance can be
    passed directly to ``src.pipeline._build_source`` without any adapter.
    """

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.azure_storage_account_url = _require(f"{prefix}AZURE_STORAGE_ACCOUNT_URL")
        self.azure_blob_endpoint_url = os.environ.get(f"{prefix}AZURE_BLOB_ENDPOINT_URL") or None
        self.azure_storage_container = _require(f"{prefix}AZURE_STORAGE_CONTAINER")
        self.azure_root_folder_path = os.environ.get(f"{prefix}AZURE_ROOT_FOLDER_PATH", "")

        self.azure_storage_connection_string = (
            os.environ.get(f"{prefix}AZURE_STORAGE_CONNECTION_STRING") or None
        )
        self.azure_storage_sas_token = os.environ.get(f"{prefix}AZURE_STORAGE_SAS_TOKEN") or None
        self.azure_tenant_id = os.environ.get(f"{prefix}AZURE_TENANT_ID") or None
        self.azure_client_id = os.environ.get(f"{prefix}AZURE_CLIENT_ID") or None
        self.azure_client_secret = os.environ.get(f"{prefix}AZURE_CLIENT_SECRET") or None

        # Auth mode: connection string > SAS token > service principal.
        if self.azure_storage_connection_string:
            self.blob_auth_mode = "connection_string"
        elif self.azure_storage_sas_token:
            self.blob_auth_mode = "sas"
        else:
            self.blob_auth_mode = "service_principal"
            missing = [
                name for name, val in (
                    (f"{prefix}AZURE_TENANT_ID", self.azure_tenant_id),
                    (f"{prefix}AZURE_CLIENT_ID", self.azure_client_id),
                    (f"{prefix}AZURE_CLIENT_SECRET", self.azure_client_secret),
                )
                if not val
            ]
            if missing:
                raise ValueError(
                    f"Blob auth for {prefix!r} source requires a connection string, a SAS "
                    f"token, or a service principal. Missing for service principal: "
                    f"{', '.join(missing)}"
                )

        self.export_name = _require(f"{prefix}EXPORT_NAME")

    @classmethod
    def from_env(cls, prefix: str) -> "SourceConfig":
        return cls(prefix)


class MergeTargetConfig:
    """Shared GCS staging + BigQuery destination for a merge run.

    Read unprefixed, so the same GCS_*/BQ_*/BILLING_SCHEMA values already set for
    the normal single-source job can be reused as-is.
    """

    def __init__(self):
        self.gcs_bucket = _require("GCS_BUCKET")
        self.gcs_destination_prefix = os.environ.get("GCS_DESTINATION_PREFIX", "")

        self.bq_project_id = _require("BQ_PROJECT_ID")
        self.bq_dataset_id = _require("BQ_DATASET_ID")
        self.bq_table_id = _require("BQ_TABLE_ID")
        self.bq_cmek_key_name = os.environ.get("BQ_CMEK_KEY_NAME") or None

        self.billing_schema = _require("BILLING_SCHEMA").strip()
        if self.billing_schema not in BILLING_SCHEMAS:
            raise ValueError(
                f"BILLING_SCHEMA must be one of {list(BILLING_SCHEMAS)}, "
                f"got {self.billing_schema!r}"
            )

    @classmethod
    def from_env(cls) -> "MergeTargetConfig":
        return cls()
