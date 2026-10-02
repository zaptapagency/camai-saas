"use client";

/**
 * Overview dashboard — the tenant-scoped landing page. Everything here reads a
 * single `/summary` payload (tiles, occupancy chart, parking, device health,
 * event feed) and polls on the operator's chosen cadence via TanStack Query.
 */

import { Card } from "@/components/Card";
import { DeviceTable } from "@/components/DeviceTable";
import { EventFeed } from "@/components/EventFeed";
import { OccupancyChart } from "@/components/OccupancyChart";
import { ParkingTable } from "@/components/ParkingTable";
import { ErrorState, LoadingState } from "@/components/StateMessage";
import { LivePulse } from "@/components/TenantControls";
import { Tile } from "@/components/Tile";
import { duration, num } from "@/lib/format";
import { useSummary } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";
import { VERTICALS } from "@/lib/verticals";

export default function OverviewPage() {
  const { tenantId } = useTenant();
  const { data, error, isPending, isError, isFetching, dataUpdatedAt } =
    useSummary();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">
          Overview <span className="text-sm font-normal text-muted">· {tenantId}</span>
        </h1>
        <LivePulse
          isError={isError}
          isFetching={isFetching}
          updatedAt={dataUpdatedAt}
        />
      </div>

      {isError && <ErrorState error={error} />}
      {isPending && !isError && <LoadingState label="Loading dashboard…" />}

      {data &&
        (() => {
          const t = data.totals;
          const alertTotal =
            (t.ppe_violations ?? 0) +
            (t.capacity_breaches ?? 0) +
            (t.proximity_alerts ?? 0) +
            (t.hazard_alerts ?? 0) +
            (t.overheat_alerts ?? 0);
          return (
        <>
          <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-4">
            <Tile
              label="Active cameras (billed)"
              value={num(t.active_cameras)}
              tone="accent"
              hint="this billing period"
            />
            <Tile
              label="Open alerts"
              value={num(alertTotal)}
              tone={alertTotal > 0 ? "bad" : "ok"}
              hint="PPE · capacity · fire · thermal · proximity"
            />
            <Tile
              label="Avg wait"
              value={duration(t.avg_wait_seconds)}
              hint={
                t.wait_samples ? `${num(t.wait_samples)} in queue` : "no queue dwell yet"
              }
            />
            <Tile
              label="Avg browse"
              value={duration(t.avg_browse_seconds)}
              hint={
                t.browse_samples ? `${num(t.browse_samples)} in store` : "no retail dwell yet"
              }
            />
          </div>

          <section className="space-y-3">
            <h2 className="text-sm font-semibold text-muted">
              Verticals · live across the fleet
            </h2>
            <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-3 lg:grid-cols-5">
              {VERTICALS.map((v) => (
                <Tile
                  key={v.key}
                  label={v.name}
                  value={v.value(t)}
                  hint={v.sub?.(t)}
                  tone={v.tone}
                />
              ))}
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
            <Card title="Occupancy by camera / zone">
              <OccupancyChart data={data.occupancy} />
            </Card>
            <Card title="Parking spaces">
              <ParkingTable rows={data.parking} />
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
            <Card title="Device health">
              <DeviceTable devices={data.devices} />
            </Card>
            <Card title="Recent events">
              <EventFeed events={data.recent} />
            </Card>
          </div>
        </>
          );
        })()}
    </div>
  );
}
