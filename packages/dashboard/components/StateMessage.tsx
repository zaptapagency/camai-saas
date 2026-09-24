import type { ReactNode } from "react";

import { ApiError } from "@/lib/api";

/** Muted inline note for empty states, matching the SPA's `.empty`. */
export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="px-1 py-4 text-sm text-muted">{children}</div>;
}

/** Consistent loading placeholder. */
export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="animate-pulse px-1 py-4 text-sm text-muted">{label}</div>
  );
}

/** Renders a friendly error, distinguishing "API unreachable" from HTTP errors. */
export function ErrorState({ error }: { error: unknown }) {
  let message = "Something went wrong.";
  if (error instanceof ApiError) {
    message =
      error.status === 0
        ? `Cannot reach the CamAI API (${error.message}). Check NEXT_PUBLIC_CAMAI_API_BASE and that the cloud service is running.`
        : `API error ${error.status} for ${error.url}.`;
  } else if (error instanceof Error) {
    message = error.message;
  }
  return (
    <div className="rounded-lg border border-bad/40 bg-bad/10 px-3 py-3 text-sm text-bad">
      {message}
    </div>
  );
}
