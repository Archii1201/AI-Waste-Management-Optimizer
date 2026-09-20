import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { EmptyState, fmt } from "./ui.jsx";

const STREAM_COLORS = ["#34d399", "#60a5fa", "#fbbf24", "#fb7185", "#a78bfa", "#94a3b8"];

export default function CollectionAnalytics({ collections, waste }) {
  const streamRows = Object.entries(waste?.by_stream || {}).map(([name, row]) => ({
    name,
    weight: row.weight_kg,
    recyclable: row.recyclable_kg,
  }));

  const split = [
    { name: "Recyclable", value: waste?.recyclable_kg || 0 },
    { name: "Non-recyclable", value: waste?.non_recyclable_kg || 0 },
  ];
  const hasSplit = split.some((row) => row.value > 0);

  return (
    <section className="card">
      <h2 className="mb-1 font-semibold">Collection & recycling</h2>
      <p className="muted mb-4">
        {fmt(collections?.collections)} collections · {fmt(waste?.total_weight_kg, 0)} kg collected ·{" "}
        {fmt(waste?.diversion_rate_pct, 1, "%")} diverted
      </p>
      {!hasSplit && streamRows.length === 0 ? (
        <EmptyState label="No collection events in this window." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="h-64">
            <ResponsiveContainer>
              <PieChart>
                <Pie data={split} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80}>
                  <Cell fill="#34d399" />
                  <Cell fill="#64748b" />
                </Pie>
                <Tooltip />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={streamRows}>
                <CartesianGrid stroke="#243049" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 12 }} />
                <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} />
                <Tooltip />
                <Bar dataKey="weight" name="kg">
                  {streamRows.map((_, index) => (
                    <Cell key={index} fill={STREAM_COLORS[index % STREAM_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </section>
  );
}
