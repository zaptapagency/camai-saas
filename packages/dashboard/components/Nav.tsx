"use client";

/**
 * Top navigation, RBAC-aware: links appear only when the current role has the
 * matching capability. A role switcher stands in for real sign-in so the gating
 * is demonstrable during the pilot.
 */

import { signIn, signOut, useSession } from "next-auth/react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useRole, type Capability } from "@/lib/roles";
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
  const { role, can } = useRole();
  const { data: session, status } = useSession();

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
          {session?.user ? (
            <div className="flex items-center gap-3 text-xs text-muted">
              <span className="flex items-center gap-1.5">
                <span className="text-fg">{session.user.email}</span>
                <span className="rounded-md bg-panel-2 px-1.5 py-0.5 text-[11px] uppercase tracking-wide text-accent">
                  {role}
                </span>
              </span>
              <button
                type="button"
                onClick={() => signOut()}
                className="rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg transition-colors hover:text-accent"
              >
                Sign out
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => signIn()}
              disabled={status === "loading"}
              className="rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg transition-colors hover:text-accent disabled:opacity-60"
            >
              Sign in
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
