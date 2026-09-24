"use client";

/**
 * Usage / billing page. Surfaces the metered unit — active cameras in the current
 * billing period — plus the plan and the exact camera ids being billed, so a
 * customer can reconcile their invoice themselves. Transparency here is the
 * cheapest way to avoid billing disputes (mirrors the cloud usage endpoint's
 * rationale). The camera-id breakdown is admin-only in the stub RBAC model.
 */

import { Card } from "@/components/Card";
import { RequireCapability } from "@/components/RequireCapability";
import { ErrorState, LoadingState } from "@/components/StateMessage";
import { LivePulse } from "@/components/TenantControls";
import { Tile } from "@/components/Tile";
import { num, shortDate } from "@/lib/format";
import { useUsage } from "@/lib/queries";
import { useRole } from "@/lib/roles";
import { useTenant } from "@/lib/tenant";

export default function UsagePage() {
  const { tenantId } = useTenant();
  const { can } = useRole();
  const { data, error, isPending, isError, isFetching, dataUpdatedAt } =
    useUsage();

  return (
    <RequireCapability cap="view:usage">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-semibold">
            Usage{" "}
            <span className="text-sm font-normal text-muted">· {tenantId}</span>
          </h1>
          <LivePulse
            isError={isError}
            isFetching={isFetching}
            updatedAt={dataUpdatedAt}
          />
        </div>

        {isError && <ErrorState error={error} />}
        {isPending && !isError && <LoadingState label="Loading usage…" />}

        {data && (
          <>
            <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-3">
              <Tile
                label="Active cameras (billed)"
                value={num(data.active_cameras)}
                tone="accent"
              />
              <Tile label="Plan" value={data.plan ?? "—"} />
              <Tile
                label="Billing period"
                value={
                  <span className="text-base font-medium">
                    {shortDate(data.period_start)}
                  </span>
                }
                hint={`through ${shortDate(data.period_end)}`}
              />
            </div>

            <Card title="Metered cameras this period">
              <p className="mb-3 text-sm text-muted">
                A camera is billed for the period if its edge agent emitted at
                least one event during it.
              </p>
              {can("manage:billing") ? (
                data.camera_ids.length ? (
                  <div className="flex flex-wrap gap-2">
                    {data.camera_ids.map((id) => (
                      <span
                        key={id}
                        className="rounded-lg border border-line bg-panel-2 px-2.5 py-1 text-xs tabular-nums"
                      >
                        {id}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted">
                    No cameras reported activity this period.
                  </p>
                )
              ) : (
                <p className="text-sm text-muted">
                  {num(data.active_cameras)} camera
                  {data.active_cameras === 1 ? "" : "s"} billed. The per-camera
                  breakdown is visible to admins.
                </p>
              )}
            </Card>
          </>
        )}
      </div>
    </RequireCapability>
  );
}
