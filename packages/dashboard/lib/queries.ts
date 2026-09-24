"use client";

/**
 * TanStack Query hooks that bind the API client to the active tenant and the
 * operator-chosen poll cadence. Centralising them keeps polling behaviour
 * (interval, background refetch, retry) consistent across every page and means a
 * component only has to say *what* it needs, not *how often*.
 */

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, queryKeys } from "./api";
import { useTenant } from "./tenant";

/** Polls only while the tab is visible; `refreshMs === 0` pauses polling. */
function pollOptions(refreshMs: number) {
  return {
    refetchInterval: refreshMs > 0 ? refreshMs : (false as const),
    refetchIntervalInBackground: false,
    // Keep the last good data on screen while a refetch is in flight (and across
    // tenant switches) so tiles don't flash empty during polling.
    placeholderData: keepPreviousData,
    retry: 1,
  };
}

export function useSummary() {
  const { tenantId, refreshMs } = useTenant();
  return useQuery({
    queryKey: queryKeys.summary(tenantId),
    queryFn: ({ signal }) => api.summary(tenantId, signal),
    ...pollOptions(refreshMs),
  });
}

export function useDevices() {
  const { tenantId, refreshMs } = useTenant();
  return useQuery({
    queryKey: queryKeys.devices(tenantId),
    queryFn: ({ signal }) => api.devices(tenantId, signal),
    ...pollOptions(refreshMs),
  });
}

export function useEvents(limit = 100) {
  const { tenantId, refreshMs } = useTenant();
  return useQuery({
    queryKey: queryKeys.events(tenantId, limit),
    queryFn: ({ signal }) => api.events(tenantId, limit, signal),
    ...pollOptions(refreshMs),
  });
}

export function useUsage() {
  const { tenantId, refreshMs } = useTenant();
  return useQuery({
    queryKey: queryKeys.usage(tenantId),
    queryFn: ({ signal }) => api.usage(tenantId, signal),
    // Billing figures move slowly; poll at most once a minute regardless of the
    // dashboard cadence, but still refresh when the operator switches tenants.
    ...pollOptions(refreshMs > 0 ? Math.max(refreshMs, 60_000) : 0),
  });
}
