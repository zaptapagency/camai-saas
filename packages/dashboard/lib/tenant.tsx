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
  useRef,
  useState,
  type ReactNode,
} from "react";

import { api } from "@/lib/api";

/** The shared, time-boxed demo tenant (mirrors app.demo.DEMO_TENANT). */
export const DEMO_TENANT = "demo-master";

export interface DemoState {
  /** True while a ?demo= token is driving the session (view is locked). */
  active: boolean;
  token: string | null;
  expiresAt: number | null; // epoch ms
  secondsRemaining: number;
  /** The window has closed (or the token was rejected). */
  expired: boolean;
}

interface TenantContextValue {
  tenantId: string;
  setTenantId: (t: string) => void;
  /** Poll interval in ms; 0 means paused. */
  refreshMs: number;
  setRefreshMs: (ms: number) => void;
  /** Time-boxed demo session state. `active` locks the tenant switcher. */
  demo: DemoState;
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

const NO_DEMO: DemoState = {
  active: false,
  token: null,
  expiresAt: null,
  secondsRemaining: 0,
  expired: false,
};

export function TenantProvider({ children }: { children: ReactNode }) {
  const [tenantId, setTenantIdState] = useState<string>(DEFAULT_TENANT);
  const [refreshMs, setRefreshMsState] = useState<number>(DEFAULT_REFRESH_MS);
  const [demo, setDemo] = useState<DemoState>(NO_DEMO);
  const demoActive = useRef(false);

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

  // Demo mode: a ?demo=<token> link locks the dashboard to the shared demo
  // tenant for a fixed window. Validate the token, then pin the view and count
  // down locally (one server check on load is enough; the banner ticks client-side).
  useEffect(() => {
    let token: string | null = null;
    try {
      token = new URLSearchParams(window.location.search).get("demo");
    } catch {
      /* ignore */
    }
    if (!token) return;
    demoActive.current = true;
    setTenantIdState(DEMO_TENANT);

    let cancelled = false;
    api
      .demoSession(token)
      .then((s) => {
        if (cancelled) return;
        setDemo({
          active: true,
          token,
          expiresAt: Date.parse(s.expires_at),
          secondsRemaining: s.seconds_remaining,
          expired: false,
        });
      })
      .catch(() => {
        if (!cancelled) setDemo({ ...NO_DEMO, active: true, token, expired: true });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Client-side countdown ticker; flips `expired` when the window closes.
  useEffect(() => {
    if (!demo.active || demo.expired || demo.expiresAt == null) return;
    const tick = () => {
      const remaining = Math.max(0, Math.round((demo.expiresAt! - Date.now()) / 1000));
      setDemo((d) => ({ ...d, secondsRemaining: remaining, expired: remaining <= 0 }));
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [demo.active, demo.expired, demo.expiresAt]);

  const setTenantId = useCallback((t: string) => {
    if (demoActive.current) return; // locked to the demo tenant
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
    () => ({ tenantId, setTenantId, refreshMs, setRefreshMs, demo }),
    [tenantId, setTenantId, refreshMs, setRefreshMs, demo],
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
