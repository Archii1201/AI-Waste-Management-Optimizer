import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";

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

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
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
      ] = await Promise.all([
        api.overview(days),
        api.zones(days),
        api.recommendations(days),
        api.alerts(),
        api.routes(),
        api.bins(),
        api.reference(),
        api.priorities(),
        api.predictions().catch(() => []),
        api.vehicles(),
      ]);

      setData({
        overview,
        zones,
        collections: overview.collections,
        waste: overview.waste,
        fill: overview.fill,
        recommendations,
        routeAnalytics: overview.routes,
        alerts,
        alertSummary: overview.alerts,
        routes,
        bins: binsPage.items || [],
        reference,
        priorities: priorities || [],
        predictions: predictions || [],
        vehicles: vehiclesPage.items || [],
      });
    } catch (err) {
      setError(err.message || "Could not load dashboard data");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [days]);

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
  }, [days]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!comparePrevious) {
      setPrevious(null);
      return undefined;
    }
    let cancelled = false;
    setCompareLoading(true);
    Promise.all([api.collections(days * 2), api.waste(days * 2)])
      .then(([collections, waste]) => {
        if (!cancelled) setPrevious({ collections, waste });
      })
      .catch(() => {
        if (!cancelled) setPrevious(null);
      })
      .finally(() => {
        if (!cancelled) setCompareLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [comparePrevious, days]);

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
    reload: load,
    refreshAlerts,
    refreshRoutes,
    previousMetrics,
    compareLoading,
  };
}
