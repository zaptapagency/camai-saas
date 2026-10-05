"use client";

/**
 * Demo-mode chrome: a slim countdown banner while a time-boxed demo session is
 * live, and a blocking "demo ended" overlay once the 3-hour window closes. Both
 * are driven by the demo state on the tenant context (set from a ?demo= link).
 */

import { useState } from "react";

import { api } from "@/lib/api";
import { useTenant } from "@/lib/tenant";

function fmt(total: number): string {
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}h ${pad(m)}m ${pad(s)}s` : `${m}m ${pad(s)}s`;
}

export function DemoBanner() {
  const { demo } = useTenant();
  const [launching, setLaunching] = useState(false);

  if (!demo.active) return null;

  async function launchNew() {
    setLaunching(true);
    try {
      const s = await api.demoStart();
      window.location.href = `${window.location.pathname}?demo=${encodeURIComponent(s.token)}`;
    } catch {
      setLaunching(false);
    }
  }

  if (demo.expired) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/90 backdrop-blur-sm">
        <div className="mx-4 max-w-md rounded-2xl border border-line bg-panel p-8 text-center shadow-lg">
          <div className="text-2xl font-semibold">Demo ended</div>
          <p className="mt-2 text-sm text-muted">
            Your live CamAI demo window has closed. Spin up a fresh 3-hour
            session to keep exploring every vertical.
          </p>
          <button
            onClick={launchNew}
            disabled={launching}
            className="mt-6 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {launching ? "Starting…" : "Launch another 3-hour demo"}
          </button>
        </div>
      </div>
    );
  }

  const low = demo.secondsRemaining <= 300; // last 5 minutes

  return (
    <div
      className={`flex items-center justify-center gap-2 px-4 py-1.5 text-center text-xs font-medium text-white ${
        low ? "bg-bad" : "bg-accent"
      }`}
    >
      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-white/90" />
      Live demo — all verticals · expires in{" "}
      <span className="tabular-nums font-semibold">{fmt(demo.secondsRemaining)}</span>
    </div>
  );
}
