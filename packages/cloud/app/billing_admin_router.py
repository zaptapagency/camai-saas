"""Admin HTTP API for wiring a tenant to its Stripe billing objects.

Populating ``billing_accounts`` (customer id, subscription-item id, plan) is an
operator task that happens out of band from ingest: after an operator provisions
Stripe (see :func:`app.billing_setup.create_catalog` /
:func:`app.billing_setup.find_or_create_subscription`) they must tell CamAI which
Stripe ids belong to which tenant, or the nightly billing job has nothing to report
against. This router exposes that as a small admin surface instead of requiring
direct SQL against the store.

Exposed as an ``APIRouter`` so the orchestrator can ``app.include_router(...)`` it
in ``main.py`` without this module editing shared files. The store is obtained via
``store_factory.get_store`` behind a FastAPI dependency, so tests can override it
and every request shares the app's configured backend.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import audit
from app.rbac import Role, require_role

router = APIRouter(prefix="/v1/admin", tags=["admin-billing"])


def get_billing_store() -> Any:
    """Resolve the storage backend. Overridable in tests via dependency_overrides."""
    from app.store_factory import get_store  # lazy + read-only: never edits the factory
    return get_store()


class BillingAccountIn(BaseModel):
    """Operator-supplied Stripe wiring for one tenant."""

    stripe_customer_id: str = Field(..., min_length=1)
    # The metered camera subscription item the nightly usage-record job reports
    # against. May be empty for a tenant provisioned for meter-event billing (which
    # keys on the customer), in which case the tenant is counted but not reported by
    # the usage-record reporter.
    stripe_subscription_item_id: str = ""
    plan: str = Field(..., min_length=1)


class BillingAccountOut(BaseModel):
    tenant_id: str
    stripe_customer_id: str | None = None
    stripe_subscription_item_id: str | None = None
    plan: str | None = None


@router.put("/tenants/{tenant_id}/billing", response_model=BillingAccountOut)
def set_billing_account(
    tenant_id: str,
    body: BillingAccountIn,
    store: Any = Depends(get_billing_store),
    role: Role = Depends(require_role(Role.admin)),
    x_camai_actor: str = Header(default="unknown", alias="X-CamAI-Actor"),
) -> BillingAccountOut:
    """Create or update a tenant's Stripe billing wiring (idempotent upsert).

    Admin-gated and audited: changing a tenant's billing wiring moves money, so it
    is exactly the kind of privileged mutation an enterprise security review
    expects to be both authorized and recorded.
    """
    store.upsert_billing_account(
        tenant_id,
        body.stripe_customer_id,
        body.stripe_subscription_item_id,
        body.plan,
    )
    account = store.get_billing_account(tenant_id)
    audit.record(
        actor=x_camai_actor,
        action="billing.account.upsert",
        tenant_id=tenant_id,
        resource=f"tenant:{tenant_id}:billing",
        meta={"role": role.value, "plan": body.plan},
    )
    return BillingAccountOut(**account)


@router.get("/tenants/{tenant_id}/billing", response_model=BillingAccountOut)
def get_billing_account(
    tenant_id: str,
    store: Any = Depends(get_billing_store),
) -> BillingAccountOut:
    """View a tenant's Stripe billing wiring, or 404 if it has none yet."""
    account = store.get_billing_account(tenant_id)
    if account is None:
        raise HTTPException(status_code=404, detail="no billing account for tenant")
    return BillingAccountOut(**account)
