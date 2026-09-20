const SPECIFIC = [
  [
    /Dispatch a route immediately via POST \/api\/v1\/routes\/optimize\.?/gi,
    "Dispatch a collection route immediately for the critical bins.",
  ],
  [
    /planning from \/api\/v1\/priorities instead of a fixed round\.?/gi,
    "planning collections from the live priority ranking instead of a fixed round.",
  ],
  [
    /Run `python -m app\.cli predict` to populate forecasts\.?/gi,
    "Refresh overflow forecasts so ranking uses predicted fill times.",
  ],
  [
    /Run python -m app\.cli optimize-routes\.?/gi,
    "Run route optimization to assign the highest-priority bins to available vehicles.",
  ],
];

export function sanitizeOperatorText(text) {
  if (text == null) return text;
  let out = String(text);
  for (const [pattern, replacement] of SPECIFIC) {
    out = out.replace(pattern, replacement);
  }
  out = out.replace(/`python -m app\.cli [^`]+`/gi, "operations tools");
  out = out.replace(/python -m app\.cli \S+/gi, "operations tools");
  out = out.replace(/\b(?:GET|POST|PUT|PATCH|DELETE)\s+\/api\/[^\s.,;)]+/gi, "the operations dashboard");
  out = out.replace(/\/api\/v\d+\/[^\s.,;)]+/gi, "the operations dashboard");
  return out;
}
