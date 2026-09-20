import { useState } from "react";
import { api } from "../api/client.js";
import { buildOperationsReport, downloadText } from "../lib/report.js";
import { PanelError } from "./ui.jsx";

export default function GenerateReport({ data, days }) {
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(null);

  async function generate() {
    setStatus("generating");
    setError(null);
    try {
      const lookback = Math.min(90, Math.max(1, days));
      const [predictionModel, predictionAccuracy, classifyStats, classifyModel] = await Promise.all([
        api.predictionModel().catch(() => ({ available: false })),
        api.predictionAccuracy(lookback).catch(() => ({})),
        api.classifyStats().catch(() => ({})),
        api.classifyModel().catch(() => ({ available: false })),
      ]);
      const text = buildOperationsReport({
        data,
        days,
        extras: { predictionModel, predictionAccuracy, classifyStats, classifyModel },
      });
      const stamp = new Date().toISOString().slice(0, 10);
      downloadText(`waste-operations-report-${stamp}.txt`, text);
      setStatus("success");
    } catch (err) {
      setError(err.message || "Could not generate report");
      setStatus("error");
    }
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <button type="button" className="btn-secondary" onClick={generate} disabled={status === "generating" || !data}>
        {status === "generating" ? "Generating…" : "Generate Report"}
      </button>
      {status === "success" && <span className="text-xs text-emerald-300">Report downloaded</span>}
      {status === "error" && <PanelError message={error} />}
    </div>
  );
}
