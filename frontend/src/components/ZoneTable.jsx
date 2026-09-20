import { EmptyState, fmt } from "./ui.jsx";

export default function ZoneTable({ zones }) {
  return (
    <section className="card overflow-hidden">
      <h2 className="mb-3 font-semibold">Zone performance</h2>
      {zones.length === 0 ? (
        <EmptyState label="No zone analytics yet. Seed the network first." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-slate-400">
              <tr>
                <th className="pb-2 font-medium">Zone</th>
                <th className="pb-2 font-medium">Type</th>
                <th className="pb-2 font-medium">Bins</th>
                <th className="pb-2 font-medium">Avg fill</th>
                <th className="pb-2 font-medium">kg/bin/day</th>
                <th className="pb-2 font-medium">Overflows</th>
                <th className="pb-2 font-medium">Alerts</th>
              </tr>
            </thead>
            <tbody>
              {zones.map((zone, index) => (
                <tr key={zone.zone_id} className="border-t border-line">
                  <td className="py-2">
                    <span className="mr-2 text-slate-500">{index + 1}</span>
                    {zone.zone_name}
                  </td>
                  <td className="py-2 capitalize">{zone.zone_type.replace("_", " ")}</td>
                  <td className="py-2">{zone.bins}</td>
                  <td className="py-2">{fmt(zone.avg_fill_level, 1, "%")}</td>
                  <td className="py-2">{fmt(zone.kg_per_bin_per_day, 2)}</td>
                  <td className="py-2">{zone.overflow_events}</td>
                  <td className="py-2">{zone.open_alerts}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
