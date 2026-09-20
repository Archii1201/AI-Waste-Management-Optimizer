import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client.js";
import { EmptyState, PanelError, SeverityBadge, fmt, fmtOverflow, fmtWhen } from "./ui.jsx";

export default function BinDetails({
  binId,
  bins,
  zones,
  priorities,
  predictions,
  onClose,
  onViewMap,
}) {
  const bin = useMemo(() => bins.find((item) => item.id === binId), [bins, binId]);
  const zone = useMemo(
    () => zones.find((item) => item.zone_id === bin?.zone_id),
    [zones, bin]
  );
  const priority = useMemo(
    () => priorities.find((item) => item.bin_id === binId),
    [priorities, binId]
  );
  const [readings, setReadings] = useState([]);
  const [collections, setCollections] = useState([]);
  const [prediction, setPrediction] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!binId) return undefined;
    let cancelled = false;
    setLoading(true);
    setError(null);
    const cached = predictions.find((item) => item.bin_id === binId) || null;
    Promise.all([
      api.binReadings(binId, 400),
      api.binCollections(binId, 5),
      cached ? Promise.resolve(cached) : api.binPrediction(binId),
    ])
      .then(([history, events, forecast]) => {
        if (cancelled) return;
        setReadings(history || []);
        setCollections(events || []);
        setPrediction(forecast);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Could not load bin details");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [binId, predictions]);

  if (!binId) return null;

  const lastCollection = collections[0]?.collected_at || bin?.last_emptied_at;
  const chartRows = (readings || []).map((row) => ({
    t: new Date(row.recorded_at).toLocaleString(),
    fill: row.fill_level,
  }));

  return (
    <div className="fixed inset-0 z-[2000] flex items-end justify-center bg-black/60 p-3 md:items-center">
      <div className="card max-h-[92vh] w-full max-w-3xl overflow-auto">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <p className="muted">Bin details</p>
            <h2 className="text-xl font-semibold">{bin?.code || `Bin ${binId}`}</h2>
            <p className="muted">{bin?.label || zone?.zone_name || "—"}</p>
          </div>
          <div className="flex gap-2">
            {bin && (
              <button type="button" className="btn-secondary" onClick={() => onViewMap(bin)}>
                View on map
              </button>
            )}
            <button type="button" className="btn-secondary" onClick={onClose}>
              Close
            </button>
          </div>
        </div>

        {loading && <p className="muted py-8 text-center">Loading bin history…</p>}
        {error && <PanelError message={error} />}

        {!loading && bin && (
          <>
            <dl className="mb-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
              <Stat label="Zone" value={zone?.zone_name || `Zone ${bin.zone_id}`} />
              <Stat label="Fill" value={fmt(bin.current_fill_level, 1, "%")} />
              <Stat label="Capacity" value={`${fmt(bin.capacity_liters, 0)} L`} />
              <Stat label="Waste type" value={bin.waste_type} />
              <Stat
                label="Battery"
                value={bin.battery_level == null ? "—" : fmt(bin.battery_level, 0, "%")}
              />
              <Stat label="Last collection" value={fmtWhen(lastCollection)} />
              <Stat
                label="Priority"
                value={
                  priority ? (
                    <span className="flex items-center gap-2">
                      <SeverityBadge value={priority.tier} />
                      <span>{fmt(priority.score, 2)}</span>
                    </span>
                  ) : (
                    bin.fill_status
                  )
                }
              />
              <Stat
                label="Predicted overflow"
                value={fmtOverflow(prediction?.hours_to_full, prediction?.predicted_full_at)}
              />
            </dl>

            {prediction && (
              <p className="muted mb-4">
                Rate {fmt(prediction.predicted_fill_rate_pct_per_hour, 2, " pp/h")} · method{" "}
                {prediction.method?.replaceAll("_", " ")}
                {prediction.is_model_backed === false ? " (fallback)" : ""}
              </p>
            )}

            <h3 className="mb-2 text-sm font-medium">Fill history</h3>
            {chartRows.length === 0 ? (
              <EmptyState label="No fill-level readings stored for this bin." />
            ) : (
              <div className="h-56">
                <ResponsiveContainer>
                  <LineChart data={chartRows}>
                    <CartesianGrid stroke="#243049" vertical={false} />
                    <XAxis dataKey="t" hide />
                    <YAxis domain={[0, 100]} tick={{ fill: "#94a3b8", fontSize: 12 }} />
                    <Tooltip />
                    <Line type="monotone" dataKey="fill" stroke="#34d399" dot={false} name="Fill %" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="rounded-xl border border-line bg-ink/50 p-3">
      <dt className="muted">{label}</dt>
      <dd className="mt-1 font-medium capitalize">{value}</dd>
    </div>
  );
}
