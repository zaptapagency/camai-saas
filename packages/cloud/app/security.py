"""Device authentication — deriving *who* a request is from, not what it claims.

The cloud must never trust the request body for the tenant. A compromised or
buggy edge box could put any ``tenant_id`` in an ``EventBatch`` and, if we
believed it, write into another customer's data. Instead the tenant/device is
derived from the **verified client certificate** presented during the mTLS
handshake, and the body ``tenant_id`` is only ever a cross-check.

Trust model
-----------
TLS (and mTLS client-cert verification) is terminated by a reverse proxy in front
of this app (nginx / Envoy / an ALB with mutual TLS). The proxy:

1. verifies the client certificate against the device CA, and
2. forwards the verified identity to us in request headers, having **stripped any
   client-supplied copies of those same headers** so a device cannot forge them.

That header-stripping at the edge is the entire basis for trusting the headers
here. This module therefore reads, in order of preference:

* ``X-SSL-Client-Verify``  -- must be ``SUCCESS`` (nginx ``$ssl_client_verify``);
* ``X-SSL-Client-S-DN``    -- the verified subject DN (``CN=...,O=...``);
* ``X-Client-Cert``        -- optionally the full URL/PEM-encoded client cert, from
  which we can re-derive the fingerprint-based device id (defence in depth).

Identity encoding convention (matches ``camai_edge.identity``):

* ``CN`` = ``device_id`` (a public-key fingerprint, e.g. ``dev_ab12...``);
* ``O``  = ``tenant_id`` **iff** the cert was CA-issued with the tenant baked in.
  During zero-touch a device's self-signed cert does not yet know its tenant, so
  the tenant is resolved from the device registry binding instead.

Dev fallback
------------
Local development and the current pilot run without a TLS-terminating proxy, so a
fallback keyed off the existing ``X-Device-Tenant`` header (plus an optional
``X-Device-Id``) is supported. It is **disabled** by refusing to fall back when
``CAMAI_REQUIRE_MTLS`` is truthy, so production can hard-require real cert auth.
"""

from __future__ import annotations

import os
from typing import Optional
from urllib.parse import unquote

from fastapi import Header, HTTPException


# Header names a TLS-terminating proxy is expected to set from the verified cert.
H_CLIENT_VERIFY = "X-SSL-Client-Verify"
H_CLIENT_DN = "X-SSL-Client-S-DN"
H_CLIENT_CERT = "X-Client-Cert"
# Dev-fallback headers (the seam that already exists in main.py).
H_DEV_TENANT = "X-Device-Tenant"
H_DEV_DEVICE = "X-Device-Id"


class DevicePrincipal(dict):
    """The authenticated caller: ``{"tenant_id": ..., "device_id": ...}``.

    A plain ``dict`` subclass so it serializes trivially and callers can do
    ``device["tenant_id"]`` while still getting attribute-style access.
    """

    @property
    def tenant_id(self) -> Optional[str]:
        return self.get("tenant_id")

    @property
    def device_id(self) -> Optional[str]:
        return self.get("device_id")


def _mtls_required() -> bool:
    """Whether the dev fallback is forbidden (production hardening switch)."""
    return os.environ.get("CAMAI_REQUIRE_MTLS", "").lower() in {"1", "true", "yes", "on"}


def parse_dn(dn: str) -> dict[str, str]:
    """Parse an X.509 subject DN string into a ``{attr: value}`` mapping.

    Handles both ``/CN=x/O=y`` (OpenSSL/nginx oneline) and ``CN=x,O=y`` (RFC 4514)
    forms. Only the last value wins for a repeated attribute, which is all we need
    for the single-CN/single-O convention. Kept crypto-free so the common path
    (proxy already parsed the cert) needs no native deps.
    """
    text = dn.strip()
    if not text:
        return {}
    # OpenSSL oneline form starts with '/'; split on '/', else on ','.
    parts = text.lstrip("/").split("/") if text.startswith("/") else text.split(",")
    out: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip().upper()] = v.strip()
    return out


def identity_from_dn(dn: str) -> DevicePrincipal:
    """Derive a principal from a verified subject DN (``CN`` -> device, ``O`` -> tenant)."""
    attrs = parse_dn(dn)
    return DevicePrincipal(
        tenant_id=attrs.get("O") or attrs.get("ORGANIZATIONNAME"),
        device_id=attrs.get("CN") or attrs.get("COMMONNAME"),
    )


def device_id_from_cert_pem(pem: str) -> Optional[str]:
    """Re-derive the fingerprint-based device id from a client cert PEM.

    Defence in depth: even if the proxy's DN header disagreed, the device id is a
    fingerprint of the public key and can be recomputed here. Uses ``cryptography``
    lazily so importing this module never requires it; returns ``None`` if the dep
    is missing or the PEM cannot be parsed.
    """
    try:
        import hashlib

        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
    except ImportError:  # pragma: no cover - only without the optional dep
        return None
    try:
        cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
        spki = cert.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except Exception:
        return None
    return f"dev_{hashlib.sha256(spki).hexdigest()[:32]}"


def current_device(
    x_ssl_client_verify: Optional[str] = Header(default=None, alias=H_CLIENT_VERIFY),
    x_ssl_client_s_dn: Optional[str] = Header(default=None, alias=H_CLIENT_DN),
    x_client_cert: Optional[str] = Header(default=None, alias=H_CLIENT_CERT),
    x_device_tenant: Optional[str] = Header(default=None, alias=H_DEV_TENANT),
    x_device_id: Optional[str] = Header(default=None, alias=H_DEV_DEVICE),
) -> DevicePrincipal:
    """FastAPI dependency returning the authenticated device principal.

    Prefers verified-mTLS headers; falls back to the dev headers unless
    ``CAMAI_REQUIRE_MTLS`` forbids it. Raises 401 when no trustworthy identity is
    present. Note ``tenant_id`` may be ``None`` for a zero-touch device whose cert
    does not yet carry a tenant -- callers resolve that via the device registry.
    """
    # --- Real mTLS path: proxy verified the cert. -------------------------- #
    if x_ssl_client_s_dn is not None:
        verify = (x_ssl_client_verify or "").upper()
        # nginx sets NONE/FAILED:<reason>/SUCCESS. Absent header is treated as OK
        # only for proxies that don't emit a verify status but only forward the DN
        # after a successful verification (documented deployment requirement).
        if x_ssl_client_verify is not None and verify != "SUCCESS":
            raise HTTPException(status_code=401, detail="client certificate not verified")

        principal = identity_from_dn(x_ssl_client_s_dn)

        # If the full cert was forwarded, re-derive and cross-check the device id.
        if x_client_cert:
            pem = _decode_forwarded_cert(x_client_cert)
            derived = device_id_from_cert_pem(pem) if pem else None
            if derived:
                if principal.device_id and principal.device_id != derived:
                    raise HTTPException(
                        status_code=401,
                        detail="certificate CN does not match its public key fingerprint",
                    )
                principal["device_id"] = derived

        if not principal.device_id:
            raise HTTPException(status_code=401, detail="no device id in client certificate")
        return principal

    # --- Dev fallback: trust the X-Device-* headers (pilot only). ---------- #
    if not _mtls_required() and (x_device_tenant or x_device_id):
        return DevicePrincipal(tenant_id=x_device_tenant, device_id=x_device_id)

    raise HTTPException(status_code=401, detail="device authentication required")


def resolve_tenant(body_tenant: str, device: DevicePrincipal) -> str:
    """Reconcile the cert-derived tenant with the body's claimed tenant.

    The authenticated tenant always wins; the body value is only accepted when the
    cert did not carry a tenant (zero-touch, pre-binding). A mismatch is fatal so
    an edge box can never write into another tenant's data. This is the hardened
    replacement for ``main._resolve_tenant``'s body-trusting seam.
    """
    cert_tenant = device.tenant_id
    if cert_tenant and cert_tenant != body_tenant:
        raise HTTPException(
            status_code=403,
            detail="tenant mismatch between device identity and payload",
        )
    return cert_tenant or body_tenant


def _decode_forwarded_cert(raw: str) -> Optional[str]:
    """Normalize a proxy-forwarded client cert into PEM text.

    nginx's ``$ssl_client_escaped_cert`` URL-encodes the PEM; some proxies send it
    with literal spaces instead of newlines. Handle both, returning ``None`` if it
    doesn't look like a certificate.
    """
    text = unquote(raw)
    begin, end = "-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"
    if begin not in text or end not in text:
        return None
    if "\n" in text:
        return text
    # Header collapsed the PEM to spaces: pull out the base64 body (everything
    # between the fixed markers), re-wrap it, and keep the markers intact -- a
    # naive space->newline replace would also split the "BEGIN CERTIFICATE" marker.
    body = text.split(begin, 1)[1].split(end, 1)[0]
    lines = [tok for tok in body.split() if tok]
    return "\n".join([begin, *lines, end])
