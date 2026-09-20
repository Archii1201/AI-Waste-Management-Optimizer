function apiPrefix() {
  const raw = import.meta.env.VITE_API_BASE_URL;
  const origin =
    typeof raw === "string" ? raw.trim().replace(/\/+$/, "") : "";
  return `${origin}/api/v1`;
}

const API_PREFIX = apiPrefix();

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function errorMessage(body, fallback) {
  if (!body || typeof body !== "object") return fallback;
  if (body.error && typeof body.error === "object" && body.error.message) {
    return body.error.message;
  }
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) {
    return body.detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  }
  if (typeof body.message === "string") return body.message;
  return fallback;
}

async function parseBody(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function request(path, options = {}) {
  const response = await fetch(`${API_PREFIX}${path}`, options);
  const body = await parseBody(response);
  if (!response.ok) {
    throw new ApiError(
      errorMessage(body, `Request failed (${response.status})`),
      response.status
    );
  }
  return body;
}

async function requestOptional(path) {
  try {
    return await request(path);
  } catch (err) {
    if (err.status === 404) return null;
    throw err;
  }
}

function postJson(path, payload) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
}

export const api = {
  health: () => request("/health"),
  reference: () => request("/reference"),
  overview: (days = 30) => request(`/analytics/overview?days=${days}`),
  zones: (days = 30) => request(`/analytics/zones?days=${days}`),
  collections: (days = 30) => request(`/analytics/collections?days=${days}`),
  waste: (days = 30) => request(`/analytics/waste-estimation?days=${days}`),
  fill: () => request("/analytics/fill"),
  recommendations: (days = 30) => request(`/analytics/recommendations?days=${days}`),
  routeAnalytics: (days = 30) => request(`/analytics/routes?days=${days}`),
  alerts: () => request("/alerts?open_only=true&limit=200"),
  alertSummary: () => request("/alerts/summary"),
  acknowledgeAlert: (id, acknowledged_by = "operations") =>
    postJson(`/alerts/${id}/acknowledge`, { acknowledged_by }),
  resolveAlert: (id, note = "Resolved from dashboard") =>
    postJson(`/alerts/${id}/resolve`, { note }),
  routes: () => request("/routes?limit=50"),
  optimizeRoutes: (payload = {}) => postJson("/routes/optimize", payload),
  bins: () => request("/bins?page=1&page_size=500&sort_by=fill_level&sort_desc=true"),
  bin: (id) => request(`/bins/${id}`),
  binReadings: (id, limit = 400) => request(`/bins/${id}/readings?limit=${limit}`),
  binCollections: (id, limit = 5) => request(`/bins/${id}/collections?limit=${limit}`),
  binPrediction: (id) => requestOptional(`/predictions/bins/${id}`),
  predictions: () => request("/predictions?limit=1000"),
  priorities: () => request("/priorities?limit=500"),
  vehicles: () => request("/vehicles?page=1&page_size=100"),
  classifyImage: (file, binId) => {
    const form = new FormData();
    form.append("file", file);
    if (binId) form.append("bin_id", String(binId));
    return request("/classify", { method: "POST", body: form });
  },
  classifyStats: () => request("/classify/stats"),
  classifyModel: () => request("/classify/model"),
  predictionModel: () => request("/predictions/model"),
  predictionAccuracy: (days = 7) => request(`/predictions/accuracy?lookback_days=${days}`),
  simulationStatus: () => request("/simulation/status"),
  simulationStart: () => postJson("/simulation/start", {}),
  simulationStop: () => postJson("/simulation/stop", {}),
  simulationTick: () => postJson("/simulation/tick", {}),
  whatIf: (payload) => postJson("/scenarios/what-if", payload),
};
