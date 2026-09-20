import { fmt } from "./ui.jsx";

const CARDS = [
  ["Active bins", (d) => fmt(d.fill?.active_bins)],
  ["Average fill", (d) => fmt(d.fill?.avg_fill_level, 1, "%")],
  ["Overflow in 24h", (d) => fmt(d.fill?.bins_forecast_to_overflow_24h)],
  ["Overflow collections", (d) => fmt(d.collections?.overflow_collection_pct, 1, "%")],
  ["Diversion rate", (d) => fmt(d.waste?.diversion_rate_pct, 1, "%")],
  ["Open alerts", (d) => fmt(d.alerts?.open)],
];

export default function KpiCards({ overview }) {
  return (
    <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      {CARDS.map(([label, pick]) => (
        <article key={label} className="card">
          <p className="muted">{label}</p>
          <p className="mt-2 text-2xl font-semibold tracking-tight">{pick(overview)}</p>
        </article>
      ))}
    </section>
  );
}
