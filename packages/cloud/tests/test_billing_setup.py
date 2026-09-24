"""Tests for idempotent Stripe catalog + subscription provisioning.

A stateful fake Stripe client records created objects and returns them from list(),
so we can assert create_catalog converges (no duplicates on a second run).
"""

from app.billing_setup import (
    PLANS,
    METER_EVENT_NAME,
    create_catalog,
    find_or_create_subscription,
)


class _Resource:
    """Fake Stripe resource: stateful list()/create() over an in-memory table."""

    def __init__(self, prefix):
        self._prefix = prefix
        self._rows = []
        self.create_calls = 0

    def list(self, **kwargs):
        rows = self._rows
        lks = kwargs.get("lookup_keys")
        if lks is not None:
            rows = [r for r in rows if r.get("lookup_key") in lks]
        customer = kwargs.get("customer")
        if customer is not None:
            rows = [r for r in rows if r.get("customer") == customer]
        return {"data": list(rows)}

    def create(self, **kwargs):
        self.create_calls += 1
        row = dict(kwargs)
        row["id"] = f"{self._prefix}_{len(self._rows) + 1}"
        self._rows.append(row)
        return row


class _Billing:
    def __init__(self):
        self.Meter = _Resource("mtr")


class _FakeStripe:
    def __init__(self):
        self.Product = _Resource("prod")
        self.Price = _Resource("price")
        self.Subscription = _SubResource("sub")
        self.billing = _Billing()


class _SubResource(_Resource):
    def create(self, **kwargs):
        # Subscriptions materialize an item (with a generated id) per requested price.
        self.create_calls += 1
        row = dict(kwargs)
        row["id"] = f"{self._prefix}_{len(self._rows) + 1}"
        items = []
        for i, spec in enumerate(kwargs.get("items", []), start=1):
            items.append({"id": f"si_{len(self._rows) + 1}_{i}",
                          "price": {"id": spec["price"]}})
        row["items"] = {"data": items}
        self._rows.append(row)
        return row


def test_create_catalog_returns_ids_for_all_plans():
    client = _FakeStripe()
    catalog = create_catalog(client=client)

    assert catalog.product_id
    assert catalog.meter_id
    assert set(catalog.prices) == set(PLANS)
    for plan in PLANS:
        assert catalog.metered_price(plan)
        assert catalog.flat_price(plan)


def test_create_catalog_is_idempotent():
    client = _FakeStripe()
    first = create_catalog(client=client)
    prod_creates = client.Product.create_calls
    price_creates = client.Price.create_calls
    meter_creates = client.billing.Meter.create_calls

    second = create_catalog(client=client)

    # Second run creates nothing new and returns identical ids.
    assert client.Product.create_calls == prod_creates
    assert client.Price.create_calls == price_creates
    assert client.billing.Meter.create_calls == meter_creates
    assert second.product_id == first.product_id
    assert second.meter_id == first.meter_id
    assert second.prices == first.prices


def test_meter_created_with_expected_event_name():
    client = _FakeStripe()
    create_catalog(client=client)
    meter = client.billing.Meter._rows[0]
    assert meter["event_name"] == METER_EVENT_NAME
    assert meter["customer_mapping"]["event_payload_key"] == "stripe_customer_id"


def test_find_or_create_subscription_idempotent():
    client = _FakeStripe()
    catalog = create_catalog(client=client)

    first = find_or_create_subscription(client, "cus_1", catalog, "growth")
    assert first.created is True
    assert first.camera_subscription_item_id

    second = find_or_create_subscription(client, "cus_1", catalog, "growth")
    assert second.created is False
    assert second.subscription_id == first.subscription_id
    assert second.camera_subscription_item_id == first.camera_subscription_item_id
    assert client.Subscription.create_calls == 1  # not created twice
