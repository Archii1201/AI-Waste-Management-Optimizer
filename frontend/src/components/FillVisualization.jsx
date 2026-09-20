import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { EmptyState, FILL_COLORS, fmt } from "./ui.jsx";

export default function FillVisualization({ bins, fill }) {
  const bands = [
    { name: "low", count: bins.filter((b) => b.fill_status === "low").length },
    { name: "medium", count: bins.filter((b) => b.fill_status === "medium").length },
    { name: "high", count: bins.filter((b) => b.fill_status === "high").length },
    { name: "critical", count: bins.filter((b) => b.fill_status === "critical").length },
  ];
  const hasData = bins.length > 0;

  return (
    <section className="card">
      <h2 className="mb-1 font-semibold">Fill-level distribution</h2>
      <p className="muted mb-4">
        Network average {fmt(fill?.avg_fill_level, 1, "%")} · {fmt(fill?.bins_critical)} critical ·{" "}
        {fmt(fill?.capacity_in_use_pct, 1, "%")} of capacity in use
      </p>
      {!hasData ? (
        <EmptyState label="No bin fill readings available." />
      ) : (
        <div className="h-64">
          <ResponsiveContainer>
            <BarChart data={bands}>
              <CartesianGrid stroke="#243049" vertical={false} />
              <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fill: "#94a3b8", fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="count" name="bins">
                {bands.map((band) => (
                  <Cell key={band.name} fill={FILL_COLORS[band.name]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}
