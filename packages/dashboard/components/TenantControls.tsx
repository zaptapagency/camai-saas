"use client";

/**
 * Tenant picker + refresh-cadence selector + a live status pulse. This is the
 * operator's control strip, ported from the SPA header. Under real auth the
 * tenant input becomes read-only for non-admins (the tenant comes from the
 * session); we keep it editable here for the pilot.
 */

import { useEffect, useState } from "react";

import { REFRESH_OPTIONS, useTenant } from "@/lib/tenant";
import { useRole } from "@/lib/roles";

export function TenantControls() {
  const { tenantId, setTenantId, refreshMs, setRefreshMs } = useTenant();
  const { can } = useRole();
  const [draft, setDraft] = useState(tenantId);

  // Keep the local draft in sync when the tenant changes elsewhere (e.g. the
  // persisted value hydrating after mount), but never clobber what the operator
  // is actively typing.
  useEffect(() => {
    if (document.activeElement?.id !== "tenant-input") {
      setDraft(tenantId);
    }
  }, [tenantId]);

  const canSwitchTenant = can("manage:billing"); // admin-only in the stub model

  // Preset demo accounts for one-click switching. `demo-tenant` is the seeded,
  // all-verticals demo; the others show tenant isolation (empty until they report).
  const ACCOUNTS = ["demo-tenant", "acme-foods", "northwind-retail"];

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
      <label className="flex items-center gap-2 text-xs text-muted">
        Account
        <select
          value={ACCOUNTS.includes(tenantId) ? tenantId : ""}
          disabled={!canSwitchTenant}
          onChange={(e) => e.target.value && setTenantId(e.target.value)}
          className="rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg disabled:opacity-60"
          aria-label="Account (tenant) preset"
        >
          {!ACCOUNTS.includes(tenantId) && <option value="">{tenantId} (custom)</option>}
          {ACCOUNTS.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-2 text-xs text-muted">
        Tenant
        <input
          id="tenant-input"
          value={draft}
          disabled={!canSwitchTenant}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => setTenantId(draft)}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
          className="w-40 rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg disabled:opacity-60"
          aria-label="Tenant id"
        />
      </label>

      <label className="flex items-center gap-2 text-xs text-muted">
        Refresh
        <select
          value={refreshMs}
          onChange={(e) => setRefreshMs(Number(e.target.value))}
          className="rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg"
          aria-label="Refresh interval"
        >
          {REFRESH_OPTIONS.map((o) => (
            <option key={o.ms} value={o.ms}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

/** A small live/error pulse dot + label, driven by a query's status. */
export function LivePulse({
  isError,
  isFetching,
  updatedAt,
}: {
  isError: boolean;
  isFetching: boolean;
  updatedAt?: number;
}) {
  const color = isError
    ? "bg-bad"
    : isFetching
      ? "bg-warn"
      : "bg-ok";
  const text = isError
    ? "cannot reach API"
    : updatedAt
      ? `live · updated ${new Date(updatedAt).toLocaleTimeString()}`
      : "connecting…";
  return (
    <span className="flex items-center gap-2 text-xs text-muted">
      <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
      {text}
    </span>
  );
}
