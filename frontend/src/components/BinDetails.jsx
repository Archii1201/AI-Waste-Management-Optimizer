import { useEffect, useMemo, useRef, useState } from "react";
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
import { getCachedHistory, setCachedHistory } from "../lib/binHistoryCache.js";
import { EmptyState, SeverityBadge, fmt, fmtOverflow, fmtWhen } from "./ui.jsx";

export default function BinDetails({
  binId,
  bins,
  zones,
  priorities,
  predictions,
  refreshToken,
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
  const listedPrediction = useMemo(
    () => predictions.find((item) => item.bin_id === binId) || null,
    [predictions, binId]
  );

  const cached = binId ? getCachedHistory(binId) : null;
  const [readings, setReadings] = useState(cached?.readings || []);
  const [collections, setCollections] = useState(cached?.collections || []);
  const [historyLoading, setHistoryLoading] = useState(!cached);
  const [historyError, setHistoryError] = useState(null);
  const inFlight = useRef(false);
  const appliedToken = useRef(cached?.token ?? null);

  useEffect(() => {
    if (!binId) return undefined;
    const existing = getCachedHistory(binId);
    if (existing) {
      setReadings(existing.readings || []);
      setCollections(existing.collections || []);
      setHistoryLoading(false);
      setHistoryError(null);
      appliedToken.current = existing.token ?? null;
    } else {
      setReadings([]);
      setCollections([]);
      setHistoryLoading(true);
      setHistoryError(null);
      appliedToken.current = null;
    }

    let cancelled = false;

    async function loadHistory(silent) {
      if (inFlight.current) return;
      inFlight.current = true;
      if (!silent) setHistoryLoading(true);
      try {
        const [history, events] = await Promise.all([
          api.binReadings(binId, 200),
          api.binCollections(binId, 5),
        ]);
        if (cancelled) return;
        const next = { readings: history || [], collections: events || [], token: refreshToken };
        setCachedHistory(binId, next);
        setReadings(next.readings);
        setCollections(next.collections);
        setHistoryError(null);
        appliedToken.current = refreshToken;
      } catch {
        if (!cancelled) {
          setHistoryError("History temporarily unavailable");
        }
      } finally {
        inFlight.current = false;
        if (!cancelled) setHistoryLoading(false);
      }
    }

    const hasFreshCache = existing && existing.token === refreshToken;
    if (!hasFreshCache) loadHistory(Boolean(existing));

    return () => {
      cancelled = true;
    };
  }, [binId]);

  useEffect(() => {
    if (!binId || !refreshToken) return undefined;
    if (appliedToken.current === refreshToken) return undefined;
    if (inFlight.current) return undefined;
    let cancelled = false;

    async function silent() {
      inFlight.current = true;
      try {
        const [history, events] = await Promise.all([
          api.binReadings(binId, 200),
          api.binCollections(binId, 5),
        ]);
        if (cancelled) return;
        const next = { readings: history || [], collections: events || [], token: refreshToken };
        setCachedHistory(binId, next);
        setReadings(next.readings);
        setCollections(next.collections);
        setHistoryError(null);
        appliedToken.current = refreshToken;
      } catch {
        /* keep last history */
      } finally {
        inFlight.current = false;
      }
    }

    silent();
    return () => {
      cancelled = true;
    };
  }, [binId, refreshToken]);

  if (!binId) return null;

  const forecast = listedPrediction;
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

        {bin ? (
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
              value={fmtOverflow(forecast?.hours_to_full, forecast?.predicted_full_at)}
            />
          </dl>
        ) : (
          <p className="muted mb-4">Bin {binId}</p>
        )}

        {forecast && (
          <p className="muted mb-4">
            Rate {fmt(forecast.predicted_fill_rate_pct_per_hour, 2, " pp/h")} · method{" "}
            {forecast.method?.replaceAll("_", " ")}
            {forecast.is_model_backed === false ? " (fallback)" : ""}
          </p>
        )}

        <h3 className="mb-2 text-sm font-medium">Fill history</h3>
        {historyLoading && chartRows.length === 0 && (
          <div className="h-32 animate-pulse rounded-xl border border-line bg-ink/50" />
        )}
        {historyError && chartRows.length === 0 && (
          <p className="muted py-4 text-center">{historyError}</p>
        )}
        {!historyLoading && !historyError && chartRows.length === 0 && (
          <EmptyState label="No fill-level readings stored for this bin." />
        )}
        {chartRows.length > 0 && (
          <div className="h-56">
            {historyLoading && (
              <p className="muted mb-2 text-xs">Updating history…</p>
            )}
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
