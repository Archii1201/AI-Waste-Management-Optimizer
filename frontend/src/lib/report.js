import { sanitizeOperatorText } from "./operatorCopy.js";

function line(label, value) {
  if (value === null || value === undefined || value === "") return `${label}: N/A`;
  return `${label}: ${value}`;
}

function periodLabel(days) {
  if (days === 1) return "24 hours";
  return `${days} days`;
}

export function buildOperationsReport({ data, days, extras }) {
  const fill = data?.fill || {};
  const collections = data?.collections || {};
  const waste = data?.waste || {};
  const routes = data?.routeAnalytics || {};
  const alerts = data?.alerts || [];
  const summary = data?.alertSummary || {};
  const recs = data?.recommendations || [];
  const priorities = data?.priorities || [];
  const predictions = data?.predictions || [];
  const model = extras?.predictionModel || {};
  const accuracy = extras?.predictionAccuracy || {};
  const classify = extras?.classifyStats || {};
  const classifier = extras?.classifyModel || {};

  const high = priorities.filter((item) => item.tier === "p0_critical" || item.tier === "p1_high").length;
  const medium = priorities.filter((item) => item.tier === "p2_medium").length;
  const low = priorities.filter((item) => item.tier === "p3_routine").length;
  const overflowSoon = predictions.filter((item) => item.overflow_within_horizon).length;

  const lines = [
    "EcoFlow AI",
    "Intelligent Waste Operations",
    "Operations report",
    "",
    `Generated: ${new Date().toLocaleString()}`,
    line("Reporting period", periodLabel(days)),
    "",
    "1. Reporting period",
    line("Window", periodLabel(days)),
    line("City", data?.reference?.map?.city),
    "",
    "2. Network overview",
    line("Active bins", fill.active_bins),
    line("Average fill %", fill.avg_fill_level),
    line("Bins over threshold", fill.bins_over_threshold),
    line("Critical bins", fill.bins_critical),
    line("Capacity in use %", fill.capacity_in_use_pct),
    "",
    "3. Fill / overflow statistics",
    line("Forecast overflow within 24h", fill.bins_forecast_to_overflow_24h),
    line("Forecasts stored", fill.forecasts_stored),
    line("Forecast MAE (hours)", fill.forecast_mae_hours),
    line("Overflow collections", collections.overflow_collections),
    line("Overflow collection %", collections.overflow_collection_pct),
    "",
    "4. Collection statistics",
    line("Collections", collections.collections),
    line("Collected weight kg", collections.total_weight_kg),
    line("Average fill at collection %", collections.avg_fill_at_collection),
    line("Early collections %", collections.early_collection_pct),
    line("Average contamination %", collections.avg_contamination_pct),
    "",
    "5. Recycling / diversion statistics",
    line("Total waste kg", waste.total_weight_kg),
    line("Recyclable kg", waste.recyclable_kg),
    line("Non-recyclable kg", waste.non_recyclable_kg),
    line("Diversion rate %", waste.diversion_rate_pct),
    line("Images classified", waste.images_classified),
    "",
    "6. AI prediction information",
    line("Model available", model.available),
    line("Model version", model.model_version),
    line("Predictions loaded", predictions.length),
    line("Overflow within alert horizon", overflowSoon),
    line("Scored predictions", accuracy.scored_predictions),
    line("Live MAE hours", accuracy.mean_absolute_error_hours),
    line("Within 6 hours %", accuracy.within_6_hours_pct),
    model.message ? line("Model note", model.message) : null,
    "",
    "7. Waste classification information",
    line("Classifier available", classifier.available),
    line("Classifier version", classifier.model_version),
    line("Classifications", classify.total),
    line("Recyclable %", classify.recyclable_pct),
    line("Pending review", classify.pending_review),
    line("Mean confidence", classify.mean_confidence),
    classifier.message ? line("Classifier note", classifier.message) : null,
    "",
    "8. Priority information",
    line("Ranked bins", priorities.length),
    line("HIGH (p0/p1)", high),
    line("MEDIUM (p2)", medium),
    line("LOW (p3)", low),
    "",
    "9. Route / vehicle statistics",
    line("Routes", routes.routes),
    line("Total stops", routes.total_stops),
    line("Total distance km", routes.total_distance_km),
    line("Distance saved km", routes.distance_saved_km),
    line("Distance saved %", routes.distance_saved_pct),
    line("Estimated cost", routes.estimated_cost),
    line("Vehicles in fleet", data?.vehicles?.length),
    line("Deferred bin records", routes.deferred_bins),
    "",
    "10. Alerts",
    line("Open", summary.open),
    line("Critical open", summary.critical_open),
    line("Acknowledged", summary.acknowledged),
    line("Resolved", summary.resolved),
    alerts.length === 0 ? "No open alerts in the current list." : "Open alert titles:",
    ...alerts.slice(0, 25).map((alert) => `- [${alert.severity}] ${alert.title}`),
    "",
    "11. Recommendations",
    recs.length === 0 ? "No recommendations for this window." : "",
    ...recs.flatMap((item) => [
      `- [${item.priority}] ${item.category}: ${item.title}`,
      `  ${sanitizeOperatorText(item.detail)}`,
      `  Action: ${sanitizeOperatorText(item.action)}`,
    ]),
    "",
    "Values marked N/A were not returned by the backend for this window.",
  ];

  return lines.filter((row) => row !== null).join("\n");
}

export function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
