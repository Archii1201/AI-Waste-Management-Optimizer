import { useMemo, useState } from "react";
import { EmptyState, PanelError, SeverityBadge } from "./ui.jsx";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "critical", label: "Critical" },
  { id: "warning", label: "Warning" },
  { id: "forecast", label: "Forecast" },
  { id: "sensor", label: "Sensor" },
];

const FORECAST_TYPES = new Set(["bin_overflow_imminent"]);
const SENSOR_TYPES = new Set(["sensor_fault", "sensor_offline", "battery_low"]);

function matches(alert, filter) {
  if (filter === "all") return true;
  if (filter === "critical") return alert.severity === "critical";
  if (filter === "warning") return alert.severity === "warning";
  if (filter === "forecast") return FORECAST_TYPES.has(alert.alert_type);
  if (filter === "sensor") return SENSOR_TYPES.has(alert.alert_type);
  return true;
}

export default function AlertPanel({
  alerts,
  summary,
  onAcknowledge,
  onResolve,
  onViewBin,
}) {
  const [filter, setFilter] = useState("all");
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);

  const filtered = useMemo(
    () => alerts.filter((alert) => matches(alert, filter)),
    [alerts, filter]
  );

  const counts = {
    all: alerts.length,
    critical: alerts.filter((alert) => alert.severity === "critical").length,
    warning: alerts.filter((alert) => alert.severity === "warning").length,
    forecast: alerts.filter((alert) => FORECAST_TYPES.has(alert.alert_type)).length,
    sensor: alerts.filter((alert) => SENSOR_TYPES.has(alert.alert_type)).length,
  };

  async function run(id, action) {
    setBusyId(id);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err.message || "Alert action failed");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="card h-full">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold">Alerts</h2>
        <span className="muted">{summary?.open ?? alerts.length} open</span>
      </div>
      <p className="muted mb-3 text-xs">
        Critical {summary?.critical_open ?? counts.critical} · Ack {summary?.acknowledged ?? 0} ·
        Resolved {summary?.resolved ?? 0}
      </p>
      <div className="mb-3 flex flex-wrap gap-1">
        {FILTERS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={filter === item.id ? "btn-primary !px-2 !py-1 text-xs" : "btn-secondary !px-2 !py-1 text-xs"}
            onClick={() => setFilter(item.id)}
          >
            {item.label} {counts[item.id]}
          </button>
        ))}
      </div>
      {error && <div className="mb-2"><PanelError message={error} /></div>}
      {filtered.length === 0 ? (
        <EmptyState label={alerts.length === 0 ? "No open alerts" : "No alerts in this filter"} />
      ) : (
        <ul className="max-h-[28rem] space-y-2 overflow-auto pr-1">
          {filtered.map((alert) => (
            <li key={alert.id} className="rounded-xl border border-line bg-ink/50 p-3">
              <div className="mb-1 flex items-center justify-between gap-2">
                <SeverityBadge value={alert.severity} />
                <span className="muted text-xs">
                  {new Date(alert.triggered_at).toLocaleString()}
                </span>
              </div>
              <p className="text-sm font-medium">{alert.title}</p>
              <p className="muted mt-1 line-clamp-2">{alert.message}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {alert.status === "open" && (
                  <button
                    type="button"
                    className="btn-secondary !px-2 !py-1 text-xs"
                    disabled={busyId === alert.id}
                    onClick={() => run(alert.id, () => onAcknowledge(alert.id))}
                  >
                    Acknowledge
                  </button>
                )}
                {alert.status !== "resolved" && (
                  <button
                    type="button"
                    className="btn-secondary !px-2 !py-1 text-xs"
                    disabled={busyId === alert.id}
                    onClick={() => run(alert.id, () => onResolve(alert.id))}
                  >
                    Resolve
                  </button>
                )}
                {alert.bin_id && (
                  <button
                    type="button"
                    className="btn-secondary !px-2 !py-1 text-xs"
                    onClick={() => onViewBin(alert.bin_id)}
                  >
                    View Bin
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
