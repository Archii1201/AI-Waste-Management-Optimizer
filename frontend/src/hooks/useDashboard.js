import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";

const POLL_MS = 12000;

function previousPeriod(current, doubled) {
  if (!current || !doubled) return null;
  const collections = Math.max(0, (doubled.collections || 0) - (current.collections || 0));
  const overflow = Math.max(
    0,
    (doubled.overflow_collections || 0) - (current.overflow_collections || 0)
  );
  const weight = Math.max(0, (doubled.total_weight_kg || 0) - (current.total_weight_kg || 0));
  const recyclable = Math.max(0, (doubled.recyclable_kg || 0) - (current.recyclable_kg || 0));
  return {
    collections,
    overflow_collections: overflow,
    total_weight_kg: Number(weight.toFixed(2)),
    recyclable_kg: Number(recyclable.toFixed(2)),
    diversion_rate_pct: weight > 0 ? Number(((100 * recyclable) / weight).toFixed(1)) : null,
  };
}

export function useDashboard(days, comparePrevious) {
  const [data, setData] = useState(null);
  const [previous, setPrevious] = useState(null);
  const [loading, setLoading] = useState(true);
  const [compareLoading, setCompareLoading] = useState(false);
  const [error, setError] = useState(null);
  const [staleError, setStaleError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [updating, setUpdating] = useState(false);
  const inFlight = useRef(false);
  const queued = useRef(false);
  const mounted = useRef(true);
  const hasData = useRef(false);
  const compareRef = useRef(comparePrevious);
  const daysRef = useRef(days);
  compareRef.current = comparePrevious;
  daysRef.current = days;

  const applyPayload = useCallback((payload) => {
    setData({
      overview: payload.overview,
      zones: payload.zones,
      collections: payload.overview.collections,
      waste: payload.overview.waste,
      fill: payload.overview.fill,
      recommendations: payload.recommendations,
      routeAnalytics: payload.overview.routes,
      alerts: payload.alerts,
      alertSummary: payload.overview.alerts,
      routes: payload.routes,
      bins: payload.binsPage.items || [],
      reference: payload.reference,
      priorities: payload.priorities || [],
      predictions: payload.predictions || [],
      vehicles: payload.vehiclesPage.items || [],
    });
    if (payload.doubledCollections && payload.doubledWaste) {
      setPrevious({
        collections: payload.doubledCollections,
        waste: payload.doubledWaste,
      });
    } else if (!compareRef.current) {
      setPrevious(null);
    }
    hasData.current = true;
    setLastUpdated(new Date().toISOString());
    setStaleError(null);
    setError(null);
  }, []);

  const load = useCallback(async (silent = false) => {
    if (inFlight.current) {
      queued.current = true;
      return;
    }
    inFlight.current = true;
    const showSplash = !silent && !hasData.current;
    if (showSplash) {
      setLoading(true);
      setError(null);
    } else {
      setUpdating(true);
    }
    if (compareRef.current && !silent) setCompareLoading(true);

    try {
      do {
        queued.current = false;
        const compare = compareRef.current;
        const windowDays = daysRef.current;
        const [
          overview,
          zones,
          recommendations,
          alerts,
          routes,
          binsPage,
          reference,
          priorities,
          predictions,
          vehiclesPage,
          doubledCollections,
          doubledWaste,
        ] = await Promise.all([
          api.overview(windowDays),
          api.zones(windowDays),
          api.recommendations(windowDays),
          api.alerts(),
          api.routes(),
          api.bins(),
          api.reference(),
          api.priorities(),
          api.predictions().catch(() => []),
          api.vehicles(),
          compare ? api.collections(windowDays * 2) : Promise.resolve(null),
          compare ? api.waste(windowDays * 2) : Promise.resolve(null),
        ]);
        if (!mounted.current) return;
        applyPayload({
          overview,
          zones,
          recommendations,
          alerts,
          routes,
          binsPage,
          reference,
          priorities,
          predictions,
          vehiclesPage,
          doubledCollections,
          doubledWaste,
        });
      } while (queued.current);
    } catch (err) {
      if (!mounted.current) return;
      const message = err.message || "Could not load dashboard data";
      if (hasData.current) {
        setStaleError(message);
      } else {
        setError(message);
        setData(null);
      }
      throw err;
    } finally {
      inFlight.current = false;
      if (mounted.current) {
        setLoading(false);
        setUpdating(false);
        setCompareLoading(false);
      }
    }
  }, [applyPayload]);

  const silentRefresh = useCallback(() => load(true).catch(() => {}), [load]);
  const reload = useCallback(() => load(false).catch(() => {}), [load]);

  const refreshAlerts = useCallback(async () => {
    const [alerts, summary] = await Promise.all([api.alerts(), api.alertSummary()]);
    setData((current) =>
      current
        ? {
            ...current,
            alerts,
            alertSummary: summary,
            overview: current.overview
              ? { ...current.overview, alerts: summary }
              : current.overview,
          }
        : current
    );
    setLastUpdated(new Date().toISOString());
  }, []);

  const refreshRoutes = useCallback(async () => {
    const [routes, routeAnalytics] = await Promise.all([
      api.routes(),
      api.routeAnalytics(days),
    ]);
    setData((current) =>
      current
        ? {
            ...current,
            routes,
            routeAnalytics,
            overview: current.overview
              ? { ...current.overview, routes: routeAnalytics }
              : current.overview,
          }
        : current
    );
    setLastUpdated(new Date().toISOString());
  }, [days]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    load(false).catch(() => {});
  }, [days, comparePrevious, load]);

  useEffect(() => {
    let timer = null;
    let cancelled = false;

    function schedule() {
      timer = setTimeout(async () => {
        if (cancelled) return;
        await silentRefresh();
        if (!cancelled) schedule();
      }, POLL_MS);
    }

    schedule();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [silentRefresh]);

  const previousMetrics =
    comparePrevious && data && previous
      ? previousPeriod(
          {
            collections: data.collections?.collections,
            overflow_collections: data.collections?.overflow_collections,
            total_weight_kg: data.waste?.total_weight_kg,
            recyclable_kg: data.waste?.recyclable_kg,
          },
          {
            collections: previous.collections?.collections,
            overflow_collections: previous.collections?.overflow_collections,
            total_weight_kg: previous.waste?.total_weight_kg,
            recyclable_kg: previous.waste?.recyclable_kg,
          }
        )
      : null;

  return {
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
  };
}
