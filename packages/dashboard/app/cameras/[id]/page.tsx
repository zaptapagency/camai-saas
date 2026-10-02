"use client";

/**
 * Per-camera detail — one camera's slice of the summary + event stream. We reuse
 * the derivation from the cameras table (device heartbeats give FPS + last-seen +
 * device_id, occupancy samples give zones + latest counts, the event stream gives
 * vertical(s) + last activity), but scoped to a single camera id from the route.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { Card } from "@/components/Card";
import { EventFeed } from "@/components/EventFeed";
import { ErrorState, EmptyState, LoadingState } from "@/components/StateMessage";
import { LivePulse } from "@/components/TenantControls";
import { Tile } from "@/components/Tile";
import { ApiError, api, queryKeys } from "@/lib/api";
import { isStale, num, timeAgo } from "@/lib/format";
import { useEvents, useSnapshotMeta, useSummary } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

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

      <div className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
        <LiveViewCard cameraId={id} />
        <LabelForm cameraId={id} />
      </div>

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

const SNAPSHOT_HINT =
  "No snapshot yet — enable snapshots on this camera (snapshot_seconds) to stream an annotated frame.";

/**
 * Opt-in annotated live view. Shows the latest single annotated JPEG the edge
 * agent chose to upload (not a continuous video stream), refreshed on the poll
 * cadence via a cache-busting query param, plus a manual Refresh button.
 */
function LiveViewCard({ cameraId }: { cameraId: string }) {
  const { tenantId, refreshMs } = useTenant();
  const [bust, setBust] = useState(() => Date.now());
  const [imgError, setImgError] = useState(false);

  const refresh = () => {
    setImgError(false);
    setBust(Date.now());
  };

  // Advance the cache-buster on the poll cadence (min 2s). Paused when the
  // operator sets Refresh to Off; the manual button still works.
  useEffect(() => {
    if (refreshMs <= 0) return;
    const ms = Math.max(refreshMs, 2000);
    const t = setInterval(() => {
      setImgError(false);
      setBust(Date.now());
    }, ms);
    return () => clearInterval(t);
  }, [refreshMs]);

  const { data: meta, isError: metaIsError, error: metaError } =
    useSnapshotMeta(cameraId);
  const noSnapshot =
    imgError || (metaIsError && metaError instanceof ApiError && metaError.status === 404);

  return (
    <Card
      title="Live view"
      actions={
        <button
          type="button"
          onClick={refresh}
          className="rounded-lg border border-line bg-panel-2 px-2.5 py-1 text-xs text-fg hover:text-accent"
        >
          Refresh
        </button>
      }
    >
      {noSnapshot ? (
        <EmptyState>{SNAPSHOT_HINT}</EmptyState>
      ) : (
        <div className="space-y-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={api.snapshotImageUrl(tenantId, cameraId, bust)}
            alt={`Latest annotated snapshot for ${cameraId}`}
            onError={() => setImgError(true)}
            className="max-w-full rounded-2xl border border-line"
          />
          <div className="flex items-center justify-between text-[13px] text-muted">
            <span>
              Predicted count:{" "}
              <span className="tabular-nums text-fg">
                {meta ? num(meta.predicted_count) : "—"}
              </span>
            </span>
            <span>{meta ? timeAgo(meta.ts) : "—"}</span>
          </div>
          <p className="text-xs text-muted">
            Opt-in annotated snapshot — a single frame the camera chose to share,
            not a continuous stream.
          </p>
        </div>
      )}
    </Card>
  );
}

/**
 * Human-in-the-loop label form: an operator confirms the true count for the
 * latest snapshot, feeding the accuracy flywheel. On success we invalidate the
 * accuracy + snapshot queries so the numbers refresh.
 */
function LabelForm({ cameraId }: { cameraId: string }) {
  const { tenantId } = useTenant();
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const n = Number(value);
    if (value.trim() === "" || Number.isNaN(n) || n < 0) {
      setErr("Enter a non-negative count.");
      setDone(false);
      return;
    }
    setSubmitting(true);
    setErr(null);
    setDone(false);
    try {
      await api.label(tenantId, cameraId, n);
      void qc.invalidateQueries({ queryKey: queryKeys.accuracy(tenantId) });
      void qc.invalidateQueries({
        queryKey: queryKeys.snapshot(tenantId, cameraId),
      });
      setDone(true);
      setValue("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to submit label.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card title="Confirm count">
      <form onSubmit={onSubmit} className="space-y-3">
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          Actual count
          <input
            type="number"
            min={0}
            step={1}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              setDone(false);
              setErr(null);
            }}
            className="w-32 rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg tabular-nums"
            aria-label="Actual count"
          />
        </label>
        <button
          type="submit"
          disabled={submitting}
          className="rounded-lg border border-line bg-panel-2 px-3 py-1.5 text-sm text-fg hover:text-accent disabled:opacity-60"
        >
          {submitting ? "Submitting…" : "Submit label"}
        </button>
        {done && (
          <p className="text-xs text-ok">Label recorded — thanks, it trains the model.</p>
        )}
        {err && <p className="text-xs text-bad">{err}</p>}
        <p className="text-xs text-muted">
          Your confirmed count is compared against the model&apos;s prediction to
          track accuracy over time.
        </p>
      </form>
    </Card>
  );
}
