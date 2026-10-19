// PosterPro APIs persist timestamps in UTC (some legacy PostgreSQL DateTime
// columns arrive without an explicit offset). Treat offset-less values as UTC
// before rendering them in the operator's configured business timezone.
export const DEFAULT_POSTERPRO_TIME_ZONE = 'America/New_York';
export const POSTERPRO_TIME_ZONE_STORAGE_KEY = 'posterpro.timezone';

export function getPosterProTimeZone() {
  if (typeof window === 'undefined') return DEFAULT_POSTERPRO_TIME_ZONE;
  try {
    const stored = window.localStorage.getItem(POSTERPRO_TIME_ZONE_STORAGE_KEY);
    if (stored) {
      new Intl.DateTimeFormat('en-US', { timeZone: stored });
      return stored;
    }
  } catch {
    // Use the stable New York default if storage is unavailable or invalid.
  }
  return DEFAULT_POSTERPRO_TIME_ZONE;
}

export function parsePosterProDate(value) {
  if (!value) return null;
  const raw = String(value).trim();
  if (!raw) return null;
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw)
    ? raw
    : `${raw.replace(' ', 'T')}Z`;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function formatPosterProDate(value, options = {}) {
  const parsed = parsePosterProDate(value);
  if (!parsed) return 'Time unavailable';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: options.timeZone || getPosterProTimeZone(),
    dateStyle: 'medium',
    timeStyle: 'short',
    ...options,
  }).format(parsed);
}

export function posterProTimestamp(value) {
  return parsePosterProDate(value)?.getTime() || 0;
}
