/**
 * The vertical catalog — one definition per analytics vertical the pipeline runs,
 * mapping each to the summary field that headlines it. Shared by the Overview KPI
 * grid and the Verticals page so the two never drift. `value`/`sub` are pure
 * formatters over SummaryTotals.
 */

import type { SummaryTotals } from "./api";
import { duration, num } from "./format";
import type { TileTone } from "@/components/Tile";

export interface VerticalDef {
  key: string;
  name: string;
  group: string;
  tier: "Core" | "Tier 1" | "Tier 2" | "Tier 3" | "Tier 4";
  tone: TileTone;
  value: (t: SummaryTotals) => string;
  sub?: (t: SummaryTotals) => string;
}

export const GROUPS = ["Front of house", "Kitchen & safety", "Fleet & outside", "Security"] as const;

export const VERTICALS: VerticalDef[] = [
  {
    key: "retail", name: "Retail foot traffic", group: "Front of house",
    tier: "Core", tone: "accent",
    value: (t) => `${num(t.retail_occupancy)} in store`,
    sub: (t) => `${num(t.entries)} in · ${num(t.exits)} out`,
  },
  {
    key: "queue", name: "Queue wait", group: "Front of house",
    tier: "Core", tone: "warn",
    value: (t) => duration(t.avg_wait_seconds),
    sub: (t) => (t.wait_samples ? `${num(t.wait_samples)} sampled` : "no queue dwell"),
  },
  {
    key: "capacity", name: "Capacity limit", group: "Front of house",
    tier: "Tier 1", tone: "bad",
    value: (t) => `${num(t.capacity_breaches)} breaches`,
    sub: () => "zone occupancy vs limit",
  },
  {
    key: "staffing", name: "Staffing coverage", group: "Kitchen & safety",
    tier: "Core", tone: "ok",
    value: (t) => `${num(t.stations_active)}/${num(t.stations_total)} active`,
    sub: (t) => `${num(t.stations_unstaffed)} unstaffed · ${num(t.stations_static)} idle`,
  },
  {
    key: "safety", name: "PPE compliance", group: "Kitchen & safety",
    tier: "Core", tone: "warn",
    value: (t) => `${num(t.ppe_violations)} violations`,
    sub: () => "helmet / vest in required zones",
  },
  {
    key: "fire", name: "Fire / smoke", group: "Kitchen & safety",
    tier: "Tier 3", tone: "bad",
    value: (t) => `${num(t.hazard_alerts)} alerts`,
    sub: () => "needs a fire/smoke model",
  },
  {
    key: "thermal", name: "Thermal / overheat", group: "Kitchen & safety",
    tier: "Tier 4", tone: "bad",
    value: (t) => `${num(t.overheat_alerts)} alerts`,
    sub: () => "needs a thermal camera",
  },
  {
    key: "parking", name: "Parking occupancy", group: "Fleet & outside",
    tier: "Core", tone: "accent",
    value: (t) => `${num(t.parking_spaces_occupied)} occupied`,
    sub: () => "per-space state",
  },
  {
    key: "traffic", name: "Traffic flow", group: "Fleet & outside",
    tier: "Core", tone: "accent",
    value: (t) => `${num(t.vehicle_crossings)} crossings`,
    sub: () => "directional vehicle counts",
  },
  {
    key: "proximity", name: "Forklift proximity", group: "Fleet & outside",
    tier: "Tier 2", tone: "bad",
    value: (t) => `${num(t.proximity_alerts)} near-misses`,
    sub: () => "forklift ↔ pedestrian",
  },
  {
    key: "drive_thru", name: "Drive-thru timing", group: "Fleet & outside",
    tier: "Tier 1", tone: "accent",
    value: (t) => duration(t.avg_service_seconds),
    sub: (t) => (t.service_samples ? `${num(t.service_samples)} vehicles` : "avg service time"),
  },
  {
    key: "crowd_density", name: "Crowd density", group: "Front of house",
    tier: "Tier 1", tone: "bad",
    value: (t) => `${num(t.crowd_alerts)} crush alerts`,
    sub: () => "headcount vs crush threshold",
  },
  {
    key: "loitering", name: "Loitering", group: "Security",
    tier: "Tier 1", tone: "warn",
    value: (t) => `${num(t.loitering_alerts)} alerts`,
    sub: () => "prolonged presence in a zone",
  },
  {
    key: "intrusion", name: "Intrusion", group: "Security",
    tier: "Tier 1", tone: "bad",
    value: (t) => `${num(t.intrusion_alerts)} alerts`,
    sub: () => "restricted / after-hours zone",
  },
  {
    key: "tailgating", name: "Tailgating", group: "Security",
    tier: "Tier 1", tone: "warn",
    value: (t) => `${num(t.tailgating_alerts)} alerts`,
    sub: () => "piggybacking a secure line",
  },
  {
    key: "fall", name: "Fall / slip", group: "Kitchen & safety",
    tier: "Tier 2", tone: "bad",
    value: (t) => `${num(t.fall_alerts)} alerts`,
    sub: () => "person collapsed on the floor",
  },
  {
    key: "weapon", name: "Weapon", group: "Security",
    tier: "Tier 3", tone: "bad",
    value: (t) => `${num(t.weapon_alerts)} alerts`,
    sub: () => "needs a weapon-trained model",
  },
  {
    key: "abandoned_object", name: "Abandoned object", group: "Security",
    tier: "Tier 2", tone: "warn",
    value: (t) => `${num(t.abandoned_object_alerts)} alerts`,
    sub: () => "unattended bag left in a zone",
  },
  {
    key: "wrong_way", name: "Wrong-way driving", group: "Fleet & outside",
    tier: "Tier 1", tone: "bad",
    value: (t) => `${num(t.wrong_way_alerts)} alerts`,
    sub: () => "vehicle against the allowed flow",
  },
];

/** The event types that represent an operational alert, for the Alerts view. */
export const ALERT_TYPES = [
  "ppe_violation",
  "capacity_breach",
  "proximity_alert",
  "hazard_alert",
  "overheat_alert",
  "vehicle_crossing",
  "fall_alert",
  "weapon_alert",
  "abandoned_object_alert",
  "wrong_way_alert",
] as const;

export const ALERT_SEVERITY: Record<string, "crit" | "warn" | "info"> = {
  hazard_alert: "crit",
  overheat_alert: "crit",
  proximity_alert: "crit",
  capacity_breach: "crit",
  weapon_alert: "crit",
  fall_alert: "crit",
  wrong_way_alert: "warn",
  abandoned_object_alert: "warn",
  ppe_violation: "warn",
  vehicle_crossing: "info",
};
