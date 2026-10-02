"use client";

/**
 * Verticals — every analytics vertical the fleet runs, grouped by area, each
 * showing its live headline metric from the single `/summary` payload. This is
 * the "one pipeline, many products" view.
 */

import { ErrorState, LoadingState } from "@/components/StateMessage";
import { LivePulse } from "@/components/TenantControls";
import { useSummary } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";
import { GROUPS, VERTICALS } from "@/lib/verticals";

const toneBar: Record<string, string> = {
  default: "bg-line", accent: "bg-accent", ok: "bg-ok", warn: "bg-warn", bad: "bg-bad",
};
const toneText: Record<string, string> = {
  default: "text-fg", accent: "text-accent", ok: "text-ok", warn: "text-warn", bad: "text-bad",
};

export default function VerticalsPage() {
  const { tenantId } = useTenant();
  const { data, error, isPending, isError, isFetching, dataUpdatedAt } = useSummary();

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">
          Verticals <span className="text-sm font-normal text-muted">· {tenantId}</span>
        </h1>
        <LivePulse isError={isError} isFetching={isFetching} updatedAt={dataUpdatedAt} />
      </div>

      {isError && <ErrorState error={error} />}
      {isPending && !isError && <LoadingState label="Loading verticals…" />}

      {data &&
        GROUPS.map((g) => (
          <section key={g} className="space-y-3">
            <h2 className="text-sm font-semibold text-muted">{g}</h2>
            <div className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
              {VERTICALS.filter((v) => v.group === g).map((v) => (
                <div
                  key={v.key}
                  className="relative overflow-hidden rounded-2xl border border-line bg-panel p-4"
                >
                  <span className={`absolute left-0 top-0 h-full w-1 ${toneBar[v.tone]}`} />
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-semibold">{v.name}</div>
                    <span className="rounded-md bg-panel-2 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
                      {v.tier}
                    </span>
                  </div>
                  <div className={`mt-2 text-2xl font-semibold tabular-nums ${toneText[v.tone]}`}>
                    {v.value(data.totals)}
                  </div>
                  {v.sub && <div className="mt-1 text-xs text-muted">{v.sub(data.totals)}</div>}
                </div>
              ))}
            </div>
          </section>
        ))}
    </div>
  );
}
