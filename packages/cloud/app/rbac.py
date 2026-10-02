"""Role-based access control — deriving *what a caller may do* from an authenticated role.

Enterprise buyers gate procurement on two things above almost all else: every
privileged action is authorized against a role, and every privileged action is
recorded (see :mod:`app.audit`). This module is the first half.

Header seam (mirrors the tenant seam in ``main._resolve_tenant`` and the mTLS seam
in ``app.security``)
--------------------------------------------------------------------------------
The caller's role is read from the ``X-CamAI-Role`` request header, defaulting to
``viewer`` (least privilege) when absent or unrecognized. **In production this
header is never client-supplied**: it is derived by the authenticating reverse
proxy / API gateway from the verified SSO/OIDC session (the IdP group or custom
claim mapped to a CamAI role) and forwarded here *after the gateway strips any
client copy of the same header* — exactly the trust model ``app.security``
documents for the mTLS identity headers. The header is the pre-SSO seam so that
wiring a real IdP later is a deployment change, not a code change.

Role model
----------
Four ordered roles, least to most privileged::

    viewer  <  analyst  <  admin  <  owner

Each carries an integer :pyattr:`Role.rank`; authorization is a simple rank
comparison, so a higher role always subsumes the permissions of a lower one.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException

# Header the authenticating gateway sets from the verified SSO/OIDC session. The
# pre-SSO dev/pilot seam: callers may send it directly (cf. X-Device-Tenant).
ROLE_HEADER = "X-CamAI-Role"


class Role(str, Enum):
    """A caller's authorization level. ``str`` subclass so it serializes as its name."""

    viewer = "viewer"
    analyst = "analyst"
    admin = "admin"
    owner = "owner"

    @property
    def rank(self) -> int:
        """Monotonic privilege rank; higher strictly subsumes lower."""
        return _RANK[self]


# Explicit ordering. Kept as a side table (rather than Enum auto-value) so the
# wire value stays the human-readable name while the rank drives comparisons.
_RANK: dict["Role", int] = {
    Role.viewer: 0,
    Role.analyst: 1,
    Role.admin: 2,
    Role.owner: 3,
}


def _coerce(raw: Optional[str]) -> Role:
    """Map a raw header value to a Role, failing closed to ``viewer``.

    An absent, blank, or unrecognized role is treated as the least-privileged
    role rather than raising, so a misconfigured gateway can never accidentally
    *escalate* a request — it can only under-privilege it.
    """
    if not raw:
        return Role.viewer
    try:
        return Role(raw.strip().lower())
    except ValueError:
        return Role.viewer


def current_role(
    x_camai_role: Optional[str] = Header(default=None, alias=ROLE_HEADER),
) -> Role:
    """FastAPI dependency: the authenticated caller's role (default ``viewer``)."""
    return _coerce(x_camai_role)


def require_role(minimum: Role) -> Callable[..., Role]:
    """Return a FastAPI dependency that admits only callers at or above ``minimum``.

    Usage::

        @router.put(..., dependencies=[Depends(require_role(Role.admin))])
        # or, to also read the role in the handler:
        def handler(role: Role = Depends(require_role(Role.admin))): ...

    Raises ``HTTPException(403)`` when the caller's rank is below ``minimum``.
    """

    def _dependency(role: Role = Depends(current_role)) -> Role:
        if role.rank < minimum.rank:
            raise HTTPException(
                status_code=403,
                detail=f"role '{role.value}' is not permitted; '{minimum.value}' or higher required",
            )
        return role

    return _dependency
