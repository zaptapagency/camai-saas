"use client";

/**
 * Cameras — the fleet, assembled from the summary: device heartbeats give FPS +
 * last-seen, occupancy samples give zones + latest counts, and the event stream
 * gives each camera's vertical(s) and last activity. One row per camera.
 */

import { ErrorState, EmptyState, LoadingState } from "@/components/StateMessage";
import { LivePulse } from "@/components/TenantControls";
import { isStale, num, timeAgo } from "@/lib/format";
import { useEvents, useSummary } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

interface Row {
  id: string;
  fps: number | null;
  lastSeen: string | null;
  device: string | null;
  zones: Set<string>;
  count: number;
  modes: Set<string>;
  lastEvent: string | null;
}

export default function CamerasPage() {
  const { tenantId } = useTenant();
  const { data, error, isPending, isError, isFetching, dataUpdatedAt } = useSummary();
  const { data: events } = useEvents(200);

  const byCam = new Map<string, Row>();
  const ensure = (id: string): Row => {
    let c = byCam.get(id);
    if (!c) {
      c = { id, fps: null, lastSeen: null, device: null, zones: new Set(), count: 0, modes: new Set(), lastEvent: null };
      byCam.set(id, c);
    }
    return c;
  };
  if (data) {
    for (const d of data.devices) {
      for (const [cam, fps] of Object.entries(d.stream_fps ?? {})) {
        const c = ensure(cam);
        c.fps = fps;
        c.lastSeen = d.ts;
        c.device = d.device_id;
      }
    }
    for (const o of data.occupancy) {
      const c = ensure(o.camera_id);
      if (o.zone_id) c.zones.add(o.zone_id);
      c.count += o.count ?? 0;
    }
  }
  for (const e of events ?? []) {
    const c = ensure(e.camera_id);
    if (e.mode) c.modes.add(e.mode);
    if (!c.lastEvent || e.ts > c.lastEvent) c.lastEvent = e.ts;
  }
  const rows = [...byCam.values()].sort((a, b) => a.id.localeCompare(b.id));
  const online = rows.filter((r) => r.fps != null && !isStale(r.lastSeen)).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">
          Cameras{" "}
          <span className="text-sm font-normal text-muted">
            · {tenantId} · {online}/{rows.length} online
          </span>
        </h1>
        <LivePulse isError={isError} isFetching={isFetching} updatedAt={dataUpdatedAt} />
      </div>

      {isError && <ErrorState error={error} />}
      {isPending && !isError && <LoadingState label="Loading cameras…" />}

      {data && (
        <div className="overflow-x-auto rounded-2xl border border-line bg-panel">
          {rows.length === 0 ? (
            <EmptyState>No cameras reporting.</EmptyState>
          ) : (
            <table className="w-full min-w-[720px] text-[13px]">
              <thead>
                <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
                  <th className="px-4 py-2.5 font-semibold">Camera</th>
                  <th className="px-4 py-2.5 font-semibold">Vertical</th>
                  <th className="px-4 py-2.5 font-semibold">Zones</th>
                  <th className="px-4 py-2.5 text-right font-semibold">Occupancy</th>
                  <th className="px-4 py-2.5 text-right font-semibold">FPS</th>
                  <th className="px-4 py-2.5 font-semibold">Status</th>
                  <th className="px-4 py-2.5 text-right font-semibold">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const up = r.fps != null && !isStale(r.lastSeen);
                  return (
                    <tr key={r.id} className="border-b border-line last:border-b-0 hover:bg-panel-2">
                      <td className="px-4 py-2.5">
                        <div className="font-medium">{r.id}</div>
                        <div className="text-xs text-muted">{r.device ?? "—"}</div>
                      </td>
                      <td className="px-4 py-2.5 capitalize">
                        {[...r.modes].join(", ") || "—"}
                      </td>
                      <td className="px-4 py-2.5 text-muted">
                        {[...r.zones].join(", ") || "—"}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{num(r.count)}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">
                        {r.fps != null ? r.fps.toFixed(1) : "—"}
                      </td>
                      <td className="px-4 py-2.5">
                        <span
                          className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${
                            up ? "bg-ok/15 text-ok" : "bg-bad/15 text-bad"
                          }`}
                        >
                          <span className={`h-1.5 w-1.5 rounded-full ${up ? "bg-ok" : "bg-bad"}`} />
                          {up ? "online" : "offline"}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-right text-muted">{timeAgo(r.lastSeen)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
