import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_run_returns_200_on_success(client):
    result = {"run_id": "20260623-1", "periods_loaded": 2, "periods_skipped": 0}
    with patch("main.run_pipeline", return_value=result) as mock_pipeline:
        resp = client.post("/run")
    assert resp.status_code == 200
    assert resp.json() == result
    mock_pipeline.assert_called_once_with(partition=None)


def test_run_passes_partition(client):
    with patch("main.run_pipeline", return_value={"run_id": "x"}) as mock_pipeline:
        resp = client.post("/run", json={"partition": "2026-05"})
    assert resp.status_code == 200
    mock_pipeline.assert_called_once_with(partition="2026-05")


def test_run_returns_500_on_error(client):
    with patch("main.run_pipeline", side_effect=RuntimeError("something broke")):
        resp = client.post("/run")
    assert resp.status_code == 500
    assert resp.json()["error"] == "something broke"


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
