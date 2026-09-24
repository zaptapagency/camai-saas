"""Nightly billing job: report active-camera usage to Stripe.

    # dry run (count only, no Stripe calls)
    python -m app.billing_job --dry-run

    # real run (needs STRIPE_API_KEY and billing_accounts populated)
    STRIPE_API_KEY=sk_live_... python -m app.billing_job

Schedule this once a day (cron / Task Scheduler / a cloud scheduled job). Stripe
generates the invoice at period end from the reported usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from app.billing import StripeReporter, run_billing
from app.store_factory import get_store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="camai-bill")
    ap.add_argument("--dry-run", action="store_true",
                    help="count active cameras but do not report to Stripe")
    args = ap.parse_args(argv)

    store = get_store()
    reporter = None
    if not args.dry_run:
        api_key = os.environ.get("STRIPE_API_KEY")
        if not api_key:
            print("STRIPE_API_KEY not set (use --dry-run to count only)", file=sys.stderr)
            return 2
        reporter = StripeReporter(api_key=api_key)

    run = run_billing(store, reporter=reporter)
    out = {
        "period_start": run.period_start.isoformat(),
        "period_end": run.period_end.isoformat(),
        "total_active_cameras": run.total_active_cameras,
        "dry_run": args.dry_run,
        "tenants": [
            {"tenant_id": t.tenant_id, "active_cameras": t.active_cameras,
             "reported": t.reported, "error": t.error}
            for t in run.tenants
        ],
    }
    print(json.dumps(out, indent=2))
    # Non-zero exit if any tenant that should have reported failed.
    return 1 if any(t.error for t in run.tenants) else 0


if __name__ == "__main__":
    raise SystemExit(main())
