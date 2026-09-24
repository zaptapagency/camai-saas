"use client";

import { useEffect, useState } from "react";

/**
 * Reads the design-token CSS variables (defined in globals.css) at runtime so
 * chart code — which cannot use Tailwind classes — stays theme-correct and
 * updates when the OS flips between light and dark.
 */
export interface ThemeColors {
  accent: string;
  muted: string;
  line: string;
  fg: string;
  ok: string;
  warn: string;
  bad: string;
}

const FALLBACK: ThemeColors = {
  accent: "#4c8dff",
  muted: "#9aa4b2",
  line: "#2a303a",
  fg: "#e6e9ef",
  ok: "#35c46b",
  warn: "#f0a92b",
  bad: "#f0533b",
};

export function useThemeColors(): ThemeColors {
  const [colors, setColors] = useState<ThemeColors>(FALLBACK);

  useEffect(() => {
    const read = (): ThemeColors => {
      const s = getComputedStyle(document.body);
      const v = (name: string, fb: string) =>
        s.getPropertyValue(name).trim() || fb;
      return {
        accent: v("--accent", FALLBACK.accent),
        muted: v("--muted", FALLBACK.muted),
        line: v("--line", FALLBACK.line),
        fg: v("--fg", FALLBACK.fg),
        ok: v("--ok", FALLBACK.ok),
        warn: v("--warn", FALLBACK.warn),
        bad: v("--bad", FALLBACK.bad),
      };
    };
    setColors(read());

    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setColors(read());
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return colors;
}
