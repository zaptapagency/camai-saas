"""Tests for metered billing: active-camera counting + Stripe reporting.

Runs against the SQLite dev store (real) with a fake Stripe client — no Postgres,
no network, no API key.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

from camai_schema import Event, EventType, Mode

from app.store import Store
from app.billing import (StripeReporter, month_bounds, active_camera_count,
                         run_billing)


def _store() -> Store:
    return Store(os.path.join(tempfile.mkdtemp(), "bill.db"))


def _event(tenant, camera, ts):
    return Event(tenant_id=tenant, site_id="s1", camera_id=camera,
                 type=EventType.occupancy_sample, mode=Mode.parking, count=1, ts=ts)


def test_month_bounds_contains_now():
    start, end = month_bounds(datetime(2026, 3, 15, 12, tzinfo=timezone.utc))
    assert start == datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 4, 1, tzinfo=timezone.utc)


def test_month_bounds_year_rollover():
    start, end = month_bounds(datetime(2026, 12, 20, tzinfo=timezone.utc))
    assert start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_active_camera_count_distinct_and_windowed():
    store = _store()
    now = datetime.now(timezone.utc)
    start, end = month_bounds(now)
    # 3 distinct cameras this month (one reports twice), 1 camera last month.
    store.insert_events([
        _event("t1", "cam-a", now),
        _event("t1", "cam-a", now),
        _event("t1", "cam-b", now),
        _event("t1", "cam-c", now),
        _event("t1", "cam-old", start - timedelta(days=2)),
    ])
    active = active_camera_count(store, "t1", start, end)
    assert active == ["cam-a", "cam-b", "cam-c"]  # distinct, sorted, in-window only


def test_run_billing_reports_active_count_to_stripe():
    store = _store()
    now = datetime.now(timezone.utc)
    store.upsert_billing_account("t1", "cus_1", "si_123", "growth")
    store.insert_events([_event("t1", "cam-a", now), _event("t1", "cam-b", now)])

    calls = []

    class FakeReporter:
        def report(self, subscription_item_id, quantity, at):
            calls.append((subscription_item_id, quantity))

    run = run_billing(store, reporter=FakeReporter(), now=now)
    assert run.total_active_cameras == 2
    assert calls == [("si_123", 2)]
    assert run.tenants[0].reported is True


def test_run_billing_skips_tenants_without_subscription_item():
    store = _store()
    now = datetime.now(timezone.utc)
    # Account exists but no subscription item -> counted, not reported.
    store.upsert_billing_account("t1", "cus_1", "", "starter")
    store.insert_events([_event("t1", "cam-a", now)])

    class FakeReporter:
        def __init__(self): self.calls = 0
        def report(self, *a, **k): self.calls += 1

    r = FakeReporter()
    run = run_billing(store, reporter=r, now=now)
    assert run.tenants[0].active_cameras == 1
    assert run.tenants[0].reported is False
    assert r.calls == 0


def test_stripe_reporter_sets_usage_idempotently():
    """StripeReporter must call the SDK with action='set' (not increment)."""
    captured = {}

    class FakeSubscriptionItem:
        @staticmethod
        def create_usage_record(sub_item, **kwargs):
            captured["sub_item"] = sub_item
            captured.update(kwargs)
            return {"id": "mbur_1"}

    class FakeStripe:
        SubscriptionItem = FakeSubscriptionItem

    reporter = StripeReporter(client=FakeStripe())
    at = datetime(2026, 4, 1, tzinfo=timezone.utc)
    reporter.report("si_123", 7, at)
    assert captured["sub_item"] == "si_123"
    assert captured["quantity"] == 7
    assert captured["action"] == "set"
    assert captured["timestamp"] == int(at.timestamp())
