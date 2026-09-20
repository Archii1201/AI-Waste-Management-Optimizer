import { EmptyState, PRIORITY_GROUP, SeverityBadge, fmt, fmtOverflow } from "./ui.jsx";

const ORDER = ["HIGH", "MEDIUM", "LOW"];

export default function PriorityView({ priorities, predictions, onSelectBin }) {
  const byGroup = { HIGH: [], MEDIUM: [], LOW: [] };
  for (const item of priorities) {
    const group = PRIORITY_GROUP[item.tier] || "LOW";
    byGroup[group].push(item);
  }
  ORDER.forEach((group) =>
    byGroup[group].sort((a, b) => b.score - a.score)
  );

  const predictionByBin = new Map(predictions.map((row) => [row.bin_id, row]));

  return (
    <section className="card">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Collection priority</h2>
        <p className="muted">
          HIGH {byGroup.HIGH.length} · MEDIUM {byGroup.MEDIUM.length} · LOW {byGroup.LOW.length}
        </p>
      </div>
      {priorities.length === 0 ? (
        <EmptyState label="No ranked bins yet. Train the fill model and refresh priorities." />
      ) : (
        <div className="grid gap-3 lg:grid-cols-3">
          {ORDER.map((group) => (
            <div key={group} className="rounded-xl border border-line bg-ink/40 p-3">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold">{group}</h3>
                <span className="muted">{byGroup[group].length}</span>
              </div>
              {byGroup[group].length === 0 ? (
                <EmptyState label={`No ${group.toLowerCase()} priority bins.`} />
              ) : (
                <ul className="max-h-72 space-y-2 overflow-auto">
                  {byGroup[group].map((item) => {
                    const forecast = predictionByBin.get(item.bin_id);
                    return (
                      <li key={item.bin_id}>
                        <button
                          type="button"
                          className="w-full rounded-lg border border-line px-3 py-2 text-left hover:border-emerald-400/50"
                          onClick={() => onSelectBin(item.bin_id)}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-medium">{item.code}</span>
                            <SeverityBadge value={item.tier} />
                          </div>
                          <p className="muted mt-1">
                            {fmt(item.fill_level, 0, "%")} · {item.waste_type} · score{" "}
                            {fmt(item.score, 2)}
                          </p>
                          <p className="muted">
                            Overflow {fmtOverflow(
                              item.hours_to_full ?? forecast?.hours_to_full,
                              forecast?.predicted_full_at
                            )}
                          </p>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
