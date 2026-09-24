import type { ReactNode } from "react";

export type TileTone = "default" | "accent" | "ok" | "warn" | "bad";

const toneToValueClass: Record<TileTone, string> = {
  default: "text-fg",
  accent: "text-accent",
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
};

/**
 * A single big-number KPI tile (SPA `.tile`). `hint` is optional secondary text
 * under the value, useful for e.g. "billed this period".
 */
export function Tile({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: TileTone;
}) {
  return (
    <div className="rounded-2xl border border-line bg-panel p-4 sm:p-[18px]">
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div
        className={`mt-1.5 text-3xl font-semibold tabular-nums sm:text-[34px] ${toneToValueClass[tone]}`}
      >
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}
