"use client";

/**
 * Per-camera/zone occupancy bar chart (Recharts), improving on the SPA's single
 * Chart.js canvas with responsive sizing, theme-aware colours, and hover
 * tooltips. Input is the `occupancy` array from the summary endpoint.
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { OccupancySample } from "@/lib/api";
import { EmptyState } from "./StateMessage";
import { useThemeColors } from "./useThemeColors";

export function OccupancyChart({ data }: { data: OccupancySample[] }) {
  const colors = useThemeColors();

  if (!data.length) {
    return <EmptyState>No occupancy samples yet.</EmptyState>;
  }

  const rows = data.map((o) => ({
    label: o.camera_id + (o.zone_id ? ` · ${o.zone_id}` : ""),
    count: o.count ?? 0,
  }));

  return (
    <div className="h-[220px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 4, left: -8 }}>
          <CartesianGrid stroke={colors.line} vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: colors.muted, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: colors.line }}
            interval={0}
            angle={rows.length > 4 ? -20 : 0}
            textAnchor={rows.length > 4 ? "end" : "middle"}
            height={rows.length > 4 ? 48 : 24}
          />
          <YAxis
            allowDecimals={false}
            tick={{ fill: colors.muted, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: colors.line }}
            width={36}
          />
          <Tooltip
            cursor={{ fill: colors.line, opacity: 0.4 }}
            contentStyle={{
              background: "var(--panel-2)",
              border: `1px solid ${colors.line}`,
              borderRadius: 8,
              color: colors.fg,
              fontSize: 12,
            }}
            labelStyle={{ color: colors.muted }}
          />
          <Bar dataKey="count" radius={[6, 6, 0, 0]} maxBarSize={64}>
            {rows.map((_, i) => (
              <Cell key={i} fill={colors.accent} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
