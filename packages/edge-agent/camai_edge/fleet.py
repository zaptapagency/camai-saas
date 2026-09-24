"""Edge-side fleet client — pull desired config + pinned release, outbound-only.

The edge box never opens an inbound port, so it cannot be "pushed" to. Instead it
*pulls* its desired state from the cloud on the same outbound, mTLS-authenticated
channel :class:`camai_edge.sync.CloudSync` uses for events, and converges toward
it. This client:

* periodically ``GET``s the device's desired config and applies it (via a callback)
  only when ``config_version`` changed — so an unchanged pull is a cheap no-op and
  never restarts a healthy pipeline;
* ``GET``s the device's pinned agent/model version and surfaces it (via a callback)
  so the supervisor can trigger an OTA update / restart;
* reports the version it is *currently* running on every pull (request headers), so
  the cloud can tell which boxes have actually converged onto a rollout.

Deliberately mirrors ``CloudSync``: same cert/verify handling, same daemon-thread
lifecycle, same "never let the poll thread die" discipline. The heavy pieces
(applying config, performing an update) are left to injected callbacks so this
module stays testable without a real pipeline.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

import httpx
from pydantic import BaseModel, Field

from camai_edge.config import CloudConfig


class FleetConfig(BaseModel):
    """Edge configuration for fleet polling. Define here — do NOT edit CloudConfig.

    ``base_url`` is optional: when empty, the client reuses ``CloudConfig.ingest_url``
    so a single mTLS endpoint serves both ingest and fleet. When ``enabled`` is
    False (the default) the client is inert — fleet management is opt-in per site.
    """

    enabled: bool = False
    base_url: str = ""  # empty => reuse CloudConfig.ingest_url
    poll_interval_seconds: float = Field(default=60.0, gt=0)


# Callbacks the supervisor injects. Kept as plain callables (not an interface) so a
# caller can pass a lambda in tests and a real applier in production.
ConfigApplier = Callable[[dict], None]
ReleaseHandler = Callable[[dict], None]


class FleetClient:
    """Polls desired config + release for one device and applies changes.

    ``on_config`` is invoked with the desired-config dict only when its
    ``config_version`` advances past the last one applied; ``on_release`` is invoked
    with the desired-release dict whenever a (changed) pin is present.
    """

    def __init__(
        self,
        fleet: FleetConfig,
        cloud: CloudConfig,
        *,
        device_id: str,
        tenant_id: str,
        agent_version: str,
        model_version: str | None = None,
        on_config: Optional[ConfigApplier] = None,
        on_release: Optional[ReleaseHandler] = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._fleet = fleet
        self._cloud = cloud
        self._device_id = device_id
        self._tenant_id = tenant_id
        self._agent_version = agent_version
        self._model_version = model_version
        self._on_config = on_config
        self._on_release = on_release

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # ``client`` is injectable so tests drive a MockTransport with no network.
        self._client = client if client is not None else self._build_client()

        # Convergence state: the last config version we applied and the last release
        # we acted on, so repeated identical pulls don't re-trigger work.
        self._applied_config_version: int = -1
        self._last_release: dict | None = None

    def _build_client(self) -> httpx.Client | None:
        base = self._fleet.base_url or self._cloud.ingest_url
        if not (self._fleet.enabled and base):
            return None  # disabled or no endpoint => inert (offline)
        cert = None
        if self._cloud.device_cert and self._cloud.device_key:
            cert = (self._cloud.device_cert, self._cloud.device_key)
        verify = self._cloud.ca_bundle or True
        return httpx.Client(
            base_url=base.rstrip("/"),
            cert=cert,
            verify=verify,
            timeout=10.0,
        )

    # -- lifecycle ---------------------------------------------------------- #

    def start(self) -> None:
        if self._client is None:
            return  # disabled / offline mode
        self._thread = threading.Thread(target=self._run, name="fleet-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        if self._client:
            self._client.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # never let the poll thread die
                print(f"[fleet] transient error, will retry: {exc}")
            self._stop.wait(self._fleet.poll_interval_seconds)

    # -- one poll ----------------------------------------------------------- #

    @property
    def _headers(self) -> dict[str, str]:
        # Report what we're actually running so the cloud can see convergence.
        headers = {"x-agent-version": self._agent_version}
        if self._model_version:
            headers["x-model-version"] = self._model_version
        return headers

    def poll_once(self) -> dict[str, Any]:
        """Pull config + release once and apply any changes.

        Returns a small status dict (handy for tests and for logging): whether the
        config changed this poll and the release pin that was seen (if any).
        """
        assert self._client is not None
        config_changed = self._pull_config()
        release = self._pull_release()
        return {"config_changed": config_changed, "release": release}

    def _pull_config(self) -> bool:
        assert self._client is not None
        resp = self._client.get(
            f"/v1/devices/{self._device_id}/config", headers=self._headers
        )
        resp.raise_for_status()
        config = resp.json()
        version = int(config.get("config_version", 0))
        # Apply only on a version advance: an unchanged config must not churn a
        # healthy pipeline (a reconfigure drops frames).
        if version > self._applied_config_version:
            self._applied_config_version = version
            if self._on_config is not None:
                self._on_config(config)
            return True
        return False

    def _pull_release(self) -> dict | None:
        assert self._client is not None
        resp = self._client.get(
            f"/v1/devices/{self._device_id}/release", headers=self._headers
        )
        if resp.status_code == 404:
            return None  # nothing pinned -> keep running current version
        resp.raise_for_status()
        release = resp.json()
        # Only hand a *changed* pin to the supervisor, so it doesn't re-trigger an
        # update/restart on every poll for the same version.
        if release != self._last_release:
            self._last_release = release
            if self._on_release is not None:
                self._on_release(release)
        return release

    @property
    def applied_config_version(self) -> int:
        return self._applied_config_version
