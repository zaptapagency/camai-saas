"""Idempotent provisioning of the Stripe billing catalog and per-tenant subscriptions.

Standing up billing by hand in the Stripe dashboard is error-prone and drifts
between environments (dev/staging/prod each need the same product, meter and
prices). These helpers make the catalog *declarative*: run :func:`create_catalog`
in any environment and it converges to the same objects, creating what's missing
and reusing what already exists. That idempotency matters because the function is
safe to run on every deploy and in tests.

Design:
* Objects are matched by stable identifiers we control — a metadata marker on the
  product/meter and a ``lookup_key`` on each price — never by fragile display names.
  Re-running never creates duplicates.
* The Stripe SDK is imported lazily and the client is injectable, so tests run
  against a fake with no network or API key (mirroring ``billing.py``).

Plans (see docs/PLAN.md "Cost model": $15-30/camera/month):

    starter    $30 / active camera / month
    growth     $20 / active camera / month
    enterprise $15 / active camera / month

MINIMUM MONTHLY FEE PER SITE (from PLAN.md risks - "Low camera density per site
-> set a minimum monthly fee per site"): metered per-camera pricing alone
under-charges sites with only one or two cameras, where the fixed edge box +
install + support cost still applies. Each plan therefore carries a per-site
minimum monthly fee. Stripe cannot express "max(metered usage, floor)" on a single
metered price, so the floor is provisioned as a **separate flat recurring price**
(one unit per site) attached to the same subscription; the metered camera price
bills usage above the floor. The floor amounts live in :data:`PLANS` and the flat
prices are created alongside the metered ones so an operator does not have to add
them manually.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Marker written to every object this module manages, so lookups find *our*
# objects and never collide with anything else in the Stripe account.
MANAGED_KEY = "camai_managed"
MANAGED_VALUE = "true"
PRODUCT_ROLE = "cctv_analytics"

# Meter that the modern MeterEventReporter writes to (event_name must match
# billing.MeterEventReporter's default). ``last`` aggregation lets the nightly job
# report the current active-camera snapshot each night idempotently.
METER_EVENT_NAME = "active_camera"
METER_AGGREGATION = "last"


@dataclass(frozen=True)
class Plan:
    """A sellable plan: a per-active-camera metered rate plus a per-site floor."""

    key: str
    per_camera_cents: int          # unit_amount of the metered camera price
    min_site_fee_cents: int        # flat per-site monthly floor (see module docstring)


# Rates from PLAN.md's $15-30/camera/month band. Minimums are illustrative floors
# that cover fixed per-site cost at low camera density; tune per go-to-market.
PLANS: dict[str, Plan] = {
    "starter":    Plan("starter",    per_camera_cents=3000, min_site_fee_cents=9900),
    "growth":     Plan("growth",     per_camera_cents=2000, min_site_fee_cents=19900),
    "enterprise": Plan("enterprise", per_camera_cents=1500, min_site_fee_cents=49900),
}

CURRENCY = "usd"


@dataclass
class Catalog:
    """Ids returned by :func:`create_catalog` — stable across re-runs."""

    product_id: str
    meter_id: str
    meter_event_name: str = METER_EVENT_NAME
    # plan key -> {"metered": price_id, "flat": price_id}
    prices: dict[str, dict[str, str]] = field(default_factory=dict)

    def metered_price(self, plan: str) -> str:
        return self.prices[plan]["metered"]

    def flat_price(self, plan: str) -> str:
        return self.prices[plan]["flat"]


# --------------------------------------------------------------------------- #
# Client resolution (lazy, injectable)
# --------------------------------------------------------------------------- #

def _resolve_client(api_key: str | None = None, client: Any = None) -> Any:
    if client is not None:
        return client
    import stripe  # lazy: not needed for tests
    if api_key:
        stripe.api_key = api_key
    return stripe


# --------------------------------------------------------------------------- #
# Product
# --------------------------------------------------------------------------- #

def find_or_create_product(client: Any, name: str = "CamAI CCTV Analytics") -> str:
    """Return the id of the managed product, creating it if absent.

    Matched by our metadata marker rather than name, so renaming the product in the
    dashboard does not cause a duplicate on the next run.
    """
    for prod in _list(client.Product):
        meta = _meta(prod)
        if meta.get(MANAGED_KEY) == MANAGED_VALUE and meta.get("role") == PRODUCT_ROLE:
            return _id(prod)
    created = client.Product.create(
        name=name,
        metadata={MANAGED_KEY: MANAGED_VALUE, "role": PRODUCT_ROLE},
    )
    return _id(created)


# --------------------------------------------------------------------------- #
# Meter
# --------------------------------------------------------------------------- #

def find_or_create_meter(client: Any, event_name: str = METER_EVENT_NAME) -> str:
    """Return the id of the active-camera billing meter, creating it if absent.

    Matched by ``event_name`` (unique per meter). The meter reads the ``value`` key
    from each event payload and maps events to a customer via ``stripe_customer_id``.
    """
    for meter in _list(client.billing.Meter):
        if _get(meter, "event_name") == event_name:
            return _id(meter)
    created = client.billing.Meter.create(
        display_name="Active cameras",
        event_name=event_name,
        default_aggregation={"formula": METER_AGGREGATION},
        value_settings={"event_payload_key": "value"},
        customer_mapping={"type": "by_id", "event_payload_key": "stripe_customer_id"},
    )
    return _id(created)


# --------------------------------------------------------------------------- #
# Prices
# --------------------------------------------------------------------------- #

def _find_price_by_lookup_key(client: Any, lookup_key: str) -> str | None:
    # Prefer a targeted lookup; fall back to scanning if the client/fake lacks it.
    try:
        found = client.Price.list(lookup_keys=[lookup_key], limit=1)
    except TypeError:
        found = client.Price.list(limit=100)
    for price in _iter(found):
        if _get(price, "lookup_key") == lookup_key:
            return _id(price)
    return None


def find_or_create_metered_price(client: Any, product_id: str, meter_id: str, plan: Plan) -> str:
    """Metered per-active-camera price for ``plan`` (usage keyed to the meter)."""
    lookup_key = f"camai_{plan.key}_camera"
    existing = _find_price_by_lookup_key(client, lookup_key)
    if existing:
        return existing
    created = client.Price.create(
        product=product_id,
        currency=CURRENCY,
        unit_amount=plan.per_camera_cents,
        lookup_key=lookup_key,
        billing_scheme="per_unit",
        recurring={"interval": "month", "usage_type": "metered", "meter": meter_id},
        metadata={MANAGED_KEY: MANAGED_VALUE, "plan": plan.key, "role": "camera"},
    )
    return _id(created)


def find_or_create_flat_price(client: Any, product_id: str, plan: Plan) -> str:
    """Flat per-site monthly floor price for ``plan`` (the minimum monthly fee)."""
    lookup_key = f"camai_{plan.key}_min_site"
    existing = _find_price_by_lookup_key(client, lookup_key)
    if existing:
        return existing
    created = client.Price.create(
        product=product_id,
        currency=CURRENCY,
        unit_amount=plan.min_site_fee_cents,
        lookup_key=lookup_key,
        recurring={"interval": "month", "usage_type": "licensed"},
        metadata={MANAGED_KEY: MANAGED_VALUE, "plan": plan.key, "role": "min_site"},
    )
    return _id(created)


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #

def create_catalog(client: Any = None, api_key: str | None = None) -> Catalog:
    """Converge the Stripe catalog (product, meter, per-plan prices) and return ids.

    Idempotent: safe to run on every deploy. Nothing is created twice because every
    object is matched by a stable identifier before creation.
    """
    client = _resolve_client(api_key=api_key, client=client)
    product_id = find_or_create_product(client)
    meter_id = find_or_create_meter(client)

    catalog = Catalog(product_id=product_id, meter_id=meter_id)
    for plan in PLANS.values():
        catalog.prices[plan.key] = {
            "metered": find_or_create_metered_price(client, product_id, meter_id, plan),
            "flat": find_or_create_flat_price(client, product_id, plan),
        }
    return catalog


# --------------------------------------------------------------------------- #
# Per-tenant subscription
# --------------------------------------------------------------------------- #

@dataclass
class SubscriptionInfo:
    subscription_id: str
    # The metered camera item is what the nightly usage-record job reports against.
    camera_subscription_item_id: str
    created: bool


def find_or_create_subscription(
    client: Any,
    customer_id: str,
    catalog: Catalog,
    plan: str,
) -> SubscriptionInfo:
    """Return the tenant's subscription for ``plan``, creating it if absent.

    A subscription carries two items: the metered camera price (whose subscription
    item the usage-record job reports against) and the flat per-site floor. Matched
    by the customer already having a subscription that includes this plan's metered
    price, so re-running does not create a second subscription.
    """
    metered_price = catalog.metered_price(plan)
    flat_price = catalog.flat_price(plan)

    for sub in _list_subscriptions(client, customer_id):
        for item in _sub_items(sub):
            if _item_price_id(item) == metered_price:
                return SubscriptionInfo(
                    subscription_id=_id(sub),
                    camera_subscription_item_id=_id(item),
                    created=False,
                )

    created = client.Subscription.create(
        customer=customer_id,
        items=[{"price": metered_price}, {"price": flat_price}],
        metadata={MANAGED_KEY: MANAGED_VALUE, "plan": plan},
    )
    camera_item_id = ""
    for item in _sub_items(created):
        if _item_price_id(item) == metered_price:
            camera_item_id = _id(item)
            break
    return SubscriptionInfo(
        subscription_id=_id(created),
        camera_subscription_item_id=camera_item_id,
        created=True,
    )


# --------------------------------------------------------------------------- #
# Small tolerant accessors — Stripe objects behave like dicts *and* attr objects,
# and test fakes may use either; these keep the logic above readable.
# --------------------------------------------------------------------------- #

def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _id(obj: Any) -> str:
    return _get(obj, "id")


def _meta(obj: Any) -> dict:
    return _get(obj, "metadata", {}) or {}


def _iter(listing: Any):
    # Stripe list responses expose ``.data``; a fake may return a plain list.
    data = _get(listing, "data", None)
    if data is None:
        data = listing if isinstance(listing, (list, tuple)) else []
    return data


def _list(resource: Any):
    return _iter(resource.list(limit=100))


def _list_subscriptions(client: Any, customer_id: str):
    try:
        return _iter(client.Subscription.list(customer=customer_id, limit=100))
    except TypeError:
        return _iter(client.Subscription.list(limit=100))


def _sub_items(sub: Any):
    items = _get(sub, "items")
    return _iter(items) if items is not None else []


def _item_price_id(item: Any) -> str:
    price = _get(item, "price")
    if price is None:
        return _get(item, "price_id", "")
    return _id(price) if not isinstance(price, str) else price
