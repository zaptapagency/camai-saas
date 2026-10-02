"""Round-trip tests for the admin billing router via TestClient.

The store dependency is overridden with a real SQLite Store so PUT then GET exercise
the actual store.upsert_billing_account / get_billing_account path.
"""

import os
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.store import Store
from app.billing_admin_router import router, get_billing_store


def _client():
    store = Store(os.path.join(tempfile.mkdtemp(), "admin.db"))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_billing_store] = lambda: store
    # The billing upsert is now admin-gated (app.rbac); authenticate the test
    # client as admin by default so these round-trip tests exercise the store.
    return TestClient(app, headers={"X-CamAI-Role": "admin"}), store


def test_put_then_get_round_trip():
    client, _ = _client()

    r = client.put("/v1/admin/tenants/t1/billing", json={
        "stripe_customer_id": "cus_1",
        "stripe_subscription_item_id": "si_1",
        "plan": "growth",
    })
    assert r.status_code == 200
    assert r.json() == {
        "tenant_id": "t1", "stripe_customer_id": "cus_1",
        "stripe_subscription_item_id": "si_1", "plan": "growth",
    }

    g = client.get("/v1/admin/tenants/t1/billing")
    assert g.status_code == 200
    assert g.json()["plan"] == "growth"
    assert g.json()["stripe_customer_id"] == "cus_1"


def test_put_is_idempotent_upsert():
    client, store = _client()
    client.put("/v1/admin/tenants/t1/billing", json={
        "stripe_customer_id": "cus_1", "stripe_subscription_item_id": "si_1", "plan": "starter"})
    client.put("/v1/admin/tenants/t1/billing", json={
        "stripe_customer_id": "cus_2", "stripe_subscription_item_id": "si_2", "plan": "growth"})

    account = store.get_billing_account("t1")
    assert account["stripe_customer_id"] == "cus_2"
    assert account["plan"] == "growth"


def test_subscription_item_optional_for_meter_billing():
    client, _ = _client()
    r = client.put("/v1/admin/tenants/t1/billing", json={
        "stripe_customer_id": "cus_1", "plan": "enterprise"})
    assert r.status_code == 200
    assert r.json()["stripe_subscription_item_id"] == ""


def test_get_unknown_tenant_404():
    client, _ = _client()
    r = client.get("/v1/admin/tenants/nope/billing")
    assert r.status_code == 404
