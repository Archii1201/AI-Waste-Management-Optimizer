import { useState } from "react";
import { useDashboard } from "./hooks/useDashboard.js";
import { api } from "./api/client.js";
import { fmtWhen } from "./components/ui.jsx";
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
import LiveSimulation from "./components/LiveSimulation.jsx";
import ImpactSummary from "./components/ImpactSummary.jsx";
import WhatIf from "./components/WhatIf.jsx";
import GenerateReport from "./components/GenerateReport.jsx";

export default function App() {
  const [days, setDays] = useState(30);
  const [compare, setCompare] = useState(false);
  const [view, setView] = useState("ops");
  const [selectedBinId, setSelectedBinId] = useState(null);
  const [selectedRouteId, setSelectedRouteId] = useState(null);
  const [focusTarget, setFocusTarget] = useState(null);
  const [whatIfResult, setWhatIfResult] = useState(null);
  const [mapMode, setMapMode] = useState("current");
  const {
    data,
    loading,
    error,
    staleError,
    lastUpdated,
    updating,
    reload,
    silentRefresh,
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
          <div className="flex items-start gap-3">
            <svg viewBox="0 0 32 32" className="mt-0.5 h-9 w-9 shrink-0" aria-hidden="true">
              <circle cx="16" cy="16" r="15" fill="#0b1220" stroke="#34d399" strokeWidth="2" />
              <path
                fill="#34d399"
                d="M16 7c3.8 3.2 6.2 6.6 6.2 9.6A6.2 6.2 0 0 1 16 22.8 6.2 6.2 0 0 1 9.8 16.6C9.8 13.6 12.2 10.2 16 7zm0 6.4a3.2 3.2 0 1 0 0 6.4 3.2 3.2 0 0 0 0-6.4z"
              />
              <path fill="#0b1220" d="M16 14.6a2 2 0 1 1 0 4 2 2 0 0 1 0-4z" />
            </svg>
            <div>
              <p className="text-xs uppercase tracking-[0.2em] text-emerald-400">Intelligent Waste Operations</p>
              <h1 className="text-xl font-semibold">EcoFlow AI</h1>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
                    updating
                      ? "bg-amber-500/20 text-amber-200"
                      : "bg-emerald-500/20 text-emerald-200"
                  }`}
                >
                  {updating ? "UPDATING" : "LIVE"}
                </span>
                <span className="muted text-xs">
                  Last updated: {lastUpdated ? fmtWhen(lastUpdated) : "N/A"}
                </span>
              </div>
            </div>
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
              <button
                type="button"
                className={view === "whatif" ? "btn-primary !py-1" : "btn-secondary !border-0 !py-1"}
                onClick={() => setView("whatif")}
              >
                What-If
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
            <button type="button" onClick={reload} className="btn-primary" disabled={updating && !data}>
              Refresh
            </button>
            <GenerateReport data={data} days={days} />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-4 px-4 py-6">
        {loading && !data && (
          <div className="card py-16 text-center text-slate-400">
            Loading live operations data…
          </div>
        )}

        {error && !data && (
          <div className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-4">
            <p className="font-medium text-rose-200">API error</p>
            <p className="muted mt-1">{error}</p>
            <p className="muted mt-2">
              Start the API with `uvicorn app.main:app --reload` from backend/, then refresh.
            </p>
          </div>
        )}

        {staleError && data && (
          <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-100">
            Showing last successful data. Refresh failed: {staleError}
          </div>
        )}

        {data && (
          <LiveSimulation silentRefresh={silentRefresh} />
        )}

        {data && view === "classify" && (
          <WasteClassification bins={data.bins} />
        )}

        {data && view === "whatif" && (
          <WhatIf
            vehicles={data.vehicles}
            result={whatIfResult}
            onResult={(next) => {
              setWhatIfResult(next);
              setMapMode(next ? "whatif" : "current");
              setSelectedRouteId(null);
            }}
          />
        )}

        {data && view === "ops" && (
          <>
            <KpiCards overview={data.overview} />
            <ImpactSummary data={data} />
            {compare && (
              <PeriodComparison
                current={data}
                previous={previousMetrics}
                loading={compareLoading && !previousMetrics}
                days={days}
              />
            )}
            <div className="grid gap-4 xl:grid-cols-3">
              <div className="xl:col-span-2">
                {whatIfResult && (
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      className={mapMode === "current" ? "btn-primary !py-1" : "btn-secondary !py-1"}
                      onClick={() => {
                        setMapMode("current");
                        setSelectedRouteId(null);
                      }}
                    >
                      Current
                    </button>
                    <button
                      type="button"
                      className={mapMode === "whatif" ? "btn-primary !py-1" : "btn-secondary !py-1"}
                      onClick={() => {
                        setMapMode("whatif");
                        setSelectedRouteId(null);
                      }}
                    >
                      What-If
                    </button>
                  </div>
                )}
                <BinMap
                  bins={data.bins}
                  reference={data.reference}
                  routes={mapMode === "whatif" && whatIfResult ? whatIfResult.routes : data.routes}
                  selectedRouteId={selectedRouteId}
                  selectedBinId={selectedBinId}
                  focusTarget={focusTarget}
                  onSelectBin={(bin) => openBin(bin.id)}
                  overlayLabel={mapMode === "whatif" && whatIfResult ? whatIfResult.label : null}
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
          refreshToken={lastUpdated}
          onClose={() => setSelectedBinId(null)}
          onViewMap={viewOnMap}
        />
      )}
    </div>
  );
}
