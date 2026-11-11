export function matomoPush(command) {
  if (typeof window === 'undefined' || !Array.isArray(window._paq)) return false;
  window._paq.push(command);
  return true;
}

export function trackSignupCompleted() {
  return matomoPush(['trackEvent', 'Account', 'signup', 'registration_completed']);
}

const SAFE_PAGE_QUERY_KEYS = new Set([
  'tab', 'page', 'page_size', 'source', 'sort', 'marketplace', 'market',
  'readiness', 'view', 'workspace', 'status', 'queue', 'lifecycle',
]);

export function matomoPageUrl(value, base = 'https://posterpro.invalid') {
  try {
    const url = new URL(value, base);
    const query = new URLSearchParams();
    for (const [key, entry] of url.searchParams.entries()) {
      if (SAFE_PAGE_QUERY_KEYS.has(key.toLowerCase())) query.append(key, entry.slice(0, 80));
    }
    const suffix = query.toString();
    return `${url.pathname}${suffix ? `?${suffix}` : ''}${url.hash}`;
  } catch {
    return String(value || '/').split('?')[0].slice(0, 240) || '/';
  }
}
