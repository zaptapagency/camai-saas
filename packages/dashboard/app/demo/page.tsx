"use client";

/**
 * /demo — the entry point the public "Launch 3-hour live demo" button links to.
 *
 * It mints a demo session *same-origin* (the dashboard proxies /v1/* to the cloud
 * API, so there is no cross-origin fetch and no CORS to configure), then redirects
 * to the dashboard pinned to that session via `/?demo=<token>`. Keeping the token
 * mint on the dashboard side is what lets the GitHub Pages landing page stay a
 * plain static link — it only needs to know the dashboard URL.
 */

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

export default function DemoLaunchPage() {
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .demoStart()
      .then((s) => {
        if (!cancelled) window.location.replace(`/?demo=${encodeURIComponent(s.token)}`);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="flex min-h-[70vh] flex-col items-center justify-center gap-4 px-4 text-center">
      {error ? (
        <>
          <div className="text-xl font-semibold">Couldn’t start the demo</div>
          <p className="max-w-sm text-sm text-muted">
            The demo backend didn’t respond. Please try again in a moment.
          </p>
          <button
            onClick={() => window.location.reload()}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:opacity-90"
          >
            Retry
          </button>
        </>
      ) : (
        <>
          <span className="h-8 w-8 animate-spin rounded-full border-2 border-line border-t-accent" />
          <div className="text-lg font-semibold">
            Starting your live demo<span className="text-accent">…</span>
          </div>
          <p className="text-sm text-muted">Spinning up all verticals for the next 3 hours.</p>
        </>
      )}
    </main>
  );
}
