"use client";

/**
 * Per-camera detail — one camera's slice of the summary + event stream. We reuse
 * the derivation from the cameras table (device heartbeats give FPS + last-seen +
 * device_id, occupancy samples give zones + latest counts, the event stream gives
 * vertical(s) + last activity), but scoped to a single camera id from the route.
 */

import Link from "next/link";
import { useParams } from "next/navigation";

import { Card } from "@/components/Card";
import { EventFeed } from "@/components/EventFeed";
import { ErrorState, EmptyState, LoadingState } from "@/components/StateMessage";
import { LivePulse } from "@/components/TenantControls";
import { Tile } from "@/components/Tile";
import { isStale, num, timeAgo } from "@/lib/format";
import { useEvents, useSummary } from "@/lib/queries";

export default function CameraDetailPage() {
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params.id);

  const { data, error, isPending, isError, isFetching, dataUpdatedAt } = useSummary();
  const { data: events } = useEvents(200);

  // --- Derive this camera's data (mirrors cameras/page.tsx, single id) --------
  let fps: number | null = null;
  let lastSeen: string | null = null;
  let device: string | null = null;
  const zoneCounts = new Map<string, number>();
  const modes = new Set<string>();
  let lastEvent: string | null = null;
  let seen = false;

  if (data) {
    for (const d of data.devices) {
      const f = d.stream_fps?.[id];
      if (f !== undefined) {
        fps = f;
        lastSeen = d.ts;
        device = d.device_id;
        seen = true;
      }
    }
    for (const o of data.occupancy) {
      if (o.camera_id !== id) continue;
      seen = true;
      if (o.zone_id) zoneCounts.set(o.zone_id, o.count ?? 0);
    }
  }

  const thisCamEvents = (events ?? []).filter((e) => e.camera_id === id);
  for (const e of thisCamEvents) {
    seen = true;
    if (e.mode) modes.add(e.mode);
    if (!lastEvent || e.ts > lastEvent) lastEvent = e.ts;
  }

  const zones = [...zoneCounts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  const up = fps != null && !isStale(lastSeen);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <Link href="/cameras" className="text-xs text-muted hover:text-accent">
            ← Cameras
          </Link>
          <h1 className="flex items-center gap-3 text-xl font-semibold">
            {id}
            {data && seen && (
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${
                  up ? "bg-ok/15 text-ok" : "bg-bad/15 text-bad"
                }`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${up ? "bg-ok" : "bg-bad"}`} />
                {up ? "online" : "offline"}
              </span>
            )}
          </h1>
        </div>
        <LivePulse isError={isError} isFetching={isFetching} updatedAt={dataUpdatedAt} />
      </div>

      {isError && <ErrorState error={error} />}
      {isPending && !isError && <LoadingState label="Loading camera…" />}

      {data && !seen && (
        <EmptyState>No data for this camera yet.</EmptyState>
      )}

      {data && seen && (
        <>
          <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-5">
            <Tile label="Status" value={up ? "online" : "offline"} tone={up ? "ok" : "bad"} />
            <Tile
              label="Streaming FPS"
              value={fps != null ? fps.toFixed(1) : "—"}
              hint={device ?? undefined}
            />
            <Tile label="Vertical(s)" value={[...modes].join(", ") || "—"} />
            <Tile label="Zones" value={num(zones.length)} />
            <Tile label="Last seen" value={timeAgo(lastSeen)} hint={lastEvent ? `activity ${timeAgo(lastEvent)}` : undefined} />
          </div>

          <div className="grid gap-4 lg:grid-cols-[1fr_1.3fr]">
            <Card title="Zones">
              {zones.length === 0 ? (
                <EmptyState>No zones reporting.</EmptyState>
              ) : (
                <div>
                  {zones.map(([zone, count]) => (
                    <div
                      key={zone}
                      className="flex items-center justify-between border-b border-line py-1.5 text-[13px] last:border-b-0"
                    >
                      <span className="text-muted">{zone}</span>
                      <span className="tabular-nums">{num(count)}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
            <Card title="Recent events">
              <EventFeed events={thisCamEvents} showMode />
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
