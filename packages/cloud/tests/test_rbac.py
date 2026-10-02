"""RBAC tests — the role gate on privileged endpoints, via TestClient.

Exercises the header seam end to end: a viewer (or a caller with no role header)
is refused an admin mutation with 403; an admin is let through; the audit read is
gated at analyst, so an analyst can read it but a viewer cannot.
"""

import os
import tempfile

from fastapi.testclient import TestClient

from app import audit
from app.rbac import Role, _coerce


def _client():
    os.environ["CAMAI_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
    import importlib
    import app.main as main
    importlib.reload(main)
    # Isolate the audit trail for this test to a fresh file.
    audit.configure(os.path.join(tempfile.mkdtemp(), "audit.db"))
    return TestClient(main.app)


_BILLING = "/v1/admin/tenants/t1/billing"
_BODY = {"stripe_customer_id": "cus_1", "stripe_subscription_item_id": "si_1", "plan": "growth"}


def test_role_ranks_are_ordered():
    assert Role.viewer.rank < Role.analyst.rank < Role.admin.rank < Role.owner.rank


def test_unknown_or_missing_role_fails_closed_to_viewer():
    assert _coerce(None) is Role.viewer
    assert _coerce("") is Role.viewer
    assert _coerce("superuser") is Role.viewer
    assert _coerce("ADMIN") is Role.admin  # case-insensitive


def test_viewer_is_denied_admin_mutation():
    client = _client()
    # Explicit viewer.
    r = client.put(_BILLING, json=_BODY, headers={"X-CamAI-Role": "viewer"})
    assert r.status_code == 403
    # Absent role header defaults to viewer -> also denied.
    r2 = client.put(_BILLING, json=_BODY)
    assert r2.status_code == 403


def test_admin_passes_the_mutation():
    client = _client()
    r = client.put(_BILLING, json=_BODY, headers={"X-CamAI-Role": "admin"})
    assert r.status_code == 200
    assert r.json()["plan"] == "growth"


def test_owner_subsumes_admin():
    client = _client()
    r = client.put(_BILLING, json=_BODY, headers={"X-CamAI-Role": "owner"})
    assert r.status_code == 200


def test_analyst_can_read_audit_but_viewer_cannot():
    client = _client()
    # Seed one audit entry via an admin mutation.
    client.put(_BILLING, json=_BODY, headers={"X-CamAI-Role": "admin"})

    ok = client.get("/v1/tenants/t1/audit", headers={"X-CamAI-Role": "analyst"})
    assert ok.status_code == 200
    assert isinstance(ok.json(), list)

    denied = client.get("/v1/tenants/t1/audit", headers={"X-CamAI-Role": "viewer"})
    assert denied.status_code == 403
    # Absent header -> viewer -> denied.
    assert client.get("/v1/tenants/t1/audit").status_code == 403


def test_fleet_config_push_is_admin_gated():
    client = _client()
    body = {"target_fps": 6.0}
    denied = client.put("/v1/devices/d1/config", params={"tenant_id": "t1"},
                        json=body, headers={"X-CamAI-Role": "analyst"})
    assert denied.status_code == 403
    allowed = client.put("/v1/devices/d1/config", params={"tenant_id": "t1"},
                        json=body, headers={"X-CamAI-Role": "admin"})
    assert allowed.status_code == 200
