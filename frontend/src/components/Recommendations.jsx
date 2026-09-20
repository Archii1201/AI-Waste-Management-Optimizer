import { EmptyState, SeverityBadge } from "./ui.jsx";

const EVIDENCE_LABELS = {
  avg_fill_level: "average fill",
  avg_fill_at_collection: "average fill at collection",
  overflow_events: "overflow events",
  overflow_collections: "overflow collections",
  overflow_collection_pct: "overflow collection percentage",
  early_collection_pct: "early collection percentage",
  collections: "collections",
  kg_per_bin_per_day: "kg/bin/day",
  diversion_rate_pct: "diversion rate",
  bins_forecast_to_overflow_24h: "forecast overflow in 24h",
  bins_critical: "critical bins",
  distance_saved_pct: "distance saved",
};

function evidenceLabel(key) {
  return EVIDENCE_LABELS[key] || key.replaceAll("_", " ");
}

export default function Recommendations({ items }) {
  return (
    <section className="card">
      <h2 className="mb-3 font-semibold">Recommendations</h2>
      {items.length === 0 ? (
        <EmptyState label="No recommendations. Operations look healthy." />
      ) : (
        <ul className="space-y-3">
          {items.map((item, index) => (
            <li key={`${item.category}-${index}`} className="rounded-xl border border-line bg-ink/50 p-3">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <SeverityBadge value={item.priority} />
                <span className="text-xs uppercase tracking-wide text-slate-500">{item.category}</span>
              </div>
              <p className="font-medium">{item.title}</p>
              <p className="muted mt-1">{item.detail}</p>
              <p className="mt-2 text-sm text-emerald-300">Action: {item.action}</p>
              <details className="mt-3">
                <summary className="cursor-pointer text-sm text-slate-300">
                  Why this recommendation?
                </summary>
                <div className="mt-2 space-y-2 text-sm">
                  <p className="muted">{item.detail}</p>
                  {item.evidence && Object.keys(item.evidence).length > 0 && (
                    <div>
                      <p className="mb-1 font-medium">Evidence</p>
                      <ul className="list-disc space-y-1 pl-5 text-slate-300">
                        {Object.entries(item.evidence).map(([key, value]) => (
                          <li key={key}>
                            {evidenceLabel(key)}: {String(value)}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <p>
                    <span className="font-medium">Recommended action: </span>
                    {item.action}
                  </p>
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
