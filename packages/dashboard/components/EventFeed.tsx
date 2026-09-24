"use client";

/**
 * Live event feed. Each row shows the event type (colour-keyed by kind), where
 * it happened (zone or line), the relevant numeric payload (count / delta /
 * dwell), and how long ago. Improves on the SPA feed with type colouring and an
 * optional mode/object-class column for the fuller view.
 */

import type { CamEvent, EventType } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { EmptyState } from "./StateMessage";

const TYPE_TONE: Record<EventType, string> = {
  entry: "text-ok",
  exit: "text-warn",
  occupancy_sample: "text-accent",
  vehicle_parked: "text-bad",
  vehicle_left: "text-ok",
  count_delta: "text-accent",
  dwell: "text-muted",
};

function payloadLabel(e: CamEvent): string {
  if (e.count != null) return `count ${e.count}`;
  if (e.delta != null) return `Δ ${e.delta > 0 ? "+" : ""}${e.delta}`;
  if (e.dwell_seconds != null) return `dwell ${e.dwell_seconds.toFixed(0)}s`;
  return "";
}

export function EventFeed({
  events,
  showMode = false,
  maxHeight = 340,
}: {
  events: CamEvent[];
  showMode?: boolean;
  maxHeight?: number;
}) {
  if (!events.length) {
    return <EmptyState>No events yet.</EmptyState>;
  }

  return (
    <div className="overflow-auto" style={{ maxHeight }}>
      {events.map((e) => {
        const where = e.zone_id || e.line_id || "";
        return (
          <div
            key={e.event_id}
            className="flex items-center gap-2.5 border-b border-line py-1.5 text-[13px]"
          >
            <span
              className={`min-w-[128px] font-semibold ${TYPE_TONE[e.type] ?? "text-fg"}`}
            >
              {e.type}
            </span>
            <span className="text-muted">
              {e.camera_id}
              {where ? ` · ${where}` : ""}
              {showMode ? ` · ${e.mode}` : ""}
            </span>
            <span className="ml-auto tabular-nums">{payloadLabel(e)}</span>
            <span className="w-16 text-right text-muted">{timeAgo(e.ts)}</span>
          </div>
        );
      })}
    </div>
  );
}
