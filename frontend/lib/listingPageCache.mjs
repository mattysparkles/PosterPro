const CACHE_TTL_MS = 90_000;
const MAX_CACHE_ENTRIES = 32;
const pages = new Map();

export function readListingPageCache(key, now = Date.now()) {
  const entry = pages.get(String(key || ""));
  if (!entry) return null;
  if (now - entry.savedAt > CACHE_TTL_MS) {
    pages.delete(String(key || ""));
    return null;
  }
  return entry.value;
}

export function writeListingPageCache(key, value, now = Date.now()) {
  const cacheKey = String(key || "");
  if (!cacheKey) return;
  pages.delete(cacheKey);
  pages.set(cacheKey, { savedAt: now, value });
  while (pages.size > MAX_CACHE_ENTRIES) pages.delete(pages.keys().next().value);
}

export function clearListingPageCache() {
  pages.clear();
}
