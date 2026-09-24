"use client";

/**
 * Active-tenant + refresh-cadence context.
 *
 * The pilot dashboard is single-tenant-at-a-time: an operator picks a tenant id
 * (as in the original SPA's text box) and everything on screen scopes to it. We
 * persist the selection and the poll interval in localStorage so a reload keeps
 * the operator where they were. Under real auth the tenant comes from the session
 * and the switcher becomes an admin-only override.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

interface TenantContextValue {
  tenantId: string;
  setTenantId: (t: string) => void;
  /** Poll interval in ms; 0 means paused. */
  refreshMs: number;
  setRefreshMs: (ms: number) => void;
}

const TenantContext = createContext<TenantContextValue | null>(null);

const TENANT_KEY = "camai.tenant";
const REFRESH_KEY = "camai.refreshMs";

export const DEFAULT_TENANT = "demo-tenant";
export const DEFAULT_REFRESH_MS = 10_000;

export const REFRESH_OPTIONS: { label: string; ms: number }[] = [
  { label: "5s", ms: 5_000 },
  { label: "10s", ms: 10_000 },
  { label: "30s", ms: 30_000 },
  { label: "Off", ms: 0 },
];

export function TenantProvider({ children }: { children: ReactNode }) {
  const [tenantId, setTenantIdState] = useState<string>(DEFAULT_TENANT);
  const [refreshMs, setRefreshMsState] = useState<number>(DEFAULT_REFRESH_MS);

  useEffect(() => {
    try {
      const t = window.localStorage.getItem(TENANT_KEY);
      if (t) setTenantIdState(t);
      const r = window.localStorage.getItem(REFRESH_KEY);
      if (r != null && !Number.isNaN(Number(r))) setRefreshMsState(Number(r));
    } catch {
      /* ignore blocked storage */
    }
  }, []);

  const setTenantId = useCallback((t: string) => {
    const clean = t.trim() || DEFAULT_TENANT;
    setTenantIdState(clean);
    try {
      window.localStorage.setItem(TENANT_KEY, clean);
    } catch {
      /* ignore */
    }
  }, []);

  const setRefreshMs = useCallback((ms: number) => {
    setRefreshMsState(ms);
    try {
      window.localStorage.setItem(REFRESH_KEY, String(ms));
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo<TenantContextValue>(
    () => ({ tenantId, setTenantId, refreshMs, setRefreshMs }),
    [tenantId, setTenantId, refreshMs, setRefreshMs],
  );

  return (
    <TenantContext.Provider value={value}>{children}</TenantContext.Provider>
  );
}

export function useTenant(): TenantContextValue {
  const ctx = useContext(TenantContext);
  if (!ctx) throw new Error("useTenant must be used within a TenantProvider");
  return ctx;
}
