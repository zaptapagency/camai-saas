"""ONVIF / RTSP camera auto-discovery — the self-serve onboarding unlock.

The single biggest friction point in getting a site live is *finding the cameras*.
An installer should not have to log into a switch, read the DHCP table, or guess
RTSP paths per vendor. This module probes the LAN for ONVIF devices and hands the
calibration wizard a list of camera candidates it can activate with one click.

How it works
------------
1. **WS-Discovery multicast probe** (pure stdlib ``socket`` — no heavy deps): we
   send a SOAP ``Probe`` datagram to the ONVIF multicast group
   ``239.255.255.250:3702`` and collect ``ProbeMatch`` responses. Each match
   carries one or more device-service ``XAddrs`` (the ONVIF endpoint) plus
   ``Scopes`` from which we recover a human-friendly name/model.
2. **Best-effort RTSP resolution**: given a device XAddr and (optional)
   credentials, we ask the device's Media service for its stream URI via
   ``onvif-zeep``. That library is *lazily imported* and entirely optional — if it
   is not installed, or the device rejects the credentials, we still return the
   candidate with its XAddr so the operator can finish in the manual path.
3. **Manual-URL fallback**: cameras that do not speak ONVIF (or sit on a segment
   that blocks multicast) can be added by pasting an RTSP URL directly; we wrap it
   in the same :class:`CameraCandidate` shape so the UI treats both paths alike.

The network probe is *injectable* (see :func:`discover_cameras`'s ``probe`` and
``rtsp_resolver`` parameters) so the whole pipeline can be unit-tested against
canned WS-Discovery XML without touching the network.

Published compatibility
-----------------------
WS-Discovery + ONVIF Profile S is an industry standard; this probe has been
designed against the response formats of the major NVR/camera vendors that ship
ONVIF by default:

* **Hikvision** (and OEM lines: Annke, LTS, Nelly's) — ONVIF must be enabled in
  the web UI on some firmware; RTSP path ``/Streaming/Channels/101``.
* **Dahua** (and OEM: Amcrest, Lorex, EmpireTech) — RTSP path
  ``/cam/realmonitor?channel=1&subtype=0``.
* **Axis Communications** — RTSP path ``/axis-media/media.amp``.
* **Hanwha / Samsung Wisenet** — RTSP path ``/profile1/media.smp``.
* **Bosch, Vivotek, Uniview, Reolink (PoE models), Amcrest, Panasonic i-PRO,
  Honeywell, FLIR** — resolved via the ONVIF Media service rather than a
  hard-coded path.

Vendor-specific RTSP paths above are only a *hint* used as a last resort; the
authoritative URL always comes from the device's own Media service when reachable.
"""

from __future__ import annotations

import re
import socket
import uuid
from typing import Callable, Optional
from urllib.parse import urlparse, urlunparse
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field

# ONVIF WS-Discovery multicast group / port (RFC-standard for WS-Discovery).
WS_DISCOVERY_ADDR = "239.255.255.250"
WS_DISCOVERY_PORT = 3702

# Default RTSP paths per vendor, used only as a last-resort hint when the ONVIF
# Media service cannot be reached. The authoritative URL always comes from the
# device itself when possible. Keys are matched case-insensitively against the
# device scopes/name.
VENDOR_RTSP_HINTS: dict[str, str] = {
    "hikvision": "/Streaming/Channels/101",
    "dahua": "/cam/realmonitor?channel=1&subtype=0",
    "amcrest": "/cam/realmonitor?channel=1&subtype=0",
    "axis": "/axis-media/media.amp",
    "hanwha": "/profile1/media.smp",
    "wisenet": "/profile1/media.smp",
    "samsung": "/profile1/media.smp",
    "reolink": "/h264Preview_01_main",
}


class CameraCandidate(BaseModel):
    """A camera the wizard can offer the operator to activate.

    ``source`` records how we found it — ``"onvif"`` for a WS-Discovery match,
    ``"manual"`` for an operator-pasted URL — so the UI can badge auto-found
    cameras and so we never silently trust an unauthenticated multicast reply as
    if the operator had typed it.
    """

    name: str = Field(description="Human-friendly label (model/hostname when known).")
    xaddr: str = Field(default="", description="ONVIF device-service URL, empty for manual entries.")
    rtsp_url: Optional[str] = Field(
        default=None, description="Resolved RTSP stream URL, if we could determine one."
    )
    source: str = Field(default="onvif", description='"onvif" | "manual"')


# --------------------------------------------------------------------------- #
# WS-Discovery message build + parse  (pure, no sockets — unit-testable)
# --------------------------------------------------------------------------- #

def build_probe_message(message_id: Optional[str] = None) -> str:
    """Build the SOAP WS-Discovery ``Probe`` datagram body.

    We probe for ONVIF ``NetworkVideoTransmitter`` devices specifically (rather
    than every WS-Discovery speaker on the wire) so printers and other UPnP-ish
    gear do not clutter the results. ``message_id`` is a per-probe UUID URN; a
    fresh one is generated when not supplied so responses can be correlated.
    """
    if message_id is None:
        message_id = f"urn:uuid:{uuid.uuid4()}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
        ' xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
        ' xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        "<e:Header>"
        f"<w:MessageID>{message_id}</w:MessageID>"
        "<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
        "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
        "</e:Header>"
        "<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>"
        "</e:Envelope>"
    )


# WS-Discovery namespaces vary slightly (2005/04 vs 2009/01) between vendors, so
# we match XAddrs/Scopes by local tag name rather than a fixed namespace.
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_text(root: ET.Element, local_name: str) -> Optional[str]:
    for el in root.iter():
        if _local(el.tag) == local_name and el.text:
            return el.text.strip()
    return None


def _name_from_scopes(scopes: str) -> Optional[str]:
    """Recover a friendly name from ONVIF discovery scopes.

    Scopes are a space-separated list of URIs like
    ``onvif://www.onvif.org/name/HIKVISION%20DS-2CD...`` and
    ``onvif://www.onvif.org/hardware/DS-2CD2143``. We prefer ``name`` then
    ``hardware``, URL-decoding the value.
    """
    from urllib.parse import unquote

    best: dict[str, str] = {}
    for token in scopes.split():
        m = re.search(r"onvif://[^/]+/(name|hardware)/(.+)$", token)
        if m:
            best[m.group(1)] = unquote(m.group(2)).replace("+", " ").strip()
    return best.get("name") or best.get("hardware")


def parse_probe_matches(responses: list[str | bytes]) -> list[CameraCandidate]:
    """Parse raw WS-Discovery ProbeMatch XML payloads into candidates.

    De-duplicates on the first XAddr (a device may answer a probe more than once,
    and multi-homed devices list several XAddrs). Malformed payloads are skipped
    rather than aborting the whole discovery — one chatty device on the LAN should
    not sink the scan.
    """
    candidates: list[CameraCandidate] = []
    seen: set[str] = set()

    for raw in responses:
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue

        xaddrs = _find_text(root, "XAddrs")
        if not xaddrs:
            continue
        # XAddrs is a space-separated list; take the first reachable-looking one.
        xaddr = xaddrs.split()[0]
        if xaddr in seen:
            continue
        seen.add(xaddr)

        scopes = _find_text(root, "Scopes") or ""
        name = _name_from_scopes(scopes) or urlparse(xaddr).hostname or "camera"

        candidates.append(CameraCandidate(name=name, xaddr=xaddr, source="onvif"))

    return candidates


# --------------------------------------------------------------------------- #
# Network probe  (the one function that touches the wire — injected in tests)
# --------------------------------------------------------------------------- #

def ws_discovery_probe(timeout: float = 3.0) -> list[bytes]:
    """Send a multicast Probe and collect raw ProbeMatch datagrams.

    Pure stdlib: no third-party WS-Discovery library. Returns the raw response
    payloads for :func:`parse_probe_matches` to interpret, keeping the socket I/O
    isolated from parsing so tests can replace this function wholesale.

    Best-effort by nature: multicast may be blocked between VLANs, so a caller
    that gets an empty list should fall back to the manual path.
    """
    message = build_probe_message().encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    responses: list[bytes] = []
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(timeout)
        sock.sendto(message, (WS_DISCOVERY_ADDR, WS_DISCOVERY_PORT))
        while True:
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                break
            if data:
                responses.append(data)
    finally:
        sock.close()
    return responses


# --------------------------------------------------------------------------- #
# RTSP resolution  (lazy onvif-zeep; also injectable)
# --------------------------------------------------------------------------- #

def resolve_rtsp_via_onvif(
    xaddr: str,
    username: str = "",
    password: str = "",
) -> Optional[str]:
    """Ask a device's ONVIF Media service for its RTSP stream URI.

    ``onvif-zeep`` is imported here (not at module load) so the common discovery
    path — and the whole default test suite — never requires the heavy SOAP stack.
    Returns ``None`` on any failure (library missing, auth rejected, device
    unreachable); the caller keeps the candidate with just its XAddr.
    """
    try:
        from onvif import ONVIFCamera  # type: ignore
    except Exception:
        return None

    parsed = urlparse(xaddr)
    host = parsed.hostname
    port = parsed.port or 80
    if not host:
        return None

    try:
        cam = ONVIFCamera(host, port, username, password)
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        if not profiles:
            return None
        token = profiles[0].token
        req = media.create_type("GetStreamUri")
        req.ProfileToken = token
        req.StreamSetup = {
            "Stream": "RTP-Unicast",
            "Transport": {"Protocol": "RTSP"},
        }
        uri = media.GetStreamUri(req).Uri
    except Exception:
        return None

    return _inject_credentials(uri, username, password)


def _inject_credentials(rtsp_url: str, username: str, password: str) -> str:
    """Embed credentials in an RTSP URL so the pipeline can open it directly.

    Many devices return a bare ``rtsp://host/...`` URI even when the stream needs
    auth; OpenCV/FFmpeg expect ``rtsp://user:pass@host/...``. We only inject when
    both are provided and the URL has no userinfo already.
    """
    if not username:
        return rtsp_url
    parsed = urlparse(rtsp_url)
    if "@" in parsed.netloc:
        return rtsp_url
    userinfo = f"{username}:{password}" if password else username
    netloc = f"{userinfo}@{parsed.netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


def rtsp_hint_for(name_or_scopes: str, xaddr: str, username: str = "", password: str = "") -> Optional[str]:
    """Build a best-guess RTSP URL from the vendor hint table.

    Last resort only, used when the Media service is unreachable. Uses the ONVIF
    host with the vendor's conventional path and default RTSP port 554.
    """
    host = urlparse(xaddr).hostname
    if not host:
        return None
    key = name_or_scopes.lower()
    path = next((p for v, p in VENDOR_RTSP_HINTS.items() if v in key), None)
    if path is None:
        return None
    userinfo = f"{username}:{password}@" if username else ""
    return f"rtsp://{userinfo}{host}:554{path}"


# --------------------------------------------------------------------------- #
# Orchestration + manual fallback
# --------------------------------------------------------------------------- #

def manual_candidate(rtsp_url: str, name: str = "") -> CameraCandidate:
    """Wrap an operator-pasted RTSP URL as a candidate (the non-ONVIF path).

    Validates that it at least looks like an RTSP URL so a typo does not sail into
    the config; the friendly name defaults to the URL host.
    """
    parsed = urlparse(rtsp_url)
    if parsed.scheme.lower() != "rtsp" or not parsed.hostname:
        raise ValueError(f"not an rtsp:// url: {rtsp_url!r}")
    return CameraCandidate(
        name=name or parsed.hostname,
        xaddr="",
        rtsp_url=rtsp_url,
        source="manual",
    )


def discover_cameras(
    timeout: float = 3.0,
    username: str = "",
    password: str = "",
    resolve_rtsp: bool = True,
    probe: Callable[[float], list[str | bytes]] = ws_discovery_probe,
    rtsp_resolver: Optional[Callable[[str, str, str], Optional[str]]] = None,
) -> list[CameraCandidate]:
    """Discover ONVIF cameras on the LAN and resolve their RTSP URLs.

    ``probe`` and ``rtsp_resolver`` are injected so this whole flow can run in a
    test against canned XML with no network. In production, ``probe`` sends the
    multicast Probe and ``rtsp_resolver`` defaults to the ONVIF Media lookup.

    RTSP resolution is best-effort: a candidate whose stream URL cannot be
    determined is still returned (with ``rtsp_url=None``) so the operator can add
    credentials or a manual URL in the wizard.
    """
    if rtsp_resolver is None:
        rtsp_resolver = resolve_rtsp_via_onvif

    raw = probe(timeout)
    candidates = parse_probe_matches(raw)

    if resolve_rtsp:
        for cand in candidates:
            url = rtsp_resolver(cand.xaddr, username, password)
            if not url:
                # Fall back to the vendor hint keyed on the discovered name.
                url = rtsp_hint_for(cand.name, cand.xaddr, username, password)
            cand.rtsp_url = url

    return candidates
