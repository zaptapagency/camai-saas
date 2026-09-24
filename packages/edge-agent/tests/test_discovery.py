"""Tests for ONVIF/RTSP camera auto-discovery.

The network probe and the ONVIF Media lookup are both injected, so these tests
feed canned WS-Discovery XML and never touch a socket. They cover:

* parsing ProbeMatch payloads (XAddrs + friendly name from scopes),
* de-duplication of chatty devices and skipping of malformed payloads,
* RTSP resolution via an injected resolver, the vendor-hint fallback, and the
  keep-the-candidate behaviour when nothing resolves,
* the manual (non-ONVIF) fallback path.
"""

import pytest

from camai_edge.discovery import (
    CameraCandidate,
    build_probe_message,
    discover_cameras,
    manual_candidate,
    parse_probe_matches,
    rtsp_hint_for,
)


def _probe_match(xaddr: str, scopes: str = "", ns: str = "http://schemas.xmlsoap.org/ws/2005/04/discovery") -> str:
    """A minimally realistic WS-Discovery ProbeMatches SOAP envelope."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
      xmlns:d="{ns}">
      <SOAP-ENV:Body>
        <d:ProbeMatches>
          <d:ProbeMatch>
            <d:Types>dn:NetworkVideoTransmitter</d:Types>
            <d:Scopes>{scopes}</d:Scopes>
            <d:XAddrs>{xaddr}</d:XAddrs>
          </d:ProbeMatch>
        </d:ProbeMatches>
      </SOAP-ENV:Body>
    </SOAP-ENV:Envelope>"""


HIK = _probe_match(
    "http://192.168.1.64/onvif/device_service",
    "onvif://www.onvif.org/name/HIKVISION%20DS-2CD2143 "
    "onvif://www.onvif.org/hardware/DS-2CD2143",
)
AXIS = _probe_match(
    "http://192.168.1.90:8080/onvif/device_service http://10.0.0.5/onvif/device_service",
    "onvif://www.onvif.org/name/AXIS+M1065",
)


def test_build_probe_message_targets_video_transmitters():
    msg = build_probe_message("urn:uuid:fixed")
    assert "NetworkVideoTransmitter" in msg
    assert "urn:uuid:fixed" in msg
    assert msg.lstrip().startswith("<?xml")


def test_parse_extracts_xaddr_and_name():
    cands = parse_probe_matches([HIK])
    assert len(cands) == 1
    assert cands[0].xaddr == "http://192.168.1.64/onvif/device_service"
    assert cands[0].name == "HIKVISION DS-2CD2143"
    assert cands[0].source == "onvif"


def test_parse_takes_first_of_multiple_xaddrs():
    cands = parse_probe_matches([AXIS])
    assert cands[0].xaddr == "http://192.168.1.90:8080/onvif/device_service"
    assert cands[0].name == "AXIS M1065"


def test_parse_dedupes_repeat_responses():
    # A device commonly answers the same probe more than once.
    cands = parse_probe_matches([HIK, HIK, AXIS])
    assert len(cands) == 2


def test_parse_skips_malformed_and_xaddr_less():
    no_xaddr = _probe_match("").replace("<d:XAddrs></d:XAddrs>", "")
    cands = parse_probe_matches(["<not-xml", no_xaddr, HIK])
    assert len(cands) == 1
    assert cands[0].name == "HIKVISION DS-2CD2143"


def test_name_falls_back_to_host_without_scopes():
    plain = _probe_match("http://192.168.1.77/onvif/device_service", scopes="")
    cands = parse_probe_matches([plain])
    assert cands[0].name == "192.168.1.77"


def test_discover_resolves_rtsp_via_injected_resolver():
    def fake_probe(_timeout):
        return [HIK]

    def fake_resolver(xaddr, user, pw):
        assert xaddr == "http://192.168.1.64/onvif/device_service"
        assert (user, pw) == ("admin", "secret")
        return "rtsp://192.168.1.64:554/Streaming/Channels/101"

    cands = discover_cameras(
        username="admin", password="secret",
        probe=fake_probe, rtsp_resolver=fake_resolver,
    )
    assert cands[0].rtsp_url == "rtsp://192.168.1.64:554/Streaming/Channels/101"


def test_discover_falls_back_to_vendor_hint():
    # Resolver returns nothing (e.g. onvif-zeep missing); we still hint a URL
    # from the vendor name recovered from scopes.
    cands = discover_cameras(
        probe=lambda _t: [HIK],
        rtsp_resolver=lambda *a: None,
    )
    assert cands[0].rtsp_url == "rtsp://192.168.1.64:554/Streaming/Channels/101"


def test_discover_keeps_candidate_when_nothing_resolves():
    # Unknown vendor + no resolver result => candidate kept with rtsp_url=None.
    unknown = _probe_match(
        "http://192.168.1.200/onvif/device_service",
        "onvif://www.onvif.org/name/GenericCam",
    )
    cands = discover_cameras(probe=lambda _t: [unknown], rtsp_resolver=lambda *a: None)
    assert len(cands) == 1
    assert cands[0].rtsp_url is None


def test_discover_can_skip_rtsp_resolution():
    called = {"n": 0}

    def resolver(*a):
        called["n"] += 1
        return "x"

    cands = discover_cameras(
        resolve_rtsp=False, probe=lambda _t: [HIK], rtsp_resolver=resolver,
    )
    assert called["n"] == 0
    assert cands[0].rtsp_url is None


def test_rtsp_hint_embeds_credentials():
    url = rtsp_hint_for("dahua ipc", "http://192.168.1.10/onvif/device_service", "u", "p")
    assert url == "rtsp://u:p@192.168.1.10:554/cam/realmonitor?channel=1&subtype=0"


def test_rtsp_hint_unknown_vendor_returns_none():
    assert rtsp_hint_for("mystery", "http://192.168.1.10/x") is None


def test_manual_candidate_accepts_rtsp_url():
    c = manual_candidate("rtsp://user:pw@10.0.0.9:554/live", name="Loading dock")
    assert c.source == "manual"
    assert c.xaddr == ""
    assert c.name == "Loading dock"
    assert c.rtsp_url == "rtsp://user:pw@10.0.0.9:554/live"


def test_manual_candidate_defaults_name_to_host():
    c = manual_candidate("rtsp://10.0.0.9/live")
    assert c.name == "10.0.0.9"


def test_manual_candidate_rejects_non_rtsp():
    with pytest.raises(ValueError):
        manual_candidate("http://10.0.0.9/live")
