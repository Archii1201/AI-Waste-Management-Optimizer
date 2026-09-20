import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { PanelError, fmtWhen, na } from "./ui.jsx";

export default function LiveSimulation({ silentRefresh }) {
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [stats, setStats] = useState(null);
  const runningRef = useRef(false);
  const timerRef = useRef(null);

  function clearTimer() {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }

  useEffect(() => {
    let cancelled = false;
    api.simulationStatus()
      .then((status) => {
        if (cancelled) return;
        setStats(status);
        if (status.running) setRunning(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    runningRef.current = running;
    clearTimer();
    if (!running) return undefined;

    let cancelled = false;

    async function loop() {
      if (!runningRef.current || cancelled) return;
      try {
        const next = await api.simulationTick();
        if (cancelled) return;
        setStats(next);
        setError(null);
        await silentRefresh();
      } catch (err) {
        if (!cancelled) setError(err.message || "Live simulation tick failed");
      }
      if (runningRef.current && !cancelled) {
        timerRef.current = setTimeout(loop, 8000);
      }
    }

    loop();
    return () => {
      cancelled = true;
      clearTimer();
    };
  }, [running, silentRefresh]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const status = await api.simulationStart();
      setStats(status);
      setRunning(true);
    } catch (err) {
      setError(err.message || "Could not start simulation");
      setRunning(false);
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    setError(null);
    runningRef.current = false;
    setRunning(false);
    clearTimer();
    try {
      const status = await api.simulationStop();
      setStats(status);
    } catch (err) {
      setError(err.message || "Could not stop simulation");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-semibold">Live simulation</h2>
          {running && (
            <span className="rounded-full bg-rose-500/20 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-rose-200">
              Generating telemetry
            </span>
          )}
          <span className="muted">
            Last tick {stats?.last_update ? fmtWhen(stats.last_update) : "N/A"}
          </span>
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn-primary" onClick={start} disabled={busy || running}>
            {busy && !running ? "Starting…" : "▶ Start Live Simulation"}
          </button>
          <button type="button" className="btn-secondary" onClick={stop} disabled={busy || !running}>
            {busy && running ? "Stopping…" : "■ Stop Simulation"}
          </button>
        </div>
      </div>
      {error && <div className="mt-3"><PanelError message={error} /></div>}
      <dl className="mt-3 grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
        <Stat label="Bins updated" value={na(stats?.bins_updated)} />
        <Stat label="Predictions refreshed" value={na(stats?.predictions_refreshed)} />
        <Stat label="New alerts" value={na(stats?.new_alerts)} />
        <Stat label="Affected routes" value={na(stats?.affected_routes)} />
      </dl>
    </section>
  );
}

function Stat({ label, value }) {
  return (
    <div className="rounded-xl border border-line bg-ink/50 px-3 py-2">
      <dt className="muted">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
