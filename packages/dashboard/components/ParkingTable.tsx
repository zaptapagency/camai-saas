import type { ParkingState } from "@/lib/api";
import { EmptyState } from "./StateMessage";

/** Latest state per parking space, with a colour-coded parked/free badge. */
export function ParkingTable({ rows }: { rows: ParkingState[] }) {
  if (!rows.length) {
    return <EmptyState>No parking cameras reporting.</EmptyState>;
  }
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-[11px] uppercase text-muted">
          <th className="border-b border-line py-2 pr-2 font-medium">Camera</th>
          <th className="border-b border-line py-2 pr-2 font-medium">Space</th>
          <th className="border-b border-line py-2 font-medium">State</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p, i) => (
          <tr key={`${p.camera_id}-${p.zone_id}-${i}`}>
            <td className="border-b border-line py-2 pr-2">{p.camera_id}</td>
            <td className="border-b border-line py-2 pr-2">{p.zone_id ?? "—"}</td>
            <td className="border-b border-line py-2">
              <span
                className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                  p.state === "parked"
                    ? "bg-bad/15 text-bad"
                    : "bg-ok/15 text-ok"
                }`}
              >
                {p.state}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
