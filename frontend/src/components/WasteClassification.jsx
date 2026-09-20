import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client.js";
import { EmptyState, PanelError, fmt } from "./ui.jsx";

const BAR_COLORS = ["#34d399", "#60a5fa", "#fbbf24", "#fb7185", "#a78bfa", "#94a3b8"];

export default function WasteClassification({ bins }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [binId, setBinId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  function reset() {
    if (preview) URL.revokeObjectURL(preview);
    setFile(null);
    setPreview(null);
    setResult(null);
    setError(null);
    setBinId("");
  }

  function onFile(event) {
    const next = event.target.files?.[0];
    if (!next) return;
    if (preview) URL.revokeObjectURL(preview);
    setFile(next);
    setPreview(URL.createObjectURL(next));
    setResult(null);
    setError(null);
  }

  async function classify() {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const response = await api.classifyImage(file, binId || undefined);
      setResult(response);
    } catch (err) {
      setError(err.message || "Classification failed");
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const classification = result?.classification;
  const probabilities = Object.entries(classification?.probabilities || {}).sort(
    (a, b) => b[1] - a[1]
  );

  return (
    <section className="card">
      <h2 className="mb-1 font-semibold">Waste classification</h2>
      <p className="muted mb-4">Upload a waste photo. The live classifier API scores the image.</p>

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={onFile}
            className="mb-3 block w-full text-sm"
          />
          {preview ? (
            <img src={preview} alt="Waste preview" className="mb-3 max-h-64 w-full rounded-xl object-contain bg-ink" />
          ) : (
            <EmptyState label="No image selected." />
          )}
          <label className="muted mb-3 block">
            Optional bin
            <select
              className="mt-1 w-full rounded-lg border border-line bg-ink px-2 py-1 text-slate-100"
              value={binId}
              onChange={(event) => setBinId(event.target.value)}
            >
              <option value="">None</option>
              {bins.map((bin) => (
                <option key={bin.id} value={bin.id}>
                  {bin.code} · {bin.waste_type}
                </option>
              ))}
            </select>
          </label>
          <div className="flex gap-2">
            <button type="button" className="btn-primary" disabled={!file || loading} onClick={classify}>
              {loading ? "Classifying…" : "Classify image"}
            </button>
            <button type="button" className="btn-secondary" onClick={reset}>
              Remove image
            </button>
          </div>
        </div>

        <div>
          {error && <PanelError message={error} />}
          {loading && <p className="muted py-8 text-center">Running classifier…</p>}
          {!loading && classification && (
            <div className="space-y-3">
              <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3">
                <p className="text-xs uppercase tracking-wide text-emerald-300">Predicted class</p>
                <p className="text-2xl font-semibold capitalize">{classification.predicted_class}</p>
                <p className="muted">
                  Confidence {fmt(classification.confidence * 100, 1, "%")} ·{" "}
                  {classification.is_recyclable ? "Recyclable" : "Non-recyclable"}
                </p>
              </div>
              {result.contamination?.checked && (
                <p className="text-sm">
                  Contamination: {result.contamination.is_contaminant ? "Yes" : "No"}
                  {result.contamination.message ? ` — ${result.contamination.message}` : ""}
                </p>
              )}
              <p className="text-sm">
                Human review: {classification.needs_review ? "Needed (low confidence)" : "Not required"}
              </p>
              {probabilities.length === 0 ? (
                <EmptyState label="No class probabilities returned." />
              ) : (
                <div className="h-56">
                  <ResponsiveContainer>
                    <BarChart data={probabilities.map(([name, value]) => ({ name, value: value * 100 }))}>
                      <CartesianGrid stroke="#243049" vertical={false} />
                      <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 12 }} />
                      <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} />
                      <Tooltip />
                      <Bar dataKey="value" name="probability %">
                        {probabilities.map((_, index) => (
                          <Cell key={index} fill={BAR_COLORS[index % BAR_COLORS.length]} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
