"""Tests for the modern Meter Events reporter and reporter-target selection.

Fake Stripe client only, no network or API key.
"""

import os
import tempfile
from datetime import datetime, timezone

from camai_schema import Event, EventType, Mode

from app.store import Store
from app.billing import MeterEventReporter, StripeReporter, run_billing


def _store() -> Store:
    return Store(os.path.join(tempfile.mkdtemp(), "meter.db"))


def _event(tenant, camera, ts):
    return Event(tenant_id=tenant, site_id="s1", camera_id=camera,
                 type=EventType.occupancy_sample, mode=Mode.parking, count=1, ts=ts)


class _FakeMeterEvent:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "mtr_evt_1", **kwargs}


class _FakeBilling:
    def __init__(self):
        self.MeterEvent = _FakeMeterEvent()


class _FakeStripe:
    def __init__(self):
        self.billing = _FakeBilling()


def test_meter_event_reporter_calls_modern_api():
    stripe = _FakeStripe()
    reporter = MeterEventReporter(event_name="active_camera", client=stripe)
    at = datetime(2026, 4, 1, tzinfo=timezone.utc)

    reporter.report("cus_123", 7, at)

    call = stripe.billing.MeterEvent.calls[0]
    assert call["event_name"] == "active_camera"
    assert call["payload"] == {"value": "7", "stripe_customer_id": "cus_123"}
    assert call["timestamp"] == int(at.timestamp())


def test_meter_reporter_declares_customer_as_billing_target():
    # account_field steers run_billing to the customer id column, not sub-item.
    assert MeterEventReporter(client=_FakeStripe()).account_field == "stripe_customer_id"
    assert StripeReporter(client=object()).account_field == "stripe_subscription_item_id"


def test_run_billing_reports_customer_id_for_meter_reporter():
    store = _store()
    now = datetime.now(timezone.utc)
    # No subscription item on file, only a customer id -> meter reporter still bills.
    store.upsert_billing_account("t1", "cus_9", "", "growth")
    store.insert_events([_event("t1", "cam-a", now), _event("t1", "cam-b", now)])

    stripe = _FakeStripe()
    reporter = MeterEventReporter(client=stripe)
    run = run_billing(store, reporter=reporter, now=now)

    assert run.tenants[0].reported is True
    call = stripe.billing.MeterEvent.calls[0]
    assert call["payload"]["stripe_customer_id"] == "cus_9"
    assert call["payload"]["value"] == "2"
