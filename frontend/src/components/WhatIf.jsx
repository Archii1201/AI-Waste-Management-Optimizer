import { useMemo, useState } from "react";
import { api } from "../api/client.js";
import { EmptyState, PanelError, na } from "./ui.jsx";

const METRICS = [
  ["vehicles", "Vehicles", "Active vehicles used for the comparison."],
  ["routes", "Routes", "Stored current routes versus unsaved what-if routes."],
  ["stops", "Stops", "Collection stops on those routes."],
  ["distance_km", "Distance (km)", "Planned route kilometres."],
  ["overflow_risk_bins", "Overflow-risk bins", "Baseline: forecast to overflow within 24h. Scenario: still unassigned after the what-if plan."],
  ["priority_bins", "Priority bins", "Bins at or above the priority threshold."],
  ["collection_workload", "Collection workload", "Stop count used as workload."],
];

function delta(current, next) {
  if (current == null || next == null) return null;
  return Number(next) - Number(current);
}

function trend(change) {
  if (change == null) return "N/A";
  if (change > 0) return "increased";
  if (change < 0) return "decreased";
  return "unchanged";
}

export default function WhatIf({ vehicles, onResult, result }) {
  const maxFleet = vehicles.length || 1;
  const [extraRounds, setExtraRounds] = useState(0);
  const [fleetCount, setFleetCount] = useState(maxFleet);
  const [useCustomFleet, setUseCustomFleet] = useState(false);
  const [minScore, setMinScore] = useState("0.35");
  const [useCustomScore, setUseCustomScore] = useState(false);
  const [horizon, setHorizon] = useState(24);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const rows = useMemo(() => {
    if (!result) return [];
    return METRICS.map(([key, label, blurb]) => {
      const current = result.baseline?.[key];
      const scenario = result.scenario?.[key];
      const change = delta(current, scenario);
      return { key, label, blurb, current, scenario, change, trend: trend(change) };
    });
  }, [result]);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const payload = {
        extra_rounds: Number(extraRounds),
        fleet_count: useCustomFleet ? Number(fleetCount) : null,
        min_priority_score: useCustomScore ? Number(minScore) : null,
        horizon_hours: Number(horizon),
      };
      const next = await api.whatIf(payload);
      onResult(next);
    } catch (err) {
      setError(err.message || "What-if analysis failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card space-y-4">
      <div>
        <h2 className="font-semibold">Operational What-If</h2>
        <p className="muted">Evaluate how operational changes could affect waste collection.</p>
        <p className="muted mt-1 text-xs">Read-only. Results are not dispatched and do not change stored routes.</p>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <label className="muted block">
          Collection frequency
          <select
            className="mt-1 w-full rounded-lg border border-line bg-ink px-2 py-1 text-slate-100"
            value={extraRounds}
            onChange={(event) => setExtraRounds(Number(event.target.value))}
          >
            <option value={0}>Current</option>
            <option value={1}>+1 collection round/day</option>
            <option value={2}>+2 collection rounds/day</option>
          </select>
        </label>
        <label className="muted block">
          Available vehicles
          <select
            className="mt-1 w-full rounded-lg border border-line bg-ink px-2 py-1 text-slate-100"
            value={useCustomFleet ? "custom" : "current"}
            onChange={(event) => setUseCustomFleet(event.target.value === "custom")}
          >
            <option value="current">Current fleet ({maxFleet})</option>
            <option value="custom">Custom count</option>
          </select>
          {useCustomFleet && (
            <input
              type="number"
              min={1}
              max={maxFleet}
              className="mt-2 w-full rounded-lg border border-line bg-ink px-2 py-1 text-slate-100"
              value={fleetCount}
              onChange={(event) => setFleetCount(Number(event.target.value))}
            />
          )}
        </label>
        <label className="muted block">
          Priority threshold
          <select
            className="mt-1 w-full rounded-lg border border-line bg-ink px-2 py-1 text-slate-100"
            value={useCustomScore ? "custom" : "current"}
            onChange={(event) => setUseCustomScore(event.target.value === "custom")}
          >
            <option value="current">Current (0.35)</option>
            <option value="custom">Custom threshold</option>
          </select>
          {useCustomScore && (
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              className="mt-2 w-full rounded-lg border border-line bg-ink px-2 py-1 text-slate-100"
              value={minScore}
              onChange={(event) => setMinScore(event.target.value)}
            />
          )}
        </label>
        <label className="muted block">
          Planning horizon
          <select
            className="mt-1 w-full rounded-lg border border-line bg-ink px-2 py-1 text-slate-100"
            value={horizon}
            onChange={(event) => setHorizon(Number(event.target.value))}
          >
            <option value={24}>24h</option>
            <option value={48}>48h</option>
            <option value={72}>72h</option>
          </select>
        </label>
      </div>

      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-primary" onClick={run} disabled={loading}>
          {loading ? "Running analysis…" : "Run What-If Analysis"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            onResult(null);
            setError(null);
          }}
        >
          Reset Scenario
        </button>
      </div>

      {error && <PanelError message={error} />}

      {!result && !loading && (
        <EmptyState label="No scenario yet. Choose inputs and run a what-if analysis." />
      )}

      {result && (
        <>
          <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
            {result.label}
          </p>
          <p className="muted text-xs">
            On the Dashboard map, switch to What-If to view these unsaved routes. Stored routes are unchanged.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-400">
                <tr>
                  <th className="pb-2 font-medium">Metric</th>
                  <th className="pb-2 font-medium">Current baseline</th>
                  <th className="pb-2 font-medium">What-if scenario</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.key} className="border-t border-line">
                    <td className="py-2">{row.label}</td>
                    <td className="py-2">{na(row.current, row.key === "distance_km" ? 1 : 0)}</td>
                    <td className="py-2">{na(row.scenario, row.key === "distance_km" ? 1 : 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div>
            <h3 className="mb-2 font-semibold">Scenario Impact</h3>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {rows.map((row) => (
                <article key={row.key} className="rounded-xl border border-line bg-ink/50 p-3">
                  <p className="text-sm font-medium">{row.label}</p>
                  <p className="muted text-xs">{row.blurb}</p>
                  <p className="mt-2 text-sm">
                    Current: {na(row.current, row.key === "distance_km" ? 1 : 0)}
                  </p>
                  <p className="text-sm">
                    Scenario: {na(row.scenario, row.key === "distance_km" ? 1 : 0)}
                  </p>
                  <p className="mt-1 text-sm text-slate-200">
                    Change:{" "}
                    {row.change == null
                      ? "N/A"
                      : `${row.change > 0 ? "+" : ""}${row.change.toFixed(row.key === "distance_km" ? 1 : 0)}${row.key === "distance_km" ? " km" : ""}`}
                    {row.change != null && (
                      <span className="ml-2 text-xs uppercase tracking-wide text-slate-400">
                        {row.trend}
                      </span>
                    )}
                  </p>
                </article>
              ))}
            </div>
          </div>
        </>
      )}
    </section>
  );
}
