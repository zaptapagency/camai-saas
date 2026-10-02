"""Audit-log tests — the append-only trail itself, and its wiring to mutations.

Two layers:
* unit — ``record`` then ``list_entries`` round-trips, is tenant-scoped, and is
  returned newest-first;
* end-to-end — a protected admin mutation performed via TestClient leaves an entry
  retrievable through the ``GET /v1/tenants/{tenant_id}/audit`` endpoint.
"""

import os
import tempfile

from fastapi.testclient import TestClient

from app import audit
from app.audit import AuditLog


def _fresh_log():
    return AuditLog(os.path.join(tempfile.mkdtemp(), "audit.db"))


def test_record_then_list_round_trip():
    log = _fresh_log()
    entry = log.record(
        actor="alice",
        action="billing.account.upsert",
        tenant_id="t1",
        resource="tenant:t1:billing",
        meta={"plan": "growth"},
    )
    assert entry["id"] is not None
    assert entry["ts"].endswith("+00:00")  # UTC

    entries = log.list_entries("t1")
    assert len(entries) == 1
    got = entries[0]
    assert got["actor"] == "alice"
    assert got["action"] == "billing.account.upsert"
    assert got["resource"] == "tenant:t1:billing"
    assert got["meta"] == {"plan": "growth"}


def test_entries_are_tenant_scoped():
    log = _fresh_log()
    log.record(actor="a", action="x", tenant_id="t1")
    log.record(actor="b", action="y", tenant_id="t2")
    t1 = log.list_entries("t1")
    assert len(t1) == 1 and t1[0]["tenant_id"] == "t1"
    assert log.list_entries("t2")[0]["actor"] == "b"
    assert log.list_entries("nope") == []


def test_newest_first_ordering():
    log = _fresh_log()
    for i in range(5):
        log.record(actor=f"actor{i}", action="push", tenant_id="t1")
    entries = log.list_entries("t1")
    assert [e["actor"] for e in entries] == ["actor4", "actor3", "actor2", "actor1", "actor0"]


def test_list_respects_limit():
    log = _fresh_log()
    for i in range(10):
        log.record(actor=f"a{i}", action="push", tenant_id="t1")
    assert len(log.list_entries("t1", limit=3)) == 3


def test_module_level_default_and_configure():
    path = os.path.join(tempfile.mkdtemp(), "default.db")
    audit.configure(path)
    audit.record(actor="svc", action="fleet.config.push", tenant_id="t9")
    entries = audit.list_entries("t9")
    assert len(entries) == 1
    assert entries[0]["action"] == "fleet.config.push"


# --------------------------------------------------------------------------- #
# End-to-end: a protected mutation writes an audit entry readable via the API.
# --------------------------------------------------------------------------- #

def _client():
    os.environ["CAMAI_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
    import importlib
    import app.main as main
    importlib.reload(main)
    audit.configure(os.path.join(tempfile.mkdtemp(), "audit.db"))
    return TestClient(main.app)


def test_admin_mutation_is_audited_and_retrievable():
    client = _client()

    r = client.put(
        "/v1/admin/tenants/acme/billing",
        json={"stripe_customer_id": "cus_9", "stripe_subscription_item_id": "si_9", "plan": "enterprise"},
        headers={"X-CamAI-Role": "admin", "X-CamAI-Actor": "ops@acme.example"},
    )
    assert r.status_code == 200

    got = client.get("/v1/tenants/acme/audit", headers={"X-CamAI-Role": "admin"})
    assert got.status_code == 200
    entries = got.json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["actor"] == "ops@acme.example"
    assert entry["action"] == "billing.account.upsert"
    assert entry["tenant_id"] == "acme"
    assert entry["resource"] == "tenant:acme:billing"
    assert entry["meta"]["role"] == "admin"

    # The entry is scoped: a different tenant's trail does not see it.
    other = client.get("/v1/tenants/other/audit", headers={"X-CamAI-Role": "admin"})
    assert other.json() == []
