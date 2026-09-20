export function fmt(value, digits = 0, suffix = "") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toFixed(digits)}${suffix}`;
}

export function na(value, digits = 0, suffix = "") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "N/A";
  return `${Number(value).toFixed(digits)}${suffix}`;
}

export function fmtWhen(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

export function fmtOverflow(hours, predictedFullAt) {
  if (predictedFullAt) return fmtWhen(predictedFullAt);
  if (hours === null || hours === undefined) return "—";
  const n = Number(hours);
  if (Number.isNaN(n)) return "—";
  if (n <= 0) return "Overdue / overflowing";
  if (n < 24) return `${n.toFixed(1)} h`;
  return `${(n / 24).toFixed(1)} d`;
}

export function EmptyState({ label }) {
  return <p className="muted py-6 text-center">{label}</p>;
}

export function PanelError({ message }) {
  return (
    <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
      {message}
    </p>
  );
}

export function SeverityBadge({ value }) {
  const tone = {
    critical: "bg-rose-500/20 text-rose-200",
    warning: "bg-amber-500/20 text-amber-200",
    high: "bg-amber-500/20 text-amber-200",
    p0_critical: "bg-rose-500/20 text-rose-200",
    p1_high: "bg-amber-500/20 text-amber-200",
    p2_medium: "bg-sky-500/20 text-sky-200",
    p3_routine: "bg-slate-500/20 text-slate-200",
    medium: "bg-sky-500/20 text-sky-200",
    info: "bg-slate-500/20 text-slate-200",
    low: "bg-slate-500/20 text-slate-200",
  }[value] || "bg-slate-500/20 text-slate-200";

  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${tone}`}>
      {String(value || "—").replaceAll("_", " ")}
    </span>
  );
}

export const FILL_COLORS = {
  low: "#34d399",
  medium: "#fbbf24",
  high: "#fb923c",
  critical: "#f43f5e",
};

export const ROUTE_COLORS = ["#38bdf8", "#a78bfa", "#f472b6", "#34d399", "#fbbf24", "#fb7185"];

export const PRIORITY_GROUP = {
  p0_critical: "HIGH",
  p1_high: "HIGH",
  p2_medium: "MEDIUM",
  p3_routine: "LOW",
};
