const historyCache = new Map();

export function getCachedHistory(binId) {
  return historyCache.get(binId) || null;
}

export function setCachedHistory(binId, payload) {
  historyCache.set(binId, payload);
}
