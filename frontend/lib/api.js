const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';

async function jsonFetch(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.message || 'Request failed');
  }
  return data;
}

export async function fetchListings() {
  return jsonFetch(`${API_BASE}/listings`);
}

export async function fetchClusters() {
  return jsonFetch(`${API_BASE}/clusters`);
}

export async function updateListing(id, body) {
  return jsonFetch(`${API_BASE}/listings/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function generateListing(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
}

export async function publishListing(id, marketplaces) {
  return jsonFetch(`${API_BASE}/listings/${id}/publish`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ marketplaces }),
  });
}

export async function fetchMarketplaceStatus(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/marketplace_status`);
}

export async function syncSoldEverywhere(listingIds = []) {
  return jsonFetch(`${API_BASE}/listings/sync_sold`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ listing_ids: listingIds }),
  });
}

export async function fetchMarketplaces() {
  return jsonFetch(`${API_BASE}/marketplaces`);
}

export async function connectMarketplace(name, userId = 1) {
  return jsonFetch(`${API_BASE}/marketplaces/${name}/connect?user_id=${userId}`, { method: 'POST' });
}

export async function fetchEbayAuthUrl(userId, redirectUri) {
  const url = new URL(`${API_BASE}/ebay/auth/url`);
  url.searchParams.set('user_id', userId);
  if (redirectUri) url.searchParams.set('redirect_uri', redirectUri);
  return jsonFetch(url.toString());
}

export async function fetchEbayStatus(id) {
  return jsonFetch(`${API_BASE}/ebay/status/${id}`);
}

export async function fetchAnalyticsOverview(userId = 1) {
  return jsonFetch(`${API_BASE}/analytics/overview?user_id=${userId}`);
}

export async function fetchPricingRecommendation(id) {
  return jsonFetch(`${API_BASE}/pricing/recommendations/${id}`);
}

export async function optimizeListing(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/optimize`, { method: 'POST' });
}

export async function fetchPrediction(id) {
  return jsonFetch(`${API_BASE}/predictions/${id}`);
}

export async function fetchAlerts(userId = 1) {
  return jsonFetch(`${API_BASE}/alerts?user_id=${userId}`);
}

export async function fetchListingPricing(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/pricing`);
}

export async function fetchAutonomousConfig() {
  return jsonFetch(`${API_BASE}/config/autonomous`);
}

export async function toggleAutonomousMode(enabled) {
  return jsonFetch(`${API_BASE}/config/toggle-autonomous`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(typeof enabled === 'boolean' ? { enabled } : {}),
  });
}

export async function fetchEbayOfferDashboard(userId = 1) {
  return jsonFetch(`${API_BASE}/ebay/offers/dashboard?user_id=${userId}`);
}

export async function fetchPlatformConfig(userId = 1) {
  return jsonFetch(`${API_BASE}/users/${userId}/platform-config`);
}

export async function updatePlatformConfig(userId, marketplaces) {
  return jsonFetch(`${API_BASE}/users/${userId}/platform-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ marketplaces }),
  });
}

export async function fetchStorageUnitBatches() {
  return jsonFetch(`${API_BASE}/batch/storage-unit`);
}

export async function fetchStorageUnitBatch(batchId) {
  return jsonFetch(`${API_BASE}/batch/storage-unit/${batchId}`);
}

export async function runOvernightBatch(batchId) {
  return jsonFetch(`${API_BASE}/batch/storage-unit/${batchId}/run-overnight`, { method: 'POST' });
}

export async function runAllOvernightBatches() {
  return jsonFetch(`${API_BASE}/batch/storage-unit/run-overnight`, { method: 'POST' });
}

export async function fetchInventory(filters = {}) {
  const url = new URL(`${API_BASE}/inventory`);
  if (filters.label) url.searchParams.set('label', filters.label);
  if (filters.quantityGtOne) url.searchParams.set('quantity_gt_one', 'true');
  if (filters.stale) url.searchParams.set('stale', 'true');
  if (filters.search) url.searchParams.set('search', filters.search);
  if (filters.page) url.searchParams.set('page', String(filters.page));
  if (filters.pageSize) url.searchParams.set('page_size', String(filters.pageSize));
  return jsonFetch(url.toString());
}

export async function bulkEditInventory(payload) {
  return jsonFetch(`${API_BASE}/inventory/bulk-edit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function runInventoryBulkJob(payload) {
  return jsonFetch(`${API_BASE}/inventory/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function fetchBulkJob(jobId) {
  return jsonFetch(`${API_BASE}/bulk-jobs/${jobId}`);
}

export async function fetchSalesDashboard(userId = 1, limit = 100) {
  return jsonFetch(`${API_BASE}/sales/dashboard?user_id=${userId}&limit=${limit}`);
}

export async function updateSaleDetails(saleId, body) {
  return jsonFetch(`${API_BASE}/sales/${saleId}/details`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function fetchSaleDetectionSettings(userId = 1) {
  return jsonFetch(`${API_BASE}/sales/settings/${userId}`);
}

export async function updateSaleDetectionSettings(userId, marketplaces) {
  return jsonFetch(`${API_BASE}/sales/settings/${userId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ marketplaces }),
  });
}


export function toPublicImageUrl(path) {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('blob:') || path.startsWith('/media/')) return path;
  const marker = '/storage/';
  const idx = path.indexOf(marker);
  if (idx >= 0) {
    return `${API_BASE}/media/${path.slice(idx + marker.length)}`;
  }
  if (path.startsWith('./storage/')) {
    return `${API_BASE}/media/${path.replace('./storage/', '')}`;
  }
  return path;
}

export async function processListingPhoto({ listingId, sourceImage, edits, removeBackground = false, file }) {
  const form = new FormData();
  form.append('edits', JSON.stringify(edits || {}));
  form.append('remove_background', String(removeBackground));
  if (sourceImage) form.append('source_image', sourceImage);
  if (file) form.append('photo', file);
  return jsonFetch(`${API_BASE}/listings/${listingId}/photo-tools`, {
    method: 'POST',
    body: form,
  });
}
