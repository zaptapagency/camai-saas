/**
 * Typed client for the CamAI cloud API.
 *
 * The TS types below mirror the pydantic models in packages/schema/camai_schema.py
 * and the aggregate shapes returned by packages/cloud/app/store.py. Keeping them
 * here (rather than scattering `any` through components) means a wire-format change
 * surfaces as a compile error in exactly one place.
 *
 * All calls are read-only GETs — the dashboard never writes to the cloud, mirroring
 * the outbound-only edge<->cloud contract. Auth headers are injected in one spot
 * (`authHeaders`) so wiring a real bearer token / SSO session later is a one-line
 * change.
 */

// --- Wire types (mirror camai_schema.py) ------------------------------------

export type Mode = "retail" | "warehouse" | "parking";

export type EventType =
  | "entry"
  | "exit"
  | "occupancy_sample"
  | "vehicle_parked"
  | "vehicle_left"
  | "count_delta"
  | "dwell";

export type ObjectClass =
  | "person"
  | "vehicle"
  | "forklift"
  | "pallet"
  | "other";

/** A single analytics event (camai_schema.Event). */
export interface CamEvent {
  schema_version: string;
  event_id: string;
  ts: string; // ISO-8601 UTC
  tenant_id: string;
  site_id: string;
  camera_id: string;
  type: EventType;
  mode: Mode;
  zone_id?: string | null;
  line_id?: string | null;
  object_class?: ObjectClass | null;
  track_id?: number | null;
  count?: number | null;
  delta?: number | null;
  dwell_seconds?: number | null;
  clip_ref?: string | null;
}

/** Device/stream health snapshot (camai_schema.Heartbeat). */
export interface Heartbeat {
  schema_version: string;
  device_id: string;
  tenant_id: string;
  ts: string;
  agent_version: string;
  uptime_seconds: number;
  cpu_percent?: number | null;
  gpu_percent?: number | null;
  gpu_temp_c?: number | null;
  disk_free_gb?: number | null;
  /** camera_id -> measured FPS (0.0 == dark). */
  stream_fps: Record<string, number>;
  queued_events: number;
}

// --- Aggregate/read shapes (mirror store.summary + endpoints) ---------------

export interface SummaryTotals {
  entries: number;
  exits: number;
  retail_occupancy: number;
  parking_spaces_occupied: number;
  /** Injected by the summary endpoint: cameras billed this period. */
  active_cameras?: number;
  /** Queue mode: average per-person wait time (dwell). */
  avg_wait_seconds?: number | null;
  wait_samples?: number;
  /** Retail mode: average per-shopper browse time (dwell). Split from wait. */
  avg_browse_seconds?: number | null;
  browse_samples?: number;
  /** Staffing mode: station coverage + activity (anonymous, station-level). */
  stations_total?: number;
  stations_unstaffed?: number;
  stations_active?: number;
  stations_static?: number;
}

export interface OccupancySample {
  camera_id: string;
  zone_id: string | null;
  count: number | null;
  ts: string | null;
}

export interface ParkingState {
  camera_id: string;
  zone_id: string | null;
  state: "parked" | "free";
  ts: string | null;
}

export interface Summary {
  tenant_id: string;
  totals: SummaryTotals;
  occupancy: OccupancySample[];
  parking: ParkingState[];
  devices: Heartbeat[];
  recent: CamEvent[];
}

export interface Usage {
  tenant_id: string;
  period_start: string;
  period_end: string;
  active_cameras: number;
  camera_ids: string[];
  plan: string | null;
}

// --- Client -----------------------------------------------------------------

/**
 * API base. Empty string means same-origin (dashboard reverse-proxied in front of
 * the API); a full URL points at a standalone cloud deployment. Read from the
 * public env var at module load so it is inlined into the client bundle.
 */
export const API_BASE = (process.env.NEXT_PUBLIC_CAMAI_API_BASE ?? "").replace(
  /\/$/,
  "",
);

/** Thrown on any non-2xx response so React Query can surface it in the UI. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public url: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Auth header hook. Stubbed for the pilot: no token is sent. When real auth
 * lands (bearer token from an SSO session, or an mTLS-fronted gateway), this is
 * the single place to attach `Authorization`. See README "What's stubbed".
 */
function authHeaders(): HeadersInit {
  return {};
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const url = `${API_BASE}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json", ...authHeaders() },
      signal,
      cache: "no-store",
    });
  } catch (err) {
    // Network-level failure (API down, CORS, DNS). Normalise to ApiError.
    throw new ApiError(0, url, (err as Error).message || "network error");
  }
  if (!res.ok) {
    throw new ApiError(res.status, url, `${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  summary: (tenantId: string, signal?: AbortSignal) =>
    getJson<Summary>(`/v1/tenants/${enc(tenantId)}/summary`, signal),

  events: (tenantId: string, limit = 100, signal?: AbortSignal) =>
    getJson<CamEvent[]>(
      `/v1/tenants/${enc(tenantId)}/events?limit=${limit}`,
      signal,
    ),

  devices: (tenantId: string, signal?: AbortSignal) =>
    getJson<Heartbeat[]>(`/v1/tenants/${enc(tenantId)}/devices`, signal),

  usage: (tenantId: string, signal?: AbortSignal) =>
    getJson<Usage>(`/v1/tenants/${enc(tenantId)}/usage`, signal),
};

/** Shared React Query keys so polling and manual refetches stay in sync. */
export const queryKeys = {
  summary: (tenantId: string) => ["summary", tenantId] as const,
  events: (tenantId: string, limit: number) =>
    ["events", tenantId, limit] as const,
  devices: (tenantId: string) => ["devices", tenantId] as const,
  usage: (tenantId: string) => ["usage", tenantId] as const,
};
