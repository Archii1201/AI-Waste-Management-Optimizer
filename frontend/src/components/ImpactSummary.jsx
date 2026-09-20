import { na } from "./ui.jsx";

const METRICS = [
  ["Total waste collected", (d) => na(d.waste?.total_weight_kg, 1, " kg"), "Weight recorded on collection events in the selected window."],
  ["Recyclable waste", (d) => na(d.waste?.recyclable_kg, 1, " kg"), "Recyclable share of collected weight from collection records."],
  ["Non-recyclable waste", (d) => na(d.waste?.non_recyclable_kg, 1, " kg"), "Collected weight that was not classified as recyclable."],
  ["Diversion rate", (d) => na(d.waste?.diversion_rate_pct, 1, "%"), "Recyclable kg as a percentage of total collected weight."],
  ["Collections", (d) => na(d.collections?.collections), "Emptying events in the selected analytics window."],
  ["Overflow collections", (d) => na(d.collections?.overflow_collections), "Collections where the bin was already overflowing."],
  ["Optimized routes", (d) => na(d.routeAnalytics?.routes), "Routes stored in the window, including optimiser output."],
  ["Total route distance", (d) => na(d.routeAnalytics?.total_distance_km, 1, " km"), "Sum of planned route distances from analytics."],
  ["Distance saved", (d) => na(d.routeAnalytics?.distance_saved_km, 1, " km"), "Unoptimised visit-order distance minus optimiser distance."],
  ["Estimated route cost", (d) => na(d.routeAnalytics?.estimated_cost, 0), "Cost stored on planned routes, when the optimiser recorded it."],
  ["Capacity in use", (d) => na(d.fill?.capacity_in_use_pct, 1, "%"), "Current fill volume as a share of total active-bin capacity."],
  ["Open alerts", (d) => na(d.alertSummary?.open), "Alerts currently open or acknowledged."],
];

export default function ImpactSummary({ data }) {
  return (
    <section className="card">
      <h2 className="mb-1 font-semibold">Impact summary</h2>
      <p className="muted mb-4">Operational metrics from the live analytics APIs. Unsupported values show N/A.</p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {METRICS.map(([label, pick, blurb]) => (
          <article key={label} className="rounded-xl border border-line bg-ink/50 p-3">
            <p className="muted">{label}</p>
            <p className="mt-1 text-xl font-semibold">{pick(data)}</p>
            <p className="muted mt-2 text-xs">{blurb}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
