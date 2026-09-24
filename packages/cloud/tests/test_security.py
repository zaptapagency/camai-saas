"""Tests for cert-derived device authentication (app.security).

The whole point of this module is that the tenant comes from the *verified cert*,
never the body. These tests exercise DN parsing, the mTLS header path, the dev
fallback (and its production lockout), and tenant reconciliation. No native crypto
is required for any of this -- the proxy has already parsed the cert into headers.
"""

import os

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import security
from app.security import (
    DevicePrincipal,
    current_device,
    identity_from_dn,
    parse_dn,
    resolve_tenant,
)


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/whoami")
    def whoami(device: DevicePrincipal = Depends(current_device)) -> dict:
        return device

    return app


def test_parse_dn_openssl_oneline():
    attrs = parse_dn("/CN=dev_abc/O=tenant1")
    assert attrs["CN"] == "dev_abc"
    assert attrs["O"] == "tenant1"


def test_parse_dn_rfc4514():
    attrs = parse_dn("CN=dev_abc,O=tenant1")
    assert attrs["CN"] == "dev_abc"
    assert attrs["O"] == "tenant1"


def test_identity_from_dn_maps_cn_and_o():
    p = identity_from_dn("CN=dev_xyz,O=t2")
    assert p.device_id == "dev_xyz"
    assert p.tenant_id == "t2"


def test_mtls_headers_authenticate():
    client = TestClient(_app())
    r = client.get("/whoami", headers={
        "X-SSL-Client-Verify": "SUCCESS",
        "X-SSL-Client-S-DN": "CN=dev_1,O=acme",
    })
    assert r.status_code == 200
    assert r.json() == {"tenant_id": "acme", "device_id": "dev_1"}


def test_mtls_rejects_failed_verification():
    client = TestClient(_app())
    r = client.get("/whoami", headers={
        "X-SSL-Client-Verify": "FAILED:self signed certificate",
        "X-SSL-Client-S-DN": "CN=dev_1,O=acme",
    })
    assert r.status_code == 401


def test_dev_fallback_allowed_by_default(monkeypatch):
    monkeypatch.delenv("CAMAI_REQUIRE_MTLS", raising=False)
    client = TestClient(_app())
    r = client.get("/whoami", headers={"X-Device-Tenant": "t1", "X-Device-Id": "d1"})
    assert r.status_code == 200
    assert r.json() == {"tenant_id": "t1", "device_id": "d1"}


def test_dev_fallback_blocked_when_mtls_required(monkeypatch):
    monkeypatch.setenv("CAMAI_REQUIRE_MTLS", "1")
    client = TestClient(_app())
    r = client.get("/whoami", headers={"X-Device-Tenant": "t1", "X-Device-Id": "d1"})
    assert r.status_code == 401


def test_no_identity_is_unauthorized(monkeypatch):
    monkeypatch.delenv("CAMAI_REQUIRE_MTLS", raising=False)
    client = TestClient(_app())
    assert client.get("/whoami").status_code == 401


def test_resolve_tenant_prefers_cert():
    # Cert carries the tenant -> body value is ignored on match, fatal on mismatch.
    dev = DevicePrincipal(tenant_id="acme", device_id="d1")
    assert resolve_tenant("acme", dev) == "acme"
    with pytest.raises(Exception):
        resolve_tenant("evil", dev)


def test_resolve_tenant_zero_touch_uses_body():
    # No tenant in the cert yet (pre-binding): fall back to the body value.
    dev = DevicePrincipal(tenant_id=None, device_id="d1")
    assert resolve_tenant("acme", dev) == "acme"


def test_forwarded_cert_reconstructs_pem():
    # Space-collapsed PEM header form is normalized back to line breaks.
    collapsed = "-----BEGIN CERTIFICATE----- AAAA BBBB -----END CERTIFICATE-----"
    pem = security._decode_forwarded_cert(collapsed)
    assert pem is not None
    assert pem.startswith("-----BEGIN CERTIFICATE-----\n")
    assert pem.strip().endswith("-----END CERTIFICATE-----")
