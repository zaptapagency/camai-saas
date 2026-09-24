/** Small presentation helpers shared across components. */

/** Human "time ago" from an ISO timestamp, matching the original SPA's `ago()`. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "—";
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/** Whole-number formatting with thousands separators; `—` for null/undefined. */
export function num(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString();
}

/**
 * Format a duration in seconds as `45s` or `3m 20s` (mirrors the SPA's `fmtWait`),
 * for the avg-wait / avg-browse dwell tiles. `—` for null/undefined.
 */
export function duration(s: number | null | undefined): string {
  if (s == null || Number.isNaN(s)) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return `${m}m ${r}s`;
}

/**
 * A device is considered stale (offline) if we have not heard from it recently.
 * 90s covers a comfortable multiple of the edge heartbeat cadence.
 */
export const STALE_AFTER_S = 90;

export function isStale(iso: string | null | undefined): boolean {
  if (!iso) return true;
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return true;
  return (Date.now() - ms) / 1000 > STALE_AFTER_S;
}

/** Format an ISO date as a short local date (used on the usage/billing page). */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "—";
  return new Date(ms).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
