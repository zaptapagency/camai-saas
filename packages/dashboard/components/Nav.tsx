"use client";

/**
 * Top navigation, RBAC-aware: links appear only when the current role has the
 * matching capability. A role switcher stands in for real sign-in so the gating
 * is demonstrable during the pilot.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ROLES, useRole, type Capability } from "@/lib/roles";
import { TenantControls } from "./TenantControls";

interface NavItem {
  href: string;
  label: string;
  cap: Capability;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Overview", cap: "view:dashboard" },
  { href: "/verticals", label: "Verticals", cap: "view:dashboard" },
  { href: "/cameras", label: "Cameras", cap: "view:dashboard" },
  { href: "/alerts", label: "Alerts", cap: "view:dashboard" },
  { href: "/devices", label: "Devices", cap: "view:devices" },
  { href: "/usage", label: "Usage", cap: "view:usage" },
];

export function Nav() {
  const pathname = usePathname();
  const { role, setRole, can } = useRole();

  return (
    <header className="border-b border-line bg-panel">
      <div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-5">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          Cam<span className="text-accent">AI</span>
        </Link>

        <nav className="flex items-center gap-1">
          {NAV_ITEMS.filter((item) => can(item.cap)).map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                  active
                    ? "bg-panel-2 text-fg"
                    : "text-muted hover:text-fg"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex flex-wrap items-center gap-x-5 gap-y-2">
          <TenantControls />
          <label className="flex items-center gap-2 text-xs text-muted">
            Role
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as (typeof ROLES)[number])}
              className="rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg"
              aria-label="Signed-in role (stub)"
              title="Stub auth: role selection stands in for real SSO"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
    </header>
  );
}
