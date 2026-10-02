"use client";

/**
 * Stub RBAC layer.
 *
 * The dashboard is designed for three roles — admin, manager, viewer — but there
 * is no real identity provider wired yet. This context fakes a signed-in user and
 * persists the chosen role in localStorage so the nav/pages can gate features
 * exactly as they will under real auth. Swapping in SSO means replacing
 * `RoleProvider`'s source of `role` with the session claim; every `can(...)`
 * call site stays unchanged. See README "What's stubbed".
 */

import { useSession } from "next-auth/react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Role = "admin" | "manager" | "viewer";

export const ROLES: Role[] = ["admin", "manager", "viewer"];

/** Capabilities gated by role. Kept coarse for the pilot. */
export type Capability =
  | "view:dashboard"
  | "view:devices"
  | "view:usage" // billing figures
  | "manage:billing"; // future: change plan, download invoices

const CAPABILITIES: Record<Role, Capability[]> = {
  admin: ["view:dashboard", "view:devices", "view:usage", "manage:billing"],
  manager: ["view:dashboard", "view:devices", "view:usage"],
  viewer: ["view:dashboard", "view:devices"],
};

interface RoleContextValue {
  role: Role;
  setRole: (r: Role) => void;
  can: (cap: Capability) => boolean;
}

const RoleContext = createContext<RoleContextValue | null>(null);

const STORAGE_KEY = "camai.role";

export function RoleProvider({ children }: { children: ReactNode }) {
  // When a NextAuth session is present its role claim wins (read-only). When
  // signed out we keep the original localStorage stub behaviour untouched, so
  // the app still works without authenticating.
  const { data: session } = useSession();
  const sessionRole = session?.user?.role;

  const [role, setRoleState] = useState<Role>("admin");

  // Hydrate the persisted role after mount to avoid SSR/CSR mismatch.
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved && (ROLES as string[]).includes(saved)) {
        setRoleState(saved as Role);
      }
    } catch {
      /* private mode / blocked storage — fall back to the default role */
    }
  }, []);

  const setRole = useCallback(
    (r: Role) => {
      // No-op when a session supplies the role — identity is the source of truth.
      if (sessionRole) return;
      setRoleState(r);
      try {
        window.localStorage.setItem(STORAGE_KEY, r);
      } catch {
        /* ignore persistence failures */
      }
    },
    [sessionRole],
  );

  // Effective role: session claim if signed in, otherwise the stub/localStorage role.
  const effectiveRole = sessionRole ?? role;

  const value = useMemo<RoleContextValue>(
    () => ({
      role: effectiveRole,
      setRole,
      can: (cap) => CAPABILITIES[effectiveRole].includes(cap),
    }),
    [effectiveRole, setRole],
  );

  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}

export function useRole(): RoleContextValue {
  const ctx = useContext(RoleContext);
  if (!ctx) throw new Error("useRole must be used within a RoleProvider");
  return ctx;
}
