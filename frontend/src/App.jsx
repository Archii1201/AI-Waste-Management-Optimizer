import { useState } from "react";
import { useDashboard } from "./hooks/useDashboard.js";
import { api } from "./api/client.js";
import KpiCards from "./components/KpiCards.jsx";
import AlertPanel from "./components/AlertPanel.jsx";
import ZoneTable from "./components/ZoneTable.jsx";
import CollectionAnalytics from "./components/CollectionAnalytics.jsx";
import FillVisualization from "./components/FillVisualization.jsx";
import Recommendations from "./components/Recommendations.jsx";
import RouteSection from "./components/RouteSection.jsx";
import BinMap from "./components/BinMap.jsx";
import BinDetails from "./components/BinDetails.jsx";
import PriorityView from "./components/PriorityView.jsx";
import WasteClassification from "./components/WasteClassification.jsx";
import PeriodComparison from "./components/PeriodComparison.jsx";

export default function App() {
  const [days, setDays] = useState(30);
  const [compare, setCompare] = useState(false);
  const [view, setView] = useState("ops");
  const [selectedBinId, setSelectedBinId] = useState(null);
  const [selectedRouteId, setSelectedRouteId] = useState(null);
  const [focusTarget, setFocusTarget] = useState(null);
  const {
    data,
    loading,
    error,
    reload,
    refreshAlerts,
    refreshRoutes,
    previousMetrics,
    compareLoading,
  } = useDashboard(days, compare);

  function openBin(id) {
    setSelectedBinId(id);
    setView("ops");
  }

  function viewOnMap(bin) {
    setSelectedBinId(null);
    setFocusTarget({
      id: bin.id,
      lat: bin.latitude,
      lng: bin.longitude,
      nonce: Date.now(),
    });
    setView("ops");
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-panel/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-4 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-emerald-400">PS-11 operations</p>
            <h1 className="text-xl font-semibold">Waste Management Optimizer</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-lg border border-line bg-ink p-0.5">
              <button
                type="button"
                className={view === "ops" ? "btn-primary !py-1" : "btn-secondary !border-0 !py-1"}
                onClick={() => setView("ops")}
              >
                Dashboard
              </button>
              <button
                type="button"
                className={view === "classify" ? "btn-primary !py-1" : "btn-secondary !border-0 !py-1"}
                onClick={() => setView("classify")}
              >
                Classify
              </button>
            </div>
            <label className="muted flex items-center gap-2">
              Window
              <select
                className="rounded-lg border border-line bg-ink px-2 py-1 text-slate-100"
                value={days}
                onChange={(event) => setDays(Number(event.target.value))}
              >
                <option value={1}>24 hours</option>
                <option value={7}>7 days</option>
                <option value={30}>30 days</option>
                <option value={90}>90 days</option>
              </select>
            </label>
            <label className="muted flex items-center gap-2">
              <input
                type="checkbox"
                checked={compare}
                onChange={(event) => setCompare(event.target.checked)}
              />
              Compare previous
            </label>
            <button type="button" onClick={reload} className="btn-primary">
              Refresh
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-4 px-4 py-6">
        {loading && (
          <div className="card py-16 text-center text-slate-400">
            Loading live operations data…
          </div>
        )}

        {error && (
          <div className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-4">
            <p className="font-medium text-rose-200">API error</p>
            <p className="muted mt-1">{error}</p>
            <p className="muted mt-2">
              Start the API with `uvicorn app.main:app --reload` from backend/, then refresh.
            </p>
          </div>
        )}

        {!loading && !error && data && view === "classify" && (
          <WasteClassification bins={data.bins} />
        )}

        {!loading && !error && data && view === "ops" && (
          <>
            <KpiCards overview={data.overview} />
            {compare && (
              <PeriodComparison
                current={data}
                previous={previousMetrics}
                loading={compareLoading}
                days={days}
              />
            )}
            <div className="grid gap-4 xl:grid-cols-3">
              <div className="xl:col-span-2">
                <BinMap
                  bins={data.bins}
                  reference={data.reference}
                  routes={data.routes}
                  selectedRouteId={selectedRouteId}
                  selectedBinId={selectedBinId}
                  focusTarget={focusTarget}
                  onSelectBin={(bin) => openBin(bin.id)}
                />
              </div>
              <AlertPanel
                alerts={data.alerts}
                summary={data.alertSummary}
                onAcknowledge={async (id) => {
                  await api.acknowledgeAlert(id);
                  await refreshAlerts();
                }}
                onResolve={async (id) => {
                  await api.resolveAlert(id);
                  await refreshAlerts();
                }}
                onViewBin={openBin}
              />
            </div>
            <PriorityView
              priorities={data.priorities}
              predictions={data.predictions}
              onSelectBin={openBin}
            />
            <div className="grid gap-4 lg:grid-cols-2">
              <FillVisualization bins={data.bins} fill={data.fill} />
              <CollectionAnalytics collections={data.collections} waste={data.waste} />
            </div>
            <ZoneTable zones={data.zones} />
            <RouteSection
              analytics={data.routeAnalytics}
              routes={data.routes}
              vehicles={data.vehicles}
              selectedRouteId={selectedRouteId}
              onSelectRoute={setSelectedRouteId}
              onOptimized={async () => {
                setSelectedRouteId(null);
                await refreshRoutes();
              }}
            />
            <Recommendations items={data.recommendations} />
          </>
        )}
      </main>

      {selectedBinId && data && (
        <BinDetails
          binId={selectedBinId}
          bins={data.bins}
          zones={data.zones}
          priorities={data.priorities}
          predictions={data.predictions}
          onClose={() => setSelectedBinId(null)}
          onViewMap={viewOnMap}
        />
      )}
    </div>
  );
}
