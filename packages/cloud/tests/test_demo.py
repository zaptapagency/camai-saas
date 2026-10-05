"""Tests for the time-boxed demo-session flow."""

import os
import tempfile

from fastapi.testclient import TestClient


def _client():
    os.environ["CAMAI_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
    os.environ["CAMAI_DEMO_DB"] = os.path.join(tempfile.mkdtemp(), "demo.db")
    import importlib
    import app.demo as demo
    import app.main as main
    importlib.reload(demo)
    importlib.reload(main)
    return TestClient(main.app), demo


def test_start_issues_token_scoped_to_demo_tenant():
    client, demo = _client()
    r = client.post("/v1/demo/start")
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert body["tenant_id"] == demo.DEMO_TENANT
    assert body["expired"] is False
    assert body["seconds_remaining"] > 0


def test_default_window_is_three_hours():
    client, _ = _client()
    body = client.post("/v1/demo/start").json()
    # 3h = 10800s; allow a small slack for execution time.
    assert 10700 < body["seconds_remaining"] <= 10800


def test_session_lookup_returns_countdown():
    client, _ = _client()
    token = client.post("/v1/demo/start").json()["token"]
    r = client.get("/v1/demo/session", params={"token": token})
    assert r.status_code == 200
    assert r.json()["token"] == token
    assert r.json()["seconds_remaining"] > 0


def test_unknown_token_404():
    client, _ = _client()
    r = client.get("/v1/demo/session", params={"token": "nope"})
    assert r.status_code == 404


def test_expired_token_reports_gone():
    client, demo = _client()
    # A session minted with a zero-length window is immediately expired.
    expired = demo.start(hours=0)
    r = client.get("/v1/demo/session", params={"token": expired["token"]})
    assert r.status_code == 410
    assert r.json()["expired"] is True


def test_custom_window_via_env():
    os.environ["CAMAI_DEMO_HOURS"] = "1"
    client, _ = _client()
    body = client.post("/v1/demo/start").json()
    assert 3500 < body["seconds_remaining"] <= 3600
    del os.environ["CAMAI_DEMO_HOURS"]
