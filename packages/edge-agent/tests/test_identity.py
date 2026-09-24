"""Tests for per-device identity (camai_edge.identity).

The pure parts (config model, fingerprint-based id derivation) run everywhere.
The key/cert generation path needs the ``cryptography`` package and is skipped
when it is absent, so the default suite stays dependency-light.
"""

import hashlib

import pytest

from camai_edge.identity import (
    DeviceIdentityConfig,
    device_id_from_public_key_der,
    ensure_identity,
)


def test_config_defaults():
    cfg = DeviceIdentityConfig()
    assert cfg.key_filename == "device.key"
    assert cfg.cert_filename == "device.crt"
    assert cfg.validity_days > 0


def test_device_id_is_stable_fingerprint():
    spki = b"some-public-key-bytes"
    expected = "dev_" + hashlib.sha256(spki).hexdigest()[:32]
    assert device_id_from_public_key_der(spki) == expected
    # Deterministic: same input -> same id.
    assert device_id_from_public_key_der(spki) == device_id_from_public_key_der(spki)


def test_ensure_identity_generates_and_is_idempotent(tmp_path):
    pytest.importorskip("cryptography")
    d = tmp_path / "device"

    ident = ensure_identity(d)
    assert ident.device_id.startswith("dev_")
    assert ident.key_path.exists()
    assert ident.cert_path.exists()
    assert ident.csr_path.exists()

    # A second call reuses the key -> the id is stable across "reboots".
    ident2 = ensure_identity(d)
    assert ident2.device_id == ident.device_id
    assert ident2.key_path.read_bytes() == ident.key_path.read_bytes()


def test_cert_and_csr_carry_device_id(tmp_path):
    pytest.importorskip("cryptography")
    from cryptography import x509

    ident = ensure_identity(tmp_path / "device")

    cert = x509.load_pem_x509_certificate(ident.cert_pem().encode())
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == ident.device_id

    csr = x509.load_pem_x509_csr(ident.csr_pem().encode())
    assert csr.is_signature_valid


def test_edge_and_cloud_derive_same_id(tmp_path):
    """The device id the edge computes must equal what the cloud re-derives.

    This is the contract that lets the cloud trust a presented cert's identity.
    """
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization

    ident = ensure_identity(tmp_path / "device")
    pub = serialization.load_pem_public_key(ident.public_key_pem().encode())
    spki = pub.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert device_id_from_public_key_der(spki) == ident.device_id
