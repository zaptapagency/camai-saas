"use client";

/**
 * Fleet health page — a fuller device view than the overview card, with resource
 * columns (CPU/GPU/disk) and a quick online/offline roll-up. Reads the dedicated
 * /devices endpoint so it stays useful even when the summary aggregate is heavy.
 */

import { Card } from "@/components/Card";
import { DeviceTable } from "@/components/DeviceTable";
import { RequireCapability } from "@/components/RequireCapability";
import { ErrorState, LoadingState } from "@/components/StateMessage";
import { LivePulse } from "@/components/TenantControls";
import { Tile } from "@/components/Tile";
import { isStale, num } from "@/lib/format";
import { useDevices } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

export default function DevicesPage() {
  const { tenantId } = useTenant();
  const { data, error, isPending, isError, isFetching, dataUpdatedAt } =
    useDevices();

  const total = data?.length ?? 0;
  const online = data?.filter((d) => !isStale(d.ts)).length ?? 0;
  const queued = data?.reduce((s, d) => s + (d.queued_events ?? 0), 0) ?? 0;

  return (
    <RequireCapability cap="view:devices">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-semibold">
            Devices{" "}
            <span className="text-sm font-normal text-muted">· {tenantId}</span>
          </h1>
          <LivePulse
            isError={isError}
            isFetching={isFetching}
            updatedAt={dataUpdatedAt}
          />
        </div>

        {isError && <ErrorState error={error} />}
        {isPending && !isError && <LoadingState label="Loading fleet…" />}

        {data && (
          <>
            <div className="grid grid-cols-3 gap-3.5">
              <Tile label="Devices" value={num(total)} />
              <Tile
                label="Online"
                value={`${online}/${total}`}
                tone={online === total ? "ok" : "warn"}
              />
              <Tile
                label="Events queued (edge)"
                value={num(queued)}
                tone={queued > 0 ? "warn" : "default"}
              />
            </div>

            <Card title="All devices">
              <DeviceTable devices={data} detailed />
            </Card>
          </>
        )}
      </div>
    </RequireCapability>
  );
}
