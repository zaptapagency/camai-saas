"use client";

/**
 * Client-side route guard for the stub RBAC model. Renders children only when the
 * current role has the capability; otherwise shows a "not authorised" notice.
 *
 * NOTE: this is UX gating, not security. Real enforcement must live server-side
 * (the cloud API authorising the session's role) — see README "What's stubbed".
 */

import type { ReactNode } from "react";

import { useRole, type Capability } from "@/lib/roles";

export function RequireCapability({
  cap,
  children,
}: {
  cap: Capability;
  children: ReactNode;
}) {
  const { can, role } = useRole();
  if (!can(cap)) {
    return (
      <div className="rounded-2xl border border-line bg-panel p-6 text-sm text-muted">
        Your role (<span className="font-medium text-fg">{role}</span>) does not
        have access to this page.
      </div>
    );
  }
  return <>{children}</>;
}
