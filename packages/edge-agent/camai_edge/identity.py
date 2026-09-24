"""Per-device cryptographic identity for the edge agent.

Every edge box needs a stable, unforgeable identity so the cloud can attribute
events to the right device (and therefore the right tenant) without ever trusting
a value the box puts in a request body. That identity is a private key that never
leaves the box plus an X.509 certificate the cloud (or a proxy in front of it) can
verify during the mTLS handshake.

Design decisions and the WHY behind them
----------------------------------------
* **Key never leaves the device.** We generate the private key locally on first
  boot and persist it under a device directory. Only the public half (as a CSR or
  a self-signed cert) is ever sent to the cloud during zero-touch registration.
* **``device_id`` is derived from the public key, not assigned.** It is the
  SHA-256 fingerprint of the SubjectPublicKeyInfo. This makes the id stable across
  reboots (same key -> same id), collision-resistant, and impossible to spoof:
  a box cannot claim another device's id without also holding that device's
  private key. The cloud can recompute the same id from the presented cert.
* **EC (P-256) over RSA.** Smaller keys/certs and much faster keygen on the low
  power SoCs these agents run on, with equivalent security.
* **``cryptography`` is a lazy import.** The default test suite and the pure
  config paths must run without the native crypto stack installed, so we only
  import it inside the functions that actually mint or read certificates.

Typical use in ``main.py`` startup::

    ident = ensure_identity(Path(config_dir) / "device")
    # feed ident.cert_path / ident.key_path into CloudConfig for the mTLS client,
    # and ident.device_id into the EventBatch / Heartbeat device_id field.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field


class DeviceIdentityConfig(BaseModel):
    """Where the device identity material lives and how certs are minted.

    Kept as its own model (rather than editing ``SiteConfig``) so this workstream
    stays self-contained. To wire it into the agent, add a single optional field
    to ``SiteConfig`` -- see this module's integration notes.
    """

    # Directory (absolute, or relative to the site config dir) holding the key,
    # cert and CSR. Isolated in its own dir so it can be locked down / backed up
    # independently of the rest of the config.
    dir: str = "device"

    key_filename: str = "device.key"
    cert_filename: str = "device.crt"
    csr_filename: str = "device.csr"

    # Subject CN is set to the derived device_id; this prefix is only for the
    # human-readable Organization field so certs are recognizable in a fleet view.
    organization: str = "CamAI"

    # Long-lived self-signed cert: the security boundary is the private key and
    # the cloud-side registry binding, not cert expiry. A CA-issued cert (via the
    # CSR path) can be shorter-lived and rotated.
    validity_days: int = Field(default=3650, gt=0)


@dataclass
class DeviceIdentity:
    """Resolved, on-disk identity for one edge box.

    ``device_id`` is derived from the public key (see module docstring) and is the
    value the agent puts in ``EventBatch.device_id`` / ``Heartbeat.device_id`` and
    that the cloud independently re-derives from the presented client certificate.
    """

    device_id: str
    dir: Path
    key_path: Path
    cert_path: Path
    csr_path: Path

    def cert_pem(self) -> str:
        """Return the self-signed certificate PEM (what a proxy would verify)."""
        return self.cert_path.read_text(encoding="utf-8")

    def csr_pem(self) -> str:
        """Return the CSR PEM to present during zero-touch registration."""
        return self.csr_path.read_text(encoding="utf-8")

    def public_key_pem(self) -> str:
        """Return the public key PEM, derived from the stored private key.

        Registration can send either the full CSR or just this public key; both
        are safe to transmit because they carry no secret material.
        """
        crypto = _load_crypto()
        key = crypto["load_key"](self.key_path.read_bytes())
        return key.public_key().public_bytes(
            encoding=crypto["Encoding"].PEM,
            format=crypto["PublicFormat"].SubjectPublicKeyInfo,
        ).decode("ascii")


def device_id_from_public_key_der(spki_der: bytes) -> str:
    """Derive the canonical device id from a SubjectPublicKeyInfo DER blob.

    Public so the cloud side can re-derive the exact same id from a presented
    certificate/CSR and confirm a device is who its key says it is. The ``dev_``
    prefix keeps ids visually distinct in logs; 32 hex chars (128 bits) of the
    SHA-256 fingerprint is far beyond collision range for any real fleet.
    """
    digest = hashlib.sha256(spki_der).hexdigest()
    return f"dev_{digest[:32]}"


def ensure_identity(
    config_dir: str | Path,
    config: DeviceIdentityConfig | None = None,
) -> DeviceIdentity:
    """Load the device identity, generating it on first boot if absent.

    Idempotent: if a key already exists it is reused (so ``device_id`` is stable),
    and only the missing artefacts (cert/CSR) are regenerated from it. This makes
    the function safe to call unconditionally on every startup.

    ``config_dir`` is the directory the identity lives in (the resolved
    ``DeviceIdentityConfig.dir``). Requires the ``cryptography`` package; it is
    imported lazily so importing this module never pulls in native deps.
    """
    cfg = config or DeviceIdentityConfig()
    d = Path(config_dir)
    d.mkdir(parents=True, exist_ok=True)

    key_path = d / cfg.key_filename
    cert_path = d / cfg.cert_filename
    csr_path = d / cfg.csr_filename

    crypto = _load_crypto()

    # 1. Private key: load if present, otherwise mint and persist (owner-only).
    if key_path.exists():
        key = crypto["load_key"](key_path.read_bytes())
    else:
        key = crypto["generate_key"]()
        _write_private_key(key_path, key, crypto)

    spki_der = key.public_key().public_bytes(
        encoding=crypto["Encoding"].DER,
        format=crypto["PublicFormat"].SubjectPublicKeyInfo,
    )
    device_id = device_id_from_public_key_der(spki_der)

    # 2. Self-signed cert (used directly for mTLS in the pilot) — (re)issue if
    #    missing so a deleted cert self-heals without touching the key/id.
    if not cert_path.exists():
        cert_pem = _build_self_signed_cert(key, device_id, cfg, crypto)
        cert_path.write_text(cert_pem, encoding="utf-8")

    # 3. CSR for the CA-issued path (zero-touch registration can forward this).
    if not csr_path.exists():
        csr_pem = _build_csr(key, device_id, cfg, crypto)
        csr_path.write_text(csr_pem, encoding="utf-8")

    return DeviceIdentity(
        device_id=device_id,
        dir=d,
        key_path=key_path,
        cert_path=cert_path,
        csr_path=csr_path,
    )


# --------------------------------------------------------------------------- #
# Internals — all crypto access goes through the lazily-loaded module bundle.
# --------------------------------------------------------------------------- #

def _load_crypto() -> dict:
    """Import ``cryptography`` lazily and return the handful of symbols we use.

    Bundled into a dict so callers never import the native library at module load
    time; the error is friendly if it is genuinely missing at runtime.
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography import x509
        from cryptography.x509.oid import NameOID
    except ImportError as exc:  # pragma: no cover - exercised only without the dep
        raise RuntimeError(
            "device identity requires the 'cryptography' package "
            "(pip install cryptography)"
        ) from exc

    def load_key(data: bytes):
        return serialization.load_pem_private_key(data, password=None)

    def generate_key():
        return ec.generate_private_key(ec.SECP256R1())

    return {
        "hashes": hashes,
        "serialization": serialization,
        "ec": ec,
        "x509": x509,
        "NameOID": NameOID,
        "Encoding": serialization.Encoding,
        "PublicFormat": serialization.PublicFormat,
        "PrivateFormat": serialization.PrivateFormat,
        "NoEncryption": serialization.NoEncryption,
        "load_key": load_key,
        "generate_key": generate_key,
    }


def _write_private_key(path: Path, key, crypto: dict) -> None:
    pem = key.private_bytes(
        encoding=crypto["Encoding"].PEM,
        format=crypto["PrivateFormat"].PKCS8,
        encryption_algorithm=crypto["NoEncryption"](),
    )
    path.write_bytes(pem)
    # Best-effort lock-down; no-op / harmless where chmod is unsupported (Windows).
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass


def _subject_name(device_id: str, cfg: DeviceIdentityConfig, crypto: dict):
    x509 = crypto["x509"]
    NameOID = crypto["NameOID"]
    return x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, device_id),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, cfg.organization),
    ])


def _build_self_signed_cert(key, device_id: str, cfg: DeviceIdentityConfig, crypto: dict) -> str:
    import datetime

    x509 = crypto["x509"]
    subject = _subject_name(device_id, cfg, crypto)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)  # self-signed: issuer == subject
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=cfg.validity_days))
        # Encode the device id as a URI SAN too, so a proxy that surfaces the SAN
        # (rather than the CN) still yields the same identity.
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(f"camai://device/{device_id}")]),
            critical=False,
        )
        .sign(private_key=key, algorithm=crypto["hashes"].SHA256())
    )
    return cert.public_bytes(crypto["Encoding"].PEM).decode("ascii")


def _build_csr(key, device_id: str, cfg: DeviceIdentityConfig, crypto: dict) -> str:
    x509 = crypto["x509"]
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(_subject_name(device_id, cfg, crypto))
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(f"camai://device/{device_id}")]),
            critical=False,
        )
        .sign(private_key=key, algorithm=crypto["hashes"].SHA256())
    )
    return csr.public_bytes(crypto["Encoding"].PEM).decode("ascii")
