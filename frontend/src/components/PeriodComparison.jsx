import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { EmptyState, fmt } from "./ui.jsx";

export default function PeriodComparison({ current, previous, loading, days }) {
  if (loading) {
    return (
      <section className="card">
        <p className="muted py-6 text-center">Loading previous period…</p>
      </section>
    );
  }
  if (!previous) {
    return (
      <section className="card">
        <EmptyState label="Previous-period comparison is unavailable for this window." />
      </section>
    );
  }

  const rows = [
    {
      name: "Average fill %",
      current: current?.fill?.avg_fill_level ?? null,
      previous: null,
    },
    {
      name: "Collections",
      current: current?.collections?.collections ?? null,
      previous: previous.collections,
    },
    {
      name: "Overflow collections",
      current: current?.collections?.overflow_collections ?? null,
      previous: previous.overflow_collections,
    },
    {
      name: "Weight kg",
      current: current?.waste?.total_weight_kg ?? null,
      previous: previous.total_weight_kg,
    },
    {
      name: "Diversion %",
      current: current?.waste?.diversion_rate_pct ?? null,
      previous: previous.diversion_rate_pct,
    },
  ].filter((row) => row.current != null || row.previous != null);

  return (
    <section className="card">
      <h2 className="mb-1 font-semibold">Current vs previous {days} day{days === 1 ? "" : "s"}</h2>
      <p className="muted mb-4">
        Average fill is live network state, so it is not compared. Other metrics use the analytics window.
      </p>
      {rows.length === 0 ? (
        <EmptyState label="No comparable metrics for this window." />
      ) : (
        <>
          <div className="mb-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-400">
                <tr>
                  <th className="pb-2 font-medium">Metric</th>
                  <th className="pb-2 font-medium">Current</th>
                  <th className="pb-2 font-medium">Previous</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.name} className="border-t border-line">
                    <td className="py-2">{row.name}</td>
                    <td className="py-2">{fmt(row.current, row.name.includes("%") || row.name === "Weight kg" ? 1 : 0)}</td>
                    <td className="py-2">{fmt(row.previous, row.name.includes("%") || row.name === "Weight kg" ? 1 : 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={rows.filter((row) => row.previous != null)}>
                <CartesianGrid stroke="#243049" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} />
                <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} />
                <Tooltip />
                <Legend />
                <Bar dataKey="current" fill="#34d399" name="Current" />
                <Bar dataKey="previous" fill="#64748b" name="Previous" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </section>
  );
}
