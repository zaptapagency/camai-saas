"""Tests for zero-touch registration + the device registry.

Split into two layers: the pure ``DeviceRegistry`` (token lifecycle, idempotency,
anti-spoof rules) with no HTTP or crypto, and the ``router`` wired into a throwaway
FastAPI app with the registry dependency overridden to a tmp-file DB. A crypto path
(real CSR -> fingerprint device_id) is exercised only when ``cryptography`` is
installed, so the default suite runs without it.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.device_registry import DeviceRegistry, get_registry, router


def _registry(tmp_path) -> DeviceRegistry:
    return DeviceRegistry(tmp_path / "devices.db")


def _client(reg: DeviceRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_registry] = lambda: reg
    return TestClient(app)


# --- Registry unit layer --------------------------------------------------- #

def test_token_binds_device_to_tenant(tmp_path):
    reg = _registry(tmp_path)
    tok = reg.create_enrollment_token("acme", "store-7")["token"]
    dev = reg.register_device(tok, device_id="dev_abc", public_key_pem="PUB")
    assert dev["tenant_id"] == "acme"
    assert dev["site_id"] == "store-7"
    assert dev["status"] == "active"


def test_registration_is_idempotent_for_same_device(tmp_path):
    reg = _registry(tmp_path)
    tok = reg.create_enrollment_token("acme", "s1")["token"]
    d1 = reg.register_device(tok, device_id="dev_abc")
    d2 = reg.register_device(tok, device_id="dev_abc")  # retry after dropped resp
    assert d1["device_id"] == d2["device_id"] == "dev_abc"


def test_token_reuse_by_other_device_rejected(tmp_path):
    reg = _registry(tmp_path)
    tok = reg.create_enrollment_token("acme", "s1")["token"]
    reg.register_device(tok, device_id="dev_abc")
    with pytest.raises(Exception):
        reg.register_device(tok, device_id="dev_other")


def test_expired_token_rejected(tmp_path):
    reg = _registry(tmp_path)
    tok = reg.create_enrollment_token("acme", "s1", ttl_seconds=-1)["token"]
    with pytest.raises(Exception):
        reg.register_device(tok, device_id="dev_abc")


def test_unknown_token_rejected(tmp_path):
    reg = _registry(tmp_path)
    with pytest.raises(Exception):
        reg.register_device("nope", device_id="dev_abc")


# --- HTTP layer ------------------------------------------------------------ #

def test_register_endpoint_zero_touch(tmp_path):
    reg = _registry(tmp_path)
    tok = reg.create_enrollment_token("acme", "s1")["token"]
    client = _client(reg)
    r = client.post("/v1/devices/register",
                    json={"enrollment_token": tok, "device_id": "dev_abc"})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "acme"
    assert body["status"] == "active"

    status = client.get("/v1/devices/dev_abc").json()
    assert status["site_id"] == "s1"


def test_register_requires_key_material(tmp_path):
    reg = _registry(tmp_path)
    tok = reg.create_enrollment_token("acme", "s1")["token"]
    client = _client(reg)
    r = client.post("/v1/devices/register", json={"enrollment_token": tok})
    assert r.status_code == 400


def test_unknown_device_status_404(tmp_path):
    client = _client(_registry(tmp_path))
    assert client.get("/v1/devices/ghost").status_code == 404


def test_bad_token_via_http_is_401(tmp_path):
    client = _client(_registry(tmp_path))
    r = client.post("/v1/devices/register",
                    json={"enrollment_token": "bad", "device_id": "dev_abc"})
    assert r.status_code == 401


# --- Crypto path (only when cryptography is available) --------------------- #

def test_register_derives_device_id_from_csr(tmp_path):
    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "unused")]))
        .sign(key, hashes.SHA256())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()

    reg = _registry(tmp_path)
    tok = reg.create_enrollment_token("acme", "s1")["token"]
    client = _client(reg)
    r = client.post("/v1/devices/register",
                    json={"enrollment_token": tok, "csr": csr_pem, "device_id": "ignored"})
    assert r.status_code == 200
    # The derived id is the key fingerprint, NOT the client-supplied "ignored".
    assert r.json()["device_id"].startswith("dev_")
    assert r.json()["device_id"] != "ignored"
