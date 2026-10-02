"use client";

/**
 * Alerts — the live operational-alert stream. Filters the event feed down to the
 * alert-class events (PPE, capacity, proximity, fire, thermal, traffic), colours
 * each by severity, and headlines the per-type counts from the summary totals.
 */

import { useState } from "react";

import { ErrorState, EmptyState, LoadingState } from "@/components/StateMessage";
import { LivePulse } from "@/components/TenantControls";
import { Tile } from "@/components/Tile";
import { num, timeAgo } from "@/lib/format";
import { useEvents, useSummary } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";
import { ALERT_SEVERITY, ALERT_TYPES } from "@/lib/verticals";

const SEV_DOT: Record<string, string> = { crit: "bg-bad", warn: "bg-warn", info: "bg-accent" };
const SEV_LABEL: Record<string, string> = { crit: "critical", warn: "warning", info: "info" };
type SevFilter = "all" | "crit" | "warn" | "info";

export default function AlertsPage() {
  const { tenantId } = useTenant();
  const { data: summary } = useSummary();
  const { data: events, error, isPending, isError, isFetching, dataUpdatedAt } = useEvents(200);
  const [sev, setSev] = useState<SevFilter>("all");

  const alerts = (events ?? []).filter((e) =>
    (ALERT_TYPES as readonly string[]).includes(e.type),
  );
  const shown = sev === "all" ? alerts : alerts.filter((e) => ALERT_SEVERITY[e.type] === sev);

  const t = summary?.totals;
  const counts: { label: string; value: number | undefined; tone: "bad" | "warn" | "accent" }[] = [
    { label: "Fire / smoke", value: t?.hazard_alerts, tone: "bad" },
    { label: "Overheat", value: t?.overheat_alerts, tone: "bad" },
    { label: "Proximity", value: t?.proximity_alerts, tone: "bad" },
    { label: "Capacity", value: t?.capacity_breaches, tone: "bad" },
    { label: "PPE", value: t?.ppe_violations, tone: "warn" },
    { label: "Vehicle crossings", value: t?.vehicle_crossings, tone: "accent" },
  ];

  const filters: SevFilter[] = ["all", "crit", "warn", "info"];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">
          Alerts <span className="text-sm font-normal text-muted">· {tenantId}</span>
        </h1>
        <LivePulse isError={isError} isFetching={isFetching} updatedAt={dataUpdatedAt} />
      </div>

      <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-3 lg:grid-cols-6">
        {counts.map((c) => (
          <Tile key={c.label} label={c.label} value={num(c.value)} tone={c.tone} />
        ))}
      </div>

      <div className="flex items-center gap-2">
        {filters.map((f) => (
          <button
            key={f}
            onClick={() => setSev(f)}
            className={`rounded-full px-3 py-1 text-xs capitalize transition-colors ${
              sev === f
                ? "bg-accent text-white"
                : "border border-line bg-panel text-muted hover:text-fg"
            }`}
          >
            {f === "all" ? "all" : SEV_LABEL[f]}
          </button>
        ))}
        <span className="ml-auto text-xs text-muted">{shown.length} shown</span>
      </div>

      {isError && <ErrorState error={error} />}
      {isPending && !isError && <LoadingState label="Loading alerts…" />}

      {events && (
        <div className="rounded-2xl border border-line bg-panel">
          {shown.length === 0 ? (
            <EmptyState>No alerts match this filter.</EmptyState>
          ) : (
            shown.map((e) => {
              const s = ALERT_SEVERITY[e.type] ?? "info";
              return (
                <div
                  key={e.event_id}
                  className="flex items-start gap-3 border-b border-line px-4 py-2.5 text-[13px] last:border-b-0"
                >
                  <span className={`mt-1 inline-block h-2 w-2 rounded-full ${SEV_DOT[s]}`} />
                  <div className="min-w-0">
                    <div className="font-medium">
                      {e.type.replace(/_/g, " ")}
                      {e.labels?.length ? (
                        <span className="text-muted"> · {e.labels.join(", ")}</span>
                      ) : null}
                    </div>
                    <div className="text-xs text-muted">
                      {e.camera_id}
                      {e.zone_id ? ` · ${e.zone_id}` : ""} · {e.mode}
                    </div>
                  </div>
                  <span className="ml-auto whitespace-nowrap text-xs text-muted">
                    {timeAgo(e.ts)}
                  </span>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
