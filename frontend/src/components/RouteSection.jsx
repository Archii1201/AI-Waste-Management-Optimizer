import { useState } from "react";
import { api } from "../api/client.js";
import { EmptyState, PanelError, fmt } from "./ui.jsx";

export default function RouteSection({
  analytics,
  routes,
  vehicles,
  selectedRouteId,
  onSelectRoute,
  onOptimized,
}) {
  const [optimizing, setOptimizing] = useState(false);
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState(null);

  const vehicleById = new Map(vehicles.map((vehicle) => [vehicle.id, vehicle]));
  const deferred = Object.values(
    Object.fromEntries(
      (plan?.deferred_bins || routes.flatMap((route) => route.deferred_bins || [])).map(
        (bin) => [bin.bin_id, bin]
      )
    )
  );
  const displayRoutes = plan?.routes?.length ? plan.routes : routes;
  const vehiclesUsed = new Set(displayRoutes.map((route) => route.vehicle_id).filter(Boolean)).size;

  async function optimize() {
    setOptimizing(true);
    setError(null);
    try {
      const result = await api.optimizeRoutes({ replace_existing: true });
      setPlan(result);
      await onOptimized();
    } catch (err) {
      setError(err.message || "Route optimization failed");
    } finally {
      setOptimizing(false);
    }
  }

  return (
    <section className="card">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="font-semibold">Routes & optimization</h2>
          <p className="muted">
            {fmt(plan?.routes?.length ?? analytics?.routes)} routes ·{" "}
            {fmt(plan?.total_distance_km ?? analytics?.total_distance_km, 1)} km · saved{" "}
            {fmt(analytics?.distance_saved_pct, 1, "%")} vs unoptimised order
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={optimize} disabled={optimizing}>
          {optimizing ? "Optimizing…" : "Optimize Collection Routes"}
        </button>
      </div>

      {error && <div className="mb-3"><PanelError message={error} /></div>}
      {plan && !error && (
        <div className="mb-4 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-100">
          Planned {plan.routes.length} routes · {plan.total_stops} stops ·{" "}
          {fmt(plan.total_distance_km, 1)} km · {vehiclesUsed} vehicles ·{" "}
          {plan.deferred_bins?.length || 0} deferred
          <span className="muted"> · {plan.solver_status} in {fmt(plan.solve_seconds, 1)}s</span>
        </div>
      )}

      {displayRoutes.length === 0 ? (
        <EmptyState label="No routes planned. Run Optimize Collection Routes." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-400">
                <tr>
                  <th className="pb-2 font-medium">Vehicle</th>
                  <th className="pb-2 font-medium">Stops</th>
                  <th className="pb-2 font-medium">Distance</th>
                  <th className="pb-2 font-medium">Priority</th>
                  <th className="pb-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {displayRoutes.map((route) => {
                  const vehicle = vehicleById.get(route.vehicle_id);
                  const selected = route.id === selectedRouteId;
                  const avgPriority =
                    route.stops?.length
                      ? route.stops.reduce((sum, stop) => sum + (stop.priority_score || 0), 0) /
                        route.stops.length
                      : null;
                  return (
                    <tr
                      key={route.id}
                      className={`cursor-pointer border-t border-line ${selected ? "bg-emerald-500/10" : ""}`}
                      onClick={() => onSelectRoute(selected ? null : route.id)}
                    >
                      <td className="py-2">
                        {vehicle?.code || route.code}
                        <div className="muted text-xs">{route.code}</div>
                      </td>
                      <td className="py-2">{route.total_stops}</td>
                      <td className="py-2">{fmt(route.total_distance_km, 1)} km</td>
                      <td className="py-2">{fmt(avgPriority, 2)}</td>
                      <td className="py-2 capitalize">{route.status.replaceAll("_", " ")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div>
            <h3 className="mb-2 text-sm font-medium text-slate-300">Deferred bins</h3>
            {deferred.length === 0 ? (
              <EmptyState label="No deferred bins on current plans." />
            ) : (
              <ul className="max-h-56 space-y-2 overflow-auto">
                {deferred.map((bin, index) => (
                  <li key={`${bin.bin_id}-${index}`} className="rounded-lg border border-line px-3 py-2 text-sm">
                    <p className="font-medium">{bin.bin_code || `Bin ${bin.bin_id}`}</p>
                    <p className="muted">{bin.reason}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
