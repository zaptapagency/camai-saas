"""Metered billing: count active cameras per tenant and report usage to Stripe.

Billing unit: **active camera per month** — a camera is active for a period if its
edge agent reported at least one event during it. A nightly job counts active
cameras per tenant and reports the number to Stripe as metered usage; Stripe issues
the invoice at period end.

Design choices that avoid billing disputes (from the plan):
* Usage is reported with ``action="set"`` (not "increment"), so the reported value
  is the current active-camera count for the period. Re-running the job is
  idempotent — it never double-counts a camera.
* The metering computation is pure and store-agnostic, so it's unit-tested against
  the dev store without touching Stripe.

The Stripe SDK is imported lazily and the client is injectable, so tests run with a
fake and no network or API key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


# --------------------------------------------------------------------------- #
# Period helpers
# --------------------------------------------------------------------------- #

def month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """[start, end) of the calendar month containing ``now`` (UTC)."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


# --------------------------------------------------------------------------- #
# Stripe reporting (injectable)
# --------------------------------------------------------------------------- #

class UsageReporter(Protocol):
    # The first argument is the tenant's billing target. Which billing_accounts
    # column supplies it is declared by the reporter's ``account_field`` attribute
    # (see ``run_billing``): usage-record reporters bill a subscription item, meter
    # reporters bill a customer. Defaulting the attribute keeps old reporters working.
    account_field: str

    def report(self, subscription_item_id_or_meter: str, quantity: int, at: datetime) -> Any: ...


class StripeReporter:
    """Reports metered usage via the legacy Stripe *usage records* API.

    Use this when the tenant's price is a classic metered price whose usage is
    reported against a **subscription item** (``SubscriptionItem.create_usage_record``).
    It reports with ``action="set"``, so the reported value replaces the period's
    running total — re-running the nightly job never inflates usage.

    ``client`` defaults to the real ``stripe`` module (imported lazily). Tests pass
    a fake client with the same ``SubscriptionItem.create_usage_record`` surface.

    Usage records vs. meter events: usage records are the older, subscription-item
    keyed API and are the natural fit for a per-period *snapshot* count (active
    cameras this month) because ``action="set"`` gives exact idempotency. Prefer
    :class:`MeterEventReporter` for new integrations Stripe steers everyone toward
    the Meter Events API and usage records are being phased out but keep this for
    subscriptions still on legacy metered prices.
    """

    # run_billing feeds report() the account's stripe_subscription_item_id.
    account_field = "stripe_subscription_item_id"

    def __init__(self, api_key: str | None = None, client: Any = None) -> None:
        if client is None:
            import stripe  # lazy: not needed for tests or metering-only runs
            if api_key:
                stripe.api_key = api_key
            client = stripe
        self._client = client

    def report(self, subscription_item_id: str, quantity: int, at: datetime) -> Any:
        # action="set" makes the reported value the period's current count, so the
        # nightly job is idempotent (re-running never inflates usage).
        return self._client.SubscriptionItem.create_usage_record(
            subscription_item_id,
            quantity=quantity,
            timestamp=int(at.timestamp()),
            action="set",
        )


class MeterEventReporter:
    """Reports usage via Stripe's modern **Billing Meter Events** API.

    ``stripe.billing.MeterEvent.create`` records a usage event against a *customer*
    and a meter (identified by its ``event_name``); Stripe maps the customer to the
    right subscription/meter and aggregates events over the period. Unlike usage
    records there is no subscription-item id in the call the tenant's billing target
    is the **customer id**, which is why ``account_field`` points run_billing at the
    ``stripe_customer_id`` column.

    Idempotency note: meter events are *aggregated*, not overwritten. To keep the
    nightly snapshot job re-runnable without double-counting, the meter must be
    configured with a ``last``-style aggregation (report the current active-camera
    count each night and Stripe keeps the last value for the period) rather than
    ``sum``. Meters configured to ``sum`` suit monotonic usage (API calls, GB
    ingested) where every event is a real increment for those, send deltas, not a
    running total. See :func:`app.billing_setup.create_catalog` for meter setup.

    ``client`` defaults to the real ``stripe`` module (imported lazily); tests pass
    a fake exposing ``billing.MeterEvent.create``.
    """

    # run_billing feeds report() the account's stripe_customer_id.
    account_field = "stripe_customer_id"

    def __init__(
        self,
        event_name: str = "active_camera",
        api_key: str | None = None,
        client: Any = None,
    ) -> None:
        # event_name is fixed per meter (not per tenant) it names the meter this
        # reporter writes to, mirroring the meter created by create_catalog().
        self._event_name = event_name
        if client is None:
            import stripe  # lazy: not needed for tests or metering-only runs
            if api_key:
                stripe.api_key = api_key
            client = stripe
        self._client = client

    def report(self, stripe_customer_id: str, quantity: int, at: datetime) -> Any:
        # payload keys are strings per the Meter Events API; "value" is the metered
        # quantity and "stripe_customer_id" is the mandatory customer mapping key.
        return self._client.billing.MeterEvent.create(
            event_name=self._event_name,
            payload={
                "value": str(quantity),
                "stripe_customer_id": stripe_customer_id,
            },
            timestamp=int(at.timestamp()),
        )


# --------------------------------------------------------------------------- #
# Metering run
# --------------------------------------------------------------------------- #

@dataclass
class TenantUsage:
    tenant_id: str
    active_cameras: int
    camera_ids: list[str]
    reported: bool = False
    subscription_item_id: str | None = None
    error: str | None = None


@dataclass
class BillingRun:
    period_start: datetime
    period_end: datetime
    tenants: list[TenantUsage] = field(default_factory=list)

    @property
    def total_active_cameras(self) -> int:
        return sum(t.active_cameras for t in self.tenants)


def active_camera_count(store, tenant_id: str, start: datetime, end: datetime) -> list[str]:
    """Active camera ids for a tenant over [start, end) — pure read, no Stripe."""
    return store.active_camera_ids(tenant_id, start.isoformat(), end.isoformat())


def run_billing(
    store,
    reporter: UsageReporter | None = None,
    now: datetime | None = None,
) -> BillingRun:
    """Count active cameras per tenant for the current month and report to Stripe.

    Only tenants with a billing account that has a ``stripe_subscription_item_id``
    are reported; others are still counted (useful for dashboards/dry runs).
    """
    start, end = month_bounds(now)
    run = BillingRun(period_start=start, period_end=end)

    # Which billing_accounts column supplies the reporter's target: a usage-record
    # reporter bills a subscription item, a meter reporter bills a customer. The
    # reporter declares it via ``account_field`` (default keeps legacy behaviour).
    target_field = getattr(reporter, "account_field", "stripe_subscription_item_id")

    for account in store.all_billing_accounts():
        tenant_id = account["tenant_id"]
        cam_ids = active_camera_count(store, tenant_id, start, end)
        target = account.get(target_field)
        usage = TenantUsage(
            tenant_id=tenant_id,
            active_cameras=len(cam_ids),
            camera_ids=cam_ids,
            subscription_item_id=target,
        )
        if reporter and target:
            try:
                reporter.report(target, usage.active_cameras, end)
                usage.reported = True
            except Exception as e:  # keep going; one tenant's failure isn't fatal
                usage.error = str(e)
        run.tenants.append(usage)

    return run
