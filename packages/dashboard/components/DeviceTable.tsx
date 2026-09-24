"use client";

/**
 * Fleet health table. Beyond the SPA's version it derives an online/stale status
 * from the heartbeat age and shows per-camera FPS with a "dark" warning when a
 * stream reports 0 fps — the two failure modes that turn into site visits if
 * missed. `detailed` adds CPU/GPU/disk columns for the dedicated /devices page.
 */

import type { Heartbeat } from "@/lib/api";
import { isStale, num, timeAgo } from "@/lib/format";
import { EmptyState } from "./StateMessage";

function StreamFps({ streams }: { streams: Record<string, number> }) {
  const entries = Object.entries(streams ?? {});
  if (!entries.length) return <span className="text-muted">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {entries.map(([cam, fps]) => (
        <span
          key={cam}
          className={`rounded px-1.5 py-0.5 text-[11px] tabular-nums ${
            fps <= 0 ? "bg-bad/15 text-bad" : "bg-panel-2 text-muted"
          }`}
          title={fps <= 0 ? "stream dark (0 fps)" : undefined}
        >
          {cam}:{fps}
        </span>
      ))}
    </span>
  );
}

function StatusDot({ ts }: { ts: string }) {
  const stale = isStale(ts);
  return (
    <span className="flex items-center gap-1.5">
      <span
        className={`inline-block h-2 w-2 rounded-full ${stale ? "bg-bad" : "bg-ok"}`}
      />
      <span className={stale ? "text-bad" : "text-ok"}>
        {stale ? "offline" : "online"}
      </span>
    </span>
  );
}

export function DeviceTable({
  devices,
  detailed = false,
}: {
  devices: Heartbeat[];
  detailed?: boolean;
}) {
  if (!devices.length) {
    return <EmptyState>No devices have checked in.</EmptyState>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase text-muted">
            <th className="border-b border-line py-2 pr-3 font-medium">Status</th>
            <th className="border-b border-line py-2 pr-3 font-medium">Device</th>
            <th className="border-b border-line py-2 pr-3 font-medium">Agent</th>
            <th className="border-b border-line py-2 pr-3 text-right font-medium">
              Queued
            </th>
            <th className="border-b border-line py-2 pr-3 font-medium">
              Streams (fps)
            </th>
            {detailed && (
              <>
                <th className="border-b border-line py-2 pr-3 text-right font-medium">
                  CPU%
                </th>
                <th className="border-b border-line py-2 pr-3 text-right font-medium">
                  GPU%
                </th>
                <th className="border-b border-line py-2 pr-3 text-right font-medium">
                  Disk free
                </th>
              </>
            )}
            <th className="border-b border-line py-2 font-medium">Last seen</th>
          </tr>
        </thead>
        <tbody>
          {devices.map((d) => (
            <tr key={d.device_id}>
              <td className="border-b border-line py-2 pr-3">
                <StatusDot ts={d.ts} />
              </td>
              <td className="border-b border-line py-2 pr-3 font-medium">
                {d.device_id}
              </td>
              <td className="border-b border-line py-2 pr-3 text-muted">
                {d.agent_version}
              </td>
              <td className="border-b border-line py-2 pr-3 text-right tabular-nums">
                {num(d.queued_events)}
              </td>
              <td className="border-b border-line py-2 pr-3">
                <StreamFps streams={d.stream_fps} />
              </td>
              {detailed && (
                <>
                  <td className="border-b border-line py-2 pr-3 text-right tabular-nums text-muted">
                    {d.cpu_percent != null ? d.cpu_percent.toFixed(0) : "—"}
                  </td>
                  <td className="border-b border-line py-2 pr-3 text-right tabular-nums text-muted">
                    {d.gpu_percent != null ? d.gpu_percent.toFixed(0) : "—"}
                  </td>
                  <td className="border-b border-line py-2 pr-3 text-right tabular-nums text-muted">
                    {d.disk_free_gb != null
                      ? `${d.disk_free_gb.toFixed(1)} GB`
                      : "—"}
                  </td>
                </>
              )}
              <td className="border-b border-line py-2 text-muted">
                {timeAgo(d.ts)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
