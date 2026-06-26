import json
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "src" / "bq_schema"


def _load(name):
    return json.loads((SCHEMA_DIR / name).read_text())


def _names(schema):
    return [c["name"] for c in schema]


def test_ea_usage_schema_column_count_and_anchors():
    schema = _load("azure-ea-usage.json")
    names = _names(schema)
    assert len(names) == 57
    # documented order (version 2024-08-01): first, partition, cluster, last
    assert names[0] == "InvoiceSectionName"
    assert names[-1] == "ResourceLocationNormalized"
    assert "BillingPeriodStartDate" in names  # partition field
    assert "Date" in names and "SubscriptionName" in names  # cluster fields
    # no duplicate columns
    assert len(names) == len(set(names))


def test_focus_schema_column_count_and_anchors():
    schema = _load("azure-focus-1.2.json")
    names = _names(schema)
    assert len(names) == 105
    assert names[0] == "BilledCost"
    assert names[-1] == "x_SkuTier"
    assert "BillingPeriodStart" in names  # partition field
    assert "ChargePeriodStart" in names and "SubAccountName" in names  # cluster fields
    assert len(names) == len(set(names))


def test_schema_entries_have_required_keys():
    for fname in ("azure-ea-usage.json", "azure-focus-1.2.json"):
        for entry in _load(fname):
            assert set(entry) >= {"name", "mode", "type"}
            assert entry["mode"] in ("NULLABLE", "REQUIRED", "REPEATED")
            assert entry["type"] in ("STRING", "FLOAT", "DATE", "TIMESTAMP", "BOOL", "INTEGER", "NUMERIC", "BIGNUMERIC")


def test_partition_and_cluster_fields_have_expected_types():
    ea = {c["name"]: c["type"] for c in _load("azure-ea-usage.json")}
    assert ea["BillingPeriodStartDate"] == "TIMESTAMP"
    assert ea["CostInBillingCurrency"] == "BIGNUMERIC"
    assert ea["PayGPrice"] == "BIGNUMERIC"
    assert ea["IsAzureCreditEligible"] == "BOOL"
    assert ea["InvoiceSectionId"] == "INTEGER"

    focus = {c["name"]: c["type"] for c in _load("azure-focus-1.2.json")}
    assert focus["BillingPeriodStart"] == "TIMESTAMP"
    assert focus["BilledCost"] == "BIGNUMERIC"
    assert focus["EffectiveCost"] == "BIGNUMERIC"
    assert focus["x_InvoiceSectionId"] == "STRING"
