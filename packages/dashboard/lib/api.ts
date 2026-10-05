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

export type Mode =
  | "retail"
  | "warehouse"
  | "parking"
  | "queue"
  | "safety"
  | "traffic"
  | "staffing"
  | "capacity"
  | "proximity"
  | "fire"
  | "thermal"
  | "drive_thru"
  | "loitering"
  | "intrusion"
  | "crowd_density"
  | "tailgating"
  | "fall"
  | "weapon"
  | "abandoned_object"
  | "wrong_way";

export type EventType =
  | "entry"
  | "exit"
  | "occupancy_sample"
  | "vehicle_parked"
  | "vehicle_left"
  | "count_delta"
  | "dwell"
  | "ppe_violation"
  | "vehicle_crossing"
  | "capacity_breach"
  | "proximity_alert"
  | "hazard_alert"
  | "overheat_alert"
  | "loitering_alert"
  | "intrusion_alert"
  | "crowd_alert"
  | "tailgating_alert"
  | "fall_alert"
  | "weapon_alert"
  | "abandoned_object_alert"
  | "wrong_way_alert";

export type ObjectClass =
  | "person"
  | "vehicle"
  | "forklift"
  | "pallet"
  | "fire"
  | "smoke"
  | "weapon"
  | "gun"
  | "knife"
  | "bag"
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
  /** Small free-form tags (e.g. missing PPE items, hazard type, direction). */
  labels?: string[] | null;
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
  /** Safety mode: PPE violations. */
  ppe_violations?: number;
  /** Traffic mode: directional vehicle line-crossings. */
  vehicle_crossings?: number;
  /** Capacity mode (Tier 1): zone occupancy-limit breaches. */
  capacity_breaches?: number;
  /** Proximity mode (Tier 2): forklift↔pedestrian near-miss alerts. */
  proximity_alerts?: number;
  /** Fire mode (Tier 3): fire/smoke hazard alerts. */
  hazard_alerts?: number;
  /** Thermal mode (Tier 4): overheat/fever alerts. */
  overheat_alerts?: number;
  /** Loitering mode (Tier 1): prolonged-presence alerts. */
  loitering_alerts?: number;
  /** Intrusion mode (Tier 1): restricted/after-hours presence alerts. */
  intrusion_alerts?: number;
  /** Crowd-density mode (Tier 1): crush-risk threshold alerts. */
  crowd_alerts?: number;
  /** Tailgating mode (Tier 1): piggyback line-crossing alerts. */
  tailgating_alerts?: number;
  /** Fall mode (Tier 2): person-collapse alerts. */
  fall_alerts?: number;
  /** Weapon mode (Tier 3): gun/knife detection alerts. */
  weapon_alerts?: number;
  /** Abandoned-object mode (Tier 2): unattended-object alerts. */
  abandoned_object_alerts?: number;
  /** Wrong-way mode (Tier 1): against-the-flow vehicle alerts. */
  wrong_way_alerts?: number;
  /** Drive-thru mode (Tier 1): average vehicle service time (dwell). */
  avg_service_seconds?: number | null;
  service_samples?: number;
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

/** A time-boxed demo session (mirrors app.demo). */
export interface DemoSession {
  token: string;
  tenant_id: string;
  created_at: string;
  expires_at: string;
  seconds_remaining: number;
  expired: boolean;
}

/** Metadata for the latest opt-in annotated snapshot of a camera. */
export interface SnapshotMeta {
  camera_id: string;
  mode: string;
  ts: string;
  predicted_count: number;
  labeled_count: number | null;
}

/** One row of the accuracy flywheel report, grouped by vertical/mode. */
export interface AccuracyRow {
  mode: string;
  n: number;
  mae: number;
  mean_pct_error: number;
}

/** Aggregate predicted-vs-labeled accuracy for a tenant. */
export interface Accuracy {
  overall: { n: number; mae: number; mean_pct_error: number };
  per_mode: AccuracyRow[];
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

/** POST a JSON body and parse a JSON response, mirroring getJson's error handling. */
async function postJson<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const url = `${API_BASE}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...authHeaders(),
      },
      body: JSON.stringify(body),
      signal,
      cache: "no-store",
    });
  } catch (err) {
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

  /**
   * Latest annotated-snapshot metadata for one camera. May 404 when the camera
   * has no opt-in snapshot yet — callers treat the ApiError(404) as "no snapshot".
   */
  snapshotMeta: (tenantId: string, cameraId: string, signal?: AbortSignal) =>
    getJson<SnapshotMeta>(
      `/v1/tenants/${enc(tenantId)}/cameras/${enc(cameraId)}/snapshot`,
      signal,
    ),

  accuracy: (tenantId: string, signal?: AbortSignal) =>
    getJson<Accuracy>(`/v1/tenants/${enc(tenantId)}/accuracy`, signal),

  /** Mint a fresh time-boxed demo session (token + expiry). */
  demoStart: () => postJson<DemoSession>(`/v1/demo/start`, {}),

  /** Validate a demo token and read its countdown. Throws ApiError(410) once expired. */
  demoSession: (token: string, signal?: AbortSignal) =>
    getJson<DemoSession>(`/v1/demo/session?token=${enc(token)}`, signal),

  /** Record a human-confirmed count for the camera's latest snapshot. */
  label: (tenantId: string, cameraId: string, actual_count: number) =>
    postJson<SnapshotMeta>(
      `/v1/tenants/${enc(tenantId)}/cameras/${enc(cameraId)}/label`,
      { actual_count },
    ),

  /**
   * URL of the latest annotated JPEG for a camera. `bust` is a cache-busting
   * number advanced on the poll cadence so the <img> re-fetches a fresh frame.
   */
  snapshotImageUrl: (tenantId: string, cameraId: string, bust: number) =>
    `${API_BASE}/v1/tenants/${enc(tenantId)}/cameras/${enc(cameraId)}/snapshot.jpg?ts=${bust}`,
};

/** Shared React Query keys so polling and manual refetches stay in sync. */
export const queryKeys = {
  summary: (tenantId: string) => ["summary", tenantId] as const,
  events: (tenantId: string, limit: number) =>
    ["events", tenantId, limit] as const,
  devices: (tenantId: string) => ["devices", tenantId] as const,
  usage: (tenantId: string) => ["usage", tenantId] as const,
  snapshot: (tenantId: string, cameraId: string) =>
    ["snapshot", tenantId, cameraId] as const,
  accuracy: (tenantId: string) => ["accuracy", tenantId] as const,
};
