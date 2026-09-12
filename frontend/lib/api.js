const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

const DEFAULT_TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_API_TIMEOUT_MS || 15000);

function formatApiErrorMessage(detail, fallback) {
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (detail && typeof detail === "object") {
    const parts = [];
    if (typeof detail.message === "string" && detail.message.trim()) parts.push(detail.message.trim());
    if (typeof detail.error === "string" && detail.error.trim() && detail.error !== detail.message) parts.push(detail.error.trim());
    if (typeof detail.status === "string" && detail.status.trim()) parts.push(`status: ${detail.status.trim()}`);
    if (detail.persisted_status) parts.push(`persisted_status: ${String(detail.persisted_status)}`);
    if (detail.expected_status) parts.push(`expected_status: ${String(detail.expected_status)}`);
    if (!parts.length) {
      try {
        return JSON.stringify(detail);
      } catch (error) {
        return String(detail);
      }
    }
    return parts.join(" | ");
  }
  return fallback;
}

async function jsonFetch(url, options = {}) {
  const controller = new AbortController();
  const timeoutMs = Number(options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const timeoutId = Number.isFinite(timeoutMs) && timeoutMs > 0
    ? setTimeout(() => controller.abort(), timeoutMs)
    : null;
  let response;
  try {
    response = await fetch(url, {
      credentials: "include",
      signal: controller.signal,
      ...options,
    });
  } catch (error) {
    if (timeoutId) clearTimeout(timeoutId);
    if (error?.name === "AbortError") {
      throw new Error("Request timed out. Please retry.");
    }
    throw error;
  }
  if (timeoutId) clearTimeout(timeoutId);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : null;
  const text = data ? "" : await response.text();
  if (!response.ok) {
    const message = formatApiErrorMessage(
      data?.detail ?? data?.message,
      text || `Request failed (${response.status} ${response.statusText})`,
    );
    throw new Error(
      message
    );
  }
  return data;
}

export async function fetchCurrentUser() {
  return jsonFetch(`${API_BASE}/auth/me`);
}

export async function updateCurrentUser(body) {
  return jsonFetch(`${API_BASE}/auth/me`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchSettingsPanels() {
  return jsonFetch(`${API_BASE}/auth/settings/panels`);
}

export async function updateServerSettings(body) {
  return jsonFetch(`${API_BASE}/auth/settings/server`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateHostedPages(body) {
  return jsonFetch(`${API_BASE}/auth/settings/hosted-pages`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function importHostedPageTheme(body) {
  return jsonFetch(`${API_BASE}/auth/settings/hosted-pages/import-theme`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function publishHostedPages(body) {
  return jsonFetch(`${API_BASE}/auth/settings/hosted-pages/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function registerUser(body) {
  return jsonFetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function loginUser(body) {
  return jsonFetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function logoutUser() {
  return jsonFetch(`${API_BASE}/auth/logout`, {
    method: "POST",
  });
}

export async function forgotPassword(body) {
  return jsonFetch(`${API_BASE}/auth/password/forgot`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function resetPassword(body) {
  return jsonFetch(`${API_BASE}/auth/password/reset`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function changePassword(body) {
  return jsonFetch(`${API_BASE}/auth/password/change`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateSessionViewMode(viewAsRegular) {
  return jsonFetch(`${API_BASE}/auth/session/view-mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ view_as_regular: viewAsRegular }),
  });
}

export async function fetchListings(options = {}) {
  // The unified catalog may contain a large recovery/import history.  This
  // request is paged in the workspace so it cannot block rendering on every
  // historical record. Callers without page options retain the legacy shape.
  const params = new URLSearchParams();
  if (options.page) params.set('page', String(options.page));
  if (options.pageSize) params.set('page_size', String(options.pageSize));
  if (options.sourceType && options.sourceType !== 'all') params.set('source_type', options.sourceType);
  if (options.marketplace && options.marketplace !== 'all') params.set('marketplace', options.marketplace);
  if (options.readiness && options.readiness !== 'all') params.set('readiness', options.readiness);
  if (options.queue && options.queue !== 'all') params.set('queue', options.queue);
  if (options.search) params.set('search', options.search);
  if (options.sortBy) params.set('sort_by', options.sortBy);
  if (options.sortDir) params.set('sort_dir', options.sortDir);
  if (options.summaryOnly) params.set('summary_only', 'true');
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return jsonFetch(`${API_BASE}/listings${suffix}`, { timeoutMs: 60000 });
}

export async function fetchPublicStorefrontListings(options = {}) {
  const params = new URLSearchParams();
  if (options.page) params.set('page', String(options.page));
  if (options.pageSize) params.set('page_size', String(options.pageSize));
  if (options.search) params.set('search', options.search);
  if (options.sortBy) params.set('sort_by', options.sortBy);
  if (options.sortDir) params.set('sort_dir', options.sortDir);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return jsonFetch(`${API_BASE}/public/storefront/listings${suffix}`, { timeoutMs: 30000 });
}

export async function fetchIntakeSettings() {
  return jsonFetch(`${API_BASE}/intake/settings`);
}

export async function updateIntakeSettings(body) {
  return jsonFetch(`${API_BASE}/intake/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function setIntakeDraftingPaused(settings, paused) {
  return updateIntakeSettings({
    ...(settings || {}),
    drafting_paused: Boolean(paused),
  });
}

export async function fetchIntakeSessions() {
  return jsonFetch(`${API_BASE}/intake/sessions`);
}

export async function createIntakeSession(body) {
  return jsonFetch(`${API_BASE}/intake/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function createIntakeSlate(body) {
  return jsonFetch(`${API_BASE}/intake/slates`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function analyzeVoiceIntake(body) {
  return jsonFetch(`${API_BASE}/intake/voice/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function transcribeVoiceIntake(body) {
  return jsonFetch(`${API_BASE}/intake/voice/transcribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function retryIntakeSlateBridgeUpload(slateId) {
  return jsonFetch(`${API_BASE}/intake/slates/${slateId}/bridge-upload`, {
    method: "POST",
  });
}

export async function printIntakeLabel(slateId) {
  return jsonFetch(`${API_BASE}/intake/slates/${slateId}/label/print`, {
    method: "POST",
  });
}

export async function markIntakeLabelWrittenOnBox(slateId) {
  return jsonFetch(`${API_BASE}/intake/slates/${slateId}/label/write-on-box`, {
    method: "POST",
  });
}

export async function fetchGooglePhotosStatus() {
  return jsonFetch(`${API_BASE}/intake/google-photos/status`);
}

export async function fetchGooglePhotosAuthUrl() {
  return jsonFetch(`${API_BASE}/intake/google-photos/auth/url`);
}

export function getGooglePhotosConnectUrl() {
  return `${API_BASE}/intake/google-photos/connect`;
}

export async function startGooglePhotosOAuth() {
  const payload = await fetchGooglePhotosAuthUrl();
  const authUrl = String(payload?.auth_url || '').trim();
  if (!authUrl) {
    throw new Error('Google Photos OAuth URL was not returned by the server.');
  }
  return payload;
}

export async function updateIntakeSlate(slateId, body) {
  return jsonFetch(`${API_BASE}/intake/slates/${slateId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchIntakeSlate(slateId) {
  return jsonFetch(`${API_BASE}/intake/slates/${slateId}`);
}

export async function fetchIntakeQueue() {
  return jsonFetch(`${API_BASE}/intake/queue`);
}

export async function runIntakeMonitor() {
  return jsonFetch(`${API_BASE}/intake/monitor/run`, {
    method: "POST",
  });
}

export async function syncIntakeAlbumTruth() {
  return jsonFetch(`${API_BASE}/intake/monitor/sync-current`, {
    method: "POST",
    timeoutMs: 0,
  });
}

export async function reconcileIntakeTimeline(body = {}) {
  return jsonFetch(`${API_BASE}/intake/timeline/reconcile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs: 0,
  });
}

export async function runIntakeIntegrityScan() {
  return jsonFetch(`${API_BASE}/intake/integrity-scan`, { method: "POST" });
}

export async function fetchIntakeTimeline({ limit = 500, offset = 0 } = {}) {
  return jsonFetch(`${API_BASE}/intake/timeline?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`);
}

export async function createRetroactiveSlate(body) {
  return jsonFetch(`${API_BASE}/intake/slates/retroactive`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

export async function assignIntakeUnassignedPhotos(body) {
  return jsonFetch(`${API_BASE}/intake/unassigned/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function applyIntakePhotoBoundaries(body) {
  return jsonFetch(`${API_BASE}/intake/photos/boundaries/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs: 0,
  });
}

export async function draftIntakeBatch(batchId, body = {}) {
  return jsonFetch(`${API_BASE}/intake/batches/${batchId}/draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateIntakePhoto(photoId, body) {
  return jsonFetch(`${API_BASE}/intake/photos/${photoId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function splitIntakeBatch(batchId, body) {
  return jsonFetch(`${API_BASE}/intake/batches/${batchId}/split`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function mergeIntakeBatches(body) {
  return jsonFetch(`${API_BASE}/intake/batches/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function buildIntakeExportUrl() {
  return `${API_BASE}/intake/export.csv`;
}

export async function backfillVineListingImages(options = {}) {
  const url = new URL(`${API_BASE}/listings/vine/backfill-images`, window.location.origin);
  if (options.includeArchived != null) url.searchParams.set("include_archived", String(Boolean(options.includeArchived)));
  if (options.forceRefresh != null) url.searchParams.set("force_refresh", String(Boolean(options.forceRefresh)));
  if (options.strictMatch != null) url.searchParams.set("strict_match", String(Boolean(options.strictMatch)));
  if (options.onlyMissingImages != null) url.searchParams.set("only_missing_images", String(Boolean(options.onlyMissingImages)));
  if (options.sinceOrderDate) url.searchParams.set("since_order_date", String(options.sinceOrderDate));
  if (Number.isFinite(Number(options.limit))) url.searchParams.set("limit", String(Number(options.limit)));
  if (Array.isArray(options.listingIds) && options.listingIds.length) {
    options.listingIds.forEach((listingId) => {
      if (Number.isFinite(Number(listingId))) {
        url.searchParams.append("listing_ids", String(Number(listingId)));
      }
    });
  }
  return jsonFetch(url.toString().replace(window.location.origin, ""), {
    method: "POST",
  });
}

export async function refreshVineListingMetadata(options = {}) {
  const url = new URL(`${API_BASE}/listings/vine/refresh-metadata`, window.location.origin);
  if (options.includeArchived != null) url.searchParams.set("include_archived", String(Boolean(options.includeArchived)));
  if (options.sinceOrderDate) url.searchParams.set("since_order_date", String(options.sinceOrderDate));
  if (Number.isFinite(Number(options.limit))) url.searchParams.set("limit", String(Number(options.limit)));
  return jsonFetch(url.toString().replace(window.location.origin, ""), {
    method: "POST",
  });
}

export async function repairAllVineListingImages(options = {}) {
  const url = new URL(`${API_BASE}/listings/vine/repair-all-images`, window.location.origin);
  if (options.includeArchived != null) url.searchParams.set("include_archived", String(Boolean(options.includeArchived)));
  if (options.forceRefresh != null) url.searchParams.set("force_refresh", String(Boolean(options.forceRefresh)));
  if (options.useBridgeSession != null) url.searchParams.set("use_bridge_session", String(Boolean(options.useBridgeSession)));
  if (options.onlyMissingImages != null) url.searchParams.set("only_missing_images", String(Boolean(options.onlyMissingImages)));
  if (Number.isFinite(Number(options.limit))) url.searchParams.set("limit", String(Number(options.limit)));
  if (Number.isFinite(Number(options.chunkSize))) url.searchParams.set("chunk_size", String(Number(options.chunkSize)));
  return jsonFetch(url.toString().replace(window.location.origin, ""), {
    method: "POST",
  });
}

export async function fetchListingPage(params = {}) {
  const url = new URL(`${API_BASE}/listings`);
  if (params.page) url.searchParams.set("page", String(params.page));
  if (params.pageSize) url.searchParams.set("page_size", String(params.pageSize));
  if (params.sourceType && params.sourceType !== "all") url.searchParams.set("source_type", params.sourceType);
  if (params.queue && params.queue !== "all") url.searchParams.set("queue", params.queue);
  if (params.search) url.searchParams.set("search", params.search);
  if (params.sortBy) url.searchParams.set("sort_by", params.sortBy);
  if (params.sortDir) url.searchParams.set("sort_dir", params.sortDir);
  return jsonFetch(url.toString());
}

export async function fetchListing(id) {
  return jsonFetch(`${API_BASE}/listings/${id}`);
}

export async function createListing(body) {
  return jsonFetch(`${API_BASE}/listings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchCrosspostPreview(id, marketplaces = []) {
  const url = new URL(`${API_BASE}/listings/${id}/crosspost-preview`);
  if (marketplaces.length) {
    url.searchParams.set("marketplaces", marketplaces.join(","));
  }
  return jsonFetch(url.toString());
}

export async function queueCrosspostJob(id, body) {
  return jsonFetch(`${API_BASE}/listings/${id}/crosspost-jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchCrosspostJobs(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/crosspost-jobs`);
}

export async function searchEbayCategories(query) {
  return jsonFetch(`${API_BASE}/ebay/taxonomy/suggestions?q=${encodeURIComponent(query)}`);
}
export async function browseEbayCategories(parentCategoryId = "") {
  const query = parentCategoryId ? `?parent_category_id=${encodeURIComponent(parentCategoryId)}` : "";
  return jsonFetch(`${API_BASE}/ebay/taxonomy/browse${query}`);
}
export async function classifyTimelineAssets(photoIds, classification) { return jsonFetch(`${API_BASE}/intake/timeline/classify`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ photo_ids: photoIds, classification }) }); }
export async function deleteTimelineAsset(photoId) { return jsonFetch(`${API_BASE}/intake/timeline/assets/${encodeURIComponent(photoId)}`, { method: 'DELETE' }); }
export async function setTimelinePrimary(photoId, options = {}) { return jsonFetch(`${API_BASE}/intake/timeline/primary`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ photo_id: photoId, clear: Boolean(options.clear) }) }); }
export async function resetTimelineClassifications(options = {}) { return jsonFetch(`${API_BASE}/intake/timeline/reset-classifications`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(options) }); }
export async function reprioritizeCorrectionJob(jobId, priority) { return jsonFetch(`${API_BASE}/marketplace-jobs/correction-jobs/${jobId}/priority`, { method: 'PATCH', body: JSON.stringify({ priority }) }); }

export async function createMarketplaceImportJob(body) {
  return jsonFetch(`${API_BASE}/imports/marketplaces/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function bulkImportMarketplaces(body = {}) {
  // The compatibility route fans one operator action into durable, per-market
  // jobs.  It prevents an all-marketplace request from being mistaken for one
  // malformed import payload.
  return jsonFetch(`${API_BASE}/imports/marketplaces/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchMarketplaceImportJobs() {
  return jsonFetch(`${API_BASE}/imports/marketplaces/jobs`);
}

export async function fetchMarketplaceJobsOverview(options = {}) {
  const url = new URL(`${API_BASE}/marketplace-jobs/overview`, window.location.origin);
  if (Number.isFinite(Number(options.limit))) {
    url.searchParams.set("limit", String(Number(options.limit)));
  }
  if (options.compact != null) {
    url.searchParams.set("compact", String(Boolean(options.compact)));
  }
  return jsonFetch(url.toString().replace(window.location.origin, ""));
}

export async function fetchProcessingHealth() {
  return jsonFetch(`${API_BASE}/processing/health`);
}

export async function fetchProcessingBlockers(reason, limit = 100) {
  const params = new URLSearchParams({ reason: String(reason || ""), limit: String(limit) });
  return jsonFetch(`${API_BASE}/processing/blockers?${params.toString()}`);
}

export async function fetchProcessNotifications(options = {}) {
  const url = new URL(`${API_BASE}/notifications`, window.location.origin);
  url.searchParams.set("limit", String(Number.isFinite(Number(options.limit)) ? Number(options.limit) : 20));
  url.searchParams.set("unread_only", options.unreadOnly ? "1" : "0");
  url.searchParams.set("offset", String(Number.isFinite(Number(options.offset)) ? Number(options.offset) : 0));
  return jsonFetch(url.toString().replace(window.location.origin, ""));
}

export async function markProcessNotificationRead(notificationId) {
  return jsonFetch(`${API_BASE}/notifications/${notificationId}/read`, {
    method: "POST",
  });
}

export async function markAllProcessNotificationsRead() {
  return jsonFetch(`${API_BASE}/notifications/read-all`, {
    method: "POST",
  });
}

export async function bulkProcessNotifications(notificationIds, action, options = {}) {
  return jsonFetch(`${API_BASE}/notifications/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notification_ids: notificationIds, action, select_all: Boolean(options.selectAll) }),
  });
}

export async function bulkRequeueMarketplaceJobs({ statuses = ["failed"], jobTypes = ["crosspost", "import"] } = {}) {
  return jsonFetch(`${API_BASE}/marketplace-jobs/bulk-requeue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ statuses, job_types: jobTypes }),
  });
}

export async function fetchCrosspostJob(jobId) {
  return jsonFetch(`${API_BASE}/marketplace-crosspost-jobs/${jobId}`);
}

export async function fetchAssistedMarketplaceJobs() {
  return jsonFetch(`${API_BASE}/assisted-marketplace-jobs`);
}

export async function fetchAssistedMarketplaceJob(jobId) {
  return jsonFetch(`${API_BASE}/assisted-marketplace-jobs/${jobId}`);
}

export async function fetchMarketplaceImportJob(jobId) {
  return jsonFetch(`${API_BASE}/marketplace-import-jobs/${jobId}`);
}

export async function retryCrosspostJob(jobId) {
  return jsonFetch(`${API_BASE}/marketplace-crosspost-jobs/${jobId}/retry`, {
    method: "POST",
  });
}

export async function cancelCrosspostJob(jobId) {
  return jsonFetch(`${API_BASE}/marketplace-crosspost-jobs/${jobId}/cancel`, {
    method: "POST",
  });
}

export async function retryMarketplaceImportJob(jobId) {
  return jsonFetch(`${API_BASE}/marketplace-import-jobs/${jobId}/retry`, {
    method: "POST",
  });
}

export async function cancelMarketplaceImportJob(jobId) {
  return jsonFetch(`${API_BASE}/marketplace-import-jobs/${jobId}/cancel`, {
    method: "POST",
  });
}

export function buildBridgeAssetUrl(assetId) {
  return `${API_BASE}/marketplace-jobs/bridge-assets/${encodeURIComponent(assetId)}`;
}

export async function runAutomationBridgeSmokeTest() {
  return jsonFetch(`${API_BASE}/marketplace-jobs/bridge-smoke-test`, {
    method: "POST",
  });
}

export async function fetchBridgeAccounts(marketplace) {
  const url = new URL(`${API_BASE}/marketplace-jobs/bridge-accounts`);
  if (marketplace) {
    url.searchParams.set("marketplace", marketplace);
  }
  return jsonFetch(url.toString());
}

export async function fetchMarketplaceExtensionDevices() {
  return jsonFetch(`${API_BASE}/browser-extension/devices`);
}

export async function createMarketplaceExtensionPairingCode(deviceName = 'PosterPro browser') {
  return jsonFetch(`${API_BASE}/browser-extension/pairing-codes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ device_name: deviceName }),
  });
}

export async function revokeMarketplaceExtensionDevice(deviceId) {
  return jsonFetch(`${API_BASE}/browser-extension/devices/${encodeURIComponent(deviceId)}`, { method: 'DELETE' });
}

export async function upsertBridgeAccount(marketplace, accountKey, body) {
  return jsonFetch(`${API_BASE}/marketplace-jobs/bridge-accounts/${encodeURIComponent(marketplace)}/${encodeURIComponent(accountKey)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateBridgeAccountSession(marketplace, accountKey, body) {
  return jsonFetch(`${API_BASE}/marketplace-jobs/bridge-accounts/${encodeURIComponent(marketplace)}/${encodeURIComponent(accountKey)}/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function connectBridgeAccount(marketplace, accountKey, body) {
  return jsonFetch(`${API_BASE}/marketplace-jobs/bridge-accounts/${encodeURIComponent(marketplace)}/${encodeURIComponent(accountKey)}/connect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function startBridgeAccountConnectSession(marketplace, accountKey, body) {
  return jsonFetch(`${API_BASE}/marketplace-jobs/bridge-accounts/${encodeURIComponent(marketplace)}/${encodeURIComponent(accountKey)}/connect/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchBridgeConnectSession(connectSessionId) {
  return jsonFetch(`${API_BASE}/marketplace-jobs/bridge-connect-sessions/${encodeURIComponent(connectSessionId)}`);
}

export function buildBridgeDesktopFrameUrl(connectSessionId, cacheKey = Date.now()) {
  return `${API_BASE}/marketplace-jobs/bridge-connect-sessions/${encodeURIComponent(connectSessionId)}/desktop-frame?ts=${encodeURIComponent(cacheKey)}`;
}

export async function sendBridgeDesktopAction(connectSessionId, action, body) {
  return jsonFetch(`${API_BASE}/marketplace-jobs/bridge-connect-sessions/${encodeURIComponent(connectSessionId)}/desktop-actions/${encodeURIComponent(action)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function buildBridgeDesktopWebsocketUrl(websocketPath, token) {
  const apiUrl = new URL(API_BASE);
  const basePath = apiUrl.pathname.endsWith("/") ? apiUrl.pathname : `${apiUrl.pathname}/`;
  const normalizedPath = String(websocketPath || "").replace(/^\/+/, "");
  const websocketUrl = new URL(normalizedPath, `${apiUrl.origin}${basePath}`);
  websocketUrl.protocol = apiUrl.protocol === "https:" ? "wss:" : "ws:";
  websocketUrl.searchParams.set("token", token);
  return websocketUrl.toString();
}

export async function fetchClusters() {
  return jsonFetch(`${API_BASE}/clusters`);
}

export async function updateListing(id, body) {
  return jsonFetch(`${API_BASE}/listings/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function savePublishListingChanges(id, body) {
  return jsonFetch(`${API_BASE}/listings/${id}/save-publish-changes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs: 30000,
  });
}

export async function uploadListingPhotos(listingId, { files = [], role, note, source = "actual_upload", operatorState = "suggested" } = {}) {
  const form = new FormData();
  (files || []).forEach((file) => form.append("photos", file));
  if (role) form.append("role", role);
  if (note) form.append("note", note);
  if (source) form.append("source", source);
  if (operatorState) form.append("operator_state", operatorState);
  return jsonFetch(`${API_BASE}/listings/${listingId}/photos/upload`, {
    method: "POST",
    body: form,
    timeoutMs: 180000,
  });
}

export async function approveListingPhotos(listingId, storagePaths = []) {
  return jsonFetch(`${API_BASE}/listings/${listingId}/photos/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ storage_paths: storagePaths }),
  });
}

export async function rejectListingPhotos(listingId, storagePaths = []) {
  return jsonFetch(`${API_BASE}/listings/${listingId}/photos/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ storage_paths: storagePaths }),
  });
}

export async function setPrimaryListingPhoto(listingId, storagePath) {
  return jsonFetch(`${API_BASE}/listings/${listingId}/photos/set-primary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ storage_path: storagePath }),
  });
}

export async function approveListing(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/approve`, {
    method: "POST",
  });
}

export async function approveListingsBulk(listingIds = []) {
  return jsonFetch(`${API_BASE}/listings/approve-bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ listing_ids: listingIds }),
  });
}

export async function deleteListing(id) {
  return jsonFetch(`${API_BASE}/listings/${id}`, {
    method: "DELETE",
  });
}

export async function deleteListingsBulk(listingIds = []) {
  return jsonFetch(`${API_BASE}/listings/delete-bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ listing_ids: listingIds }),
  });
}

export async function fetchListingTemplates(userId, categoryId) {
  const url = new URL(`${API_BASE}/listing-templates`);
  if (userId) url.searchParams.set("user_id", String(userId));
  if (categoryId) url.searchParams.set("category_id", categoryId);
  return jsonFetch(url.toString());
}

export async function createListingTemplate(body) {
  return jsonFetch(`${API_BASE}/listing-templates`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function applyListingTemplate(listingId, templateId) {
  return jsonFetch(`${API_BASE}/listings/${listingId}/apply-template`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ template_id: templateId }),
  });
}

export async function generateListing(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

export async function requestListingRevision(id, fields = [], note = "", priority = 0) {
  return jsonFetch(`${API_BASE}/listings/${id}/request-revision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields, note, priority }),
  });
}

export async function approveAndQueueListings(listingIds, marketplaces = ['ebay']) {
  return jsonFetch(`${API_BASE}/listings/approve-and-queue`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ listing_ids: listingIds, marketplaces, confirm_live_publish: marketplaces.includes('ebay'), confirmation_phrase: 'QUEUE LIVE EBAY READY LISTINGS' }),
  });
}

export async function publishListing(id, marketplaces, extra = {}) {
  return jsonFetch(`${API_BASE}/listings/${id}/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ marketplaces, ...extra }),
  });
}

export async function publishListingEbay(id, extra = {}) {
  return jsonFetch(`${API_BASE}/listings/${id}/publish/ebay`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(extra),
  });
}

export async function publishListingsBulk(listingIds = [], marketplaces, extra = {}) {
  return jsonFetch(`${API_BASE}/listings/publish-bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ listing_ids: listingIds, marketplaces, ...extra }),
  });
}

export async function fetchMarketplacePreflight(listingId, marketplace) {
  return jsonFetch(`${API_BASE}/marketplaces/${encodeURIComponent(marketplace)}/listings/${encodeURIComponent(listingId)}/preflight`);
}

export async function fetchMarketplacePayloadPreview(listingId, marketplace) {
  return jsonFetch(`${API_BASE}/marketplaces/${encodeURIComponent(marketplace)}/listings/${encodeURIComponent(listingId)}/payload-preview`);
}

export async function fetchMarketplacePreflightBulk(body) {
  return jsonFetch(`${API_BASE}/marketplaces/preflight/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function exportMarketplacePreflightCsv(body) {
  const response = await fetch(`${API_BASE}/marketplaces/preflight/bulk/export`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed (${response.status} ${response.statusText})`);
  }
  return response.text();
}

export async function publishMarketplaceReadyBulk(body) {
  return jsonFetch(`${API_BASE}/marketplaces/publish-ready/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchLaunchCandidates(params = {}) {
  const url = new URL(`${API_BASE}/marketplaces/launch-candidates`);
  if (params.marketplace) url.searchParams.set("marketplace", params.marketplace);
  if (Number.isFinite(Number(params.max_items))) url.searchParams.set("max_items", String(Number(params.max_items)));
  if (Number.isFinite(Number(params.max_price))) url.searchParams.set("max_price", String(Number(params.max_price)));
  if (params.include_warning_only != null) url.searchParams.set("include_warning_only", String(Boolean(params.include_warning_only)));
  if (params.include_local_pickup != null) url.searchParams.set("include_local_pickup", String(Boolean(params.include_local_pickup)));
  if (params.include_risky_shipping != null) url.searchParams.set("include_risky_shipping", String(Boolean(params.include_risky_shipping)));
  return jsonFetch(url.toString());
}

export async function fetchEbayLaunchRepairQueue(params = {}) {
  const url = new URL(`${API_BASE}/marketplaces/ebay/launch-repair-queue`);
  if (Number.isFinite(Number(params.max_items))) url.searchParams.set("max_items", String(Number(params.max_items)));
  if (Number.isFinite(Number(params.max_price))) url.searchParams.set("max_price", String(Number(params.max_price)));
  if (params.image_status) url.searchParams.set("image_status", params.image_status);
  if (params.has_category_suggestion != null) url.searchParams.set("has_category_suggestion", String(Boolean(params.has_category_suggestion)));
  if (params.repair_difficulty) url.searchParams.set("repair_difficulty", params.repair_difficulty);
  return jsonFetch(url.toString());
}

export async function applyEbayLaunchRepair(listingId, body = {}) {
  return jsonFetch(`${API_BASE}/marketplaces/ebay/listings/${encodeURIComponent(listingId)}/repair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function runLaunchDrillDryRun(body) {
  return jsonFetch(`${API_BASE}/marketplaces/launch-drill/dry-run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchEbayAccountReadiness() {
  return jsonFetch(`${API_BASE}/marketplaces/ebay/account-readiness`);
}

export async function fetchEbayPolicies(marketplaceId = 'EBAY_US') {
  const url = new URL(`${API_BASE}/marketplaces/ebay/policies`);
  if (marketplaceId) url.searchParams.set('marketplace_id', marketplaceId);
  return jsonFetch(url.toString());
}

export async function syncEbayPolicySettings(body = {}) {
  return jsonFetch(`${API_BASE}/marketplaces/ebay/policies/sync`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function selectEbayPolicySettings(body = {}) {
  return jsonFetch(`${API_BASE}/marketplaces/ebay/policies/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function verifyEbayMerchantLocation(body = {}) {
  return jsonFetch(`${API_BASE}/marketplaces/ebay/location/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function createEbayMerchantLocation(body = {}) {
  return jsonFetch(`${API_BASE}/marketplaces/ebay/location/create`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function refreshEbayPolicySettings() {
  return jsonFetch(`${API_BASE}/marketplaces/ebay/policies/refresh`, {
    method: "POST",
  });
}

export async function fetchMarketplaceStatus(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/marketplace_status`);
}

export async function syncSoldEverywhere(listingIds = []) {
  return jsonFetch(`${API_BASE}/listings/sync_sold`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ listing_ids: listingIds }),
  });
}

export async function fetchMarketplaces() {
  return jsonFetch(`${API_BASE}/marketplaces`);
}

export async function connectMarketplace(name, userId) {
  const url = new URL(`${API_BASE}/marketplaces/${name}/connect`);
  if (userId) url.searchParams.set("user_id", String(userId));
  return jsonFetch(url.toString(), { method: "POST" });
}

export async function fetchEbayAuthUrl(userId, redirectUri) {
  const url = new URL(`${API_BASE}/ebay/auth/url`);
  url.searchParams.set("user_id", userId);
  if (redirectUri) url.searchParams.set("redirect_uri", redirectUri);
  return jsonFetch(url.toString());
}

export async function importEbayTokens(body, userId) {
  const url = new URL(`${API_BASE}/ebay/account/manual`);
  if (userId) url.searchParams.set("user_id", String(userId));
  return jsonFetch(url.toString(), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchPublicSitePage(slug) {
  return jsonFetch(`${API_BASE}/auth/public/site-pages/${encodeURIComponent(slug)}`);
}

export async function fetchEbayStatus(id) {
  return jsonFetch(`${API_BASE}/ebay/status/${id}`);
}

export async function fetchAnalyticsOverview(userId) {
  const url = new URL(`${API_BASE}/analytics/overview`);
  if (userId) url.searchParams.set("user_id", String(userId));
  return jsonFetch(url.toString());
}

export async function fetchAnalyticsDashboard(userId, days = 30) {
  const url = new URL(`${API_BASE}/analytics/dashboard`);
  if (userId) url.searchParams.set("user_id", String(userId));
  url.searchParams.set("days", String(days));
  return jsonFetch(url.toString());
}

export async function fetchPricingRecommendation(id) {
  return jsonFetch(`${API_BASE}/pricing/recommendations/${id}`);
}

export async function refreshPricingRecommendation(id) {
  return jsonFetch(`${API_BASE}/pricing/recommendations/${id}/refresh`, {
    method: "POST",
  });
}

export async function applyPricingRecommendation(id, body) {
  return jsonFetch(`${API_BASE}/pricing/recommendations/${id}/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function bulkPricingAction(body) {
  return jsonFetch(`${API_BASE}/pricing/recommendations/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchListingReadiness(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/readiness`);
}

export async function optimizeListing(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/optimize`, { method: "POST" });
}

export async function fetchPrediction(id) {
  return jsonFetch(`${API_BASE}/predictions/${id}`);
}

export async function fetchAlerts(userId) {
  const url = new URL(`${API_BASE}/alerts`);
  if (userId) url.searchParams.set("user_id", String(userId));
  return jsonFetch(url.toString());
}

export async function fetchListingPricing(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/pricing`);
}

export async function fetchListingIntelligence(id) {
  return jsonFetch(`${API_BASE}/listings/${id}/intelligence`);
}

export async function fetchAutonomousConfig() {
  return jsonFetch(`${API_BASE}/config/autonomous`);
}

export async function toggleAutonomousMode(enabled) {
  return jsonFetch(`${API_BASE}/config/toggle-autonomous`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(typeof enabled === "boolean" ? { enabled } : {}),
  });
}

export async function runDashboardOperatorCommand(body = {}, { timeoutMs = 180000 } = {}) {
  return jsonFetch(`${API_BASE}/dashboard/operator-command`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs,
  });
}

export async function fetchEbayOfferDashboard(userId) {
  const url = new URL(`${API_BASE}/ebay/offers/dashboard`);
  if (userId) url.searchParams.set("user_id", String(userId));
  return jsonFetch(url.toString());
}

export async function fetchPlatformConfig(userId) {
  return jsonFetch(`${API_BASE}/users/${userId}/platform-config`);
}

export async function fetchAccountSetupSummary(userId) {
  return jsonFetch(`${API_BASE}/users/${userId}/setup`);
}

export async function updatePlatformConfig(userId, marketplaces) {
  return jsonFetch(`${API_BASE}/users/${userId}/platform-config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ marketplaces }),
  });
}

export async function updateMarketplaceConnection(userId, marketplace, body) {
  return jsonFetch(`${API_BASE}/users/${userId}/marketplace-connections/${marketplace}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchStorageUnitBatches() {
  return jsonFetch(`${API_BASE}/batch/storage-unit`);
}

export async function ingestPhotos({ files, storageUnitName }) {
  const form = new FormData();
  (files || []).forEach((file) => form.append("photos", file));
  if (storageUnitName) form.append("storage_unit_name", storageUnitName);
  return jsonFetch(`${API_BASE}/ingest/photos`, {
    method: "POST",
    body: form,
    timeoutMs: 180000,
  });
}

export async function createStorageUnitBatch({
  zipFile,
  imageUrls,
  storageUnitName,
  overnightMode = false,
}) {
  const form = new FormData();
  if (zipFile) form.append("zip_file", zipFile);
  if (imageUrls?.length) form.append("image_urls", JSON.stringify(imageUrls));
  if (storageUnitName) form.append("storage_unit_name", storageUnitName);
  form.append("overnight_mode", String(overnightMode));
  return jsonFetch(`${API_BASE}/batch/storage-unit`, {
    method: "POST",
    body: form,
    timeoutMs: 300000,
  });
}

export async function fetchStorageUnitBatch(batchId) {
  return jsonFetch(`${API_BASE}/batch/storage-unit/${batchId}`);
}

export async function runOvernightBatch(batchId) {
  return jsonFetch(`${API_BASE}/batch/storage-unit/${batchId}/run-overnight`, {
    method: "POST",
  });
}

export async function runAllOvernightBatches() {
  return jsonFetch(`${API_BASE}/batch/storage-unit/run-overnight`, {
    method: "POST",
  });
}

export async function fetchInventory(filters = {}) {
  const url = new URL(`${API_BASE}/inventory`);
  if (filters.label) url.searchParams.set("label", filters.label);
  if (filters.quantityGtOne) url.searchParams.set("quantity_gt_one", "true");
  if (filters.stale) url.searchParams.set("stale", "true");
  if (filters.search) url.searchParams.set("search", filters.search);
  if (filters.page) url.searchParams.set("page", String(filters.page));
  if (filters.pageSize)
    url.searchParams.set("page_size", String(filters.pageSize));
  return jsonFetch(url.toString());
}

export async function uploadVineReport(file) {
  const form = new FormData();
  form.append("file", file);
  return jsonFetch(`${API_BASE}/imports/vine/upload`, {
    method: "POST",
    body: form,
    timeoutMs: 300000,
  });
}

export async function fetchVineBatches() {
  return jsonFetch(`${API_BASE}/imports/vine/batches`);
}

export async function fetchVineBatch(batchId) {
  return jsonFetch(`${API_BASE}/imports/vine/batches/${batchId}`);
}

export async function fetchVineMedia(batchId, itemIds) {
  return jsonFetch(`${API_BASE}/imports/vine/batches/${batchId}/fetch-media`, {
    method: "POST",
    timeoutMs: 0,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_ids: itemIds }),
  });
}

export async function repairVineImages(batchId, itemIds) {
  return jsonFetch(`${API_BASE}/imports/vine/batches/${batchId}/repair-images`, {
    method: "POST",
    timeoutMs: 0,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_ids: itemIds }),
  });
}

export async function refreshVineDraftMetadata(batchId) {
  return jsonFetch(`${API_BASE}/imports/vine/batches/${batchId}/refresh-drafts`, {
    method: "POST",
    timeoutMs: 0,
  });
}

export async function createVineInventory(batchId, itemIds, includeLocked = true, includeCancelled = false) {
  return jsonFetch(`${API_BASE}/imports/vine/batches/${batchId}/create-inventory`, {
    method: "POST",
    timeoutMs: 0,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_ids: itemIds, include_locked: includeLocked, include_cancelled: includeCancelled }),
  });
}

export async function createVineDrafts(batchId, itemIds, options = {}) {
  const {
    fetchMediaFirst = false,
    requireMediaForAsin = false,
    allowDraftsWithoutMedia = false,
    includeCancelled = false,
  } = options || {};
  return jsonFetch(`${API_BASE}/imports/vine/batches/${batchId}/create-drafts`, {
    method: "POST",
    timeoutMs: 0,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_ids: itemIds,
      fetch_media_first: !!fetchMediaFirst,
      require_media_for_asin: !!requireMediaForAsin,
      allow_drafts_without_media: !!allowDraftsWithoutMedia,
      include_cancelled: !!includeCancelled,
    }),
  });
}

export async function autoBuildVineDrafts(batchId, options = {}) {
  const {
    itemIds = [],
    newOnly = true,
    includeCancelled = true,
  } = options || {};
  return jsonFetch(`${API_BASE}/imports/vine/batches/${batchId}/auto-build`, {
    method: "POST",
    timeoutMs: 0,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_ids: itemIds,
      new_only: !!newOnly,
      include_cancelled: !!includeCancelled,
    }),
  });
}

export async function updateVineItem(itemId, body) {
  return jsonFetch(`${API_BASE}/imports/vine/items/${itemId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function retryVineItemDiscovery(itemId) {
  return jsonFetch(`${API_BASE}/imports/vine/items/${itemId}/retry-discovery`, {
    method: "POST",
  });
}

export async function bulkEditInventory(payload) {
  return jsonFetch(`${API_BASE}/inventory/bulk-edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function runInventoryBulkJob(payload) {
  return jsonFetch(`${API_BASE}/inventory/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function fetchBulkJob(jobId) {
  return jsonFetch(`${API_BASE}/bulk-jobs/${jobId}`);
}

export async function fetchSalesDashboard(userId, limit = 100, options = {}) {
  const url = new URL(`${API_BASE}/sales/dashboard`);
  if (userId) url.searchParams.set("user_id", String(userId));
  url.searchParams.set("limit", String(limit));
  if (options.search) url.searchParams.set("search", options.search);
  if (options.marketplace) url.searchParams.set("marketplace", options.marketplace);
  if (options.sortBy) url.searchParams.set("sort_by", options.sortBy);
  if (options.sortDir) url.searchParams.set("sort_dir", options.sortDir);
  return jsonFetch(url.toString());
}

export function downloadSalesReportCsv(userId) {
  const suffix = userId ? `?user_id=${userId}` : '';
  window.open(`${API_BASE}/sales/reports/sales.csv${suffix}`, '_blank', 'noopener,noreferrer');
}

export function downloadInventoryReportCsv(userId) {
  const suffix = userId ? `?user_id=${userId}` : '';
  window.open(`${API_BASE}/sales/reports/inventory.csv${suffix}`, '_blank', 'noopener,noreferrer');
}

export async function updateSaleDetails(saleId, body) {
  return jsonFetch(`${API_BASE}/sales/${saleId}/details`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function syncEbayListing(listingId, body = {}) {
  return jsonFetch(`${API_BASE}/marketplaces/ebay/listings/${listingId}/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function refreshEbayInventory(body = {}) {
  return jsonFetch(`${API_BASE}/marketplaces/ebay/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function refreshEbayHistory(body = {}) {
  return jsonFetch(`${API_BASE}/marketplaces/ebay/sync/history`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function reconcileSale(saleId, body = {}) {
  return jsonFetch(`${API_BASE}/sales/${saleId}/reconcile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchSaleDetectionSettings(userId) {
  return jsonFetch(`${API_BASE}/sales/settings/${userId}`);
}

export async function updateSaleDetectionSettings(userId, marketplaces) {
  return jsonFetch(`${API_BASE}/sales/settings/${userId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ marketplaces }),
  });
}

export async function fetchOfferRules(userId) {
  return jsonFetch(`${API_BASE}/sales/offers/rules/${userId}`);
}

export async function updateOfferRules(userId, body) {
  return jsonFetch(`${API_BASE}/sales/offers/rules/${userId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function sendOffersNow(userId) {
  return jsonFetch(`${API_BASE}/sales/offers/send/${userId}`, { method: "POST" });
}

export async function fetchOfferHistory(userId) {
  const url = new URL(`${API_BASE}/sales/offers/history`);
  if (userId) url.searchParams.set("user_id", String(userId));
  return jsonFetch(url.toString());
}

export async function fetchGooglePhotosWatch() {
  return fetchIntakeSettings().then((payload) => ({
    enabled: Boolean(payload?.enabled),
    auto_enrich: Boolean(payload?.auto_draft_listing ?? true),
    album_url: payload?.album_url || "",
    last_synced_at: payload?.last_synced_at || null,
    last_imported_count: Number(payload?.last_imported_count || 0),
    last_error: payload?.last_error || null,
  }));
}

export async function updateGooglePhotosWatch(body) {
  return updateIntakeSettings({
    enabled: Boolean(body?.enabled),
    album_url: body?.album_url || "",
    auto_draft_listing: Boolean(body?.auto_enrich ?? true),
  }).then((payload) => ({
    enabled: Boolean(payload?.enabled),
    auto_enrich: Boolean(payload?.auto_draft_listing ?? true),
    album_url: payload?.album_url || "",
    last_synced_at: payload?.last_synced_at || null,
    last_imported_count: Number(payload?.last_imported_count || 0),
    last_error: payload?.last_error || null,
  }));
}

export async function runGooglePhotosWatch() {
  return jsonFetch(`${API_BASE}/import/google-photos/watch/run`, {
    method: "POST",
  });
}

export function toPublicImageUrl(path) {
  if (!path) return "";
  if (
    path.startsWith("http://") ||
    path.startsWith("https://") ||
    path.startsWith("blob:") ||
    path.startsWith("/media/")
  )
    return path;
  const marker = "/storage/";
  const mediaBase = API_BASE === "/api" ? "/media" : `${API_BASE}/media`;
  const idx = path.indexOf(marker);
  if (idx >= 0) {
    return `${mediaBase}/${path.slice(idx + marker.length)}`;
  }
  if (path.startsWith("storage/")) {
    return `${mediaBase}/${path.slice("storage/".length)}`;
  }
  if (path.startsWith("./storage/")) {
    return `${mediaBase}/${path.replace("./storage/", "")}`;
  }
  return path;
}

export function toThumbnailImageUrl(path, width = 200, height = 200) {
  const publicPath = toPublicImageUrl(path);
  // Normalize both relative and absolute URLs emitted by older listing rows.
  // A number of imported/recovery records predate the public-media helper and
  // contain either an absolute host URL or `/api/media/...`; both are still
  // local originals and must use a bounded derivative in catalog views.
  const localMatch = String(publicPath || '').match(/^(?:https?:\/\/[^/]+)?\/(?:api\/)?media\/([^?#]+)/i);
  if (!localMatch) return publicPath;
  const mediaPath = `/media/${localMatch[1]}`;
  return `/media/thumbnail?path=${encodeURIComponent(mediaPath)}&width=${width}&height=${height}`;
}

export async function processListingPhoto({
  listingId,
  sourceImage,
  edits,
  removeBackground = false,
  file,
}) {
  const form = new FormData();
  form.append("edits", JSON.stringify(edits || {}));
  form.append("remove_background", String(removeBackground));
  if (sourceImage) form.append("source_image", sourceImage);
  if (file) form.append("photo", file);
  return jsonFetch(`${API_BASE}/listings/${listingId}/photo-tools`, {
    method: "POST",
    body: form,
  });
}

export async function diagnoseListings(listingIds, { runFreshPreflight = true, marketplace = "ebay" } = {}) {
  return jsonFetch(`${API_BASE}/admin/listing-diagnostics`, {
    method: "POST",
    body: JSON.stringify({ listing_ids: listingIds, marketplace, run_fresh_preflight: runFreshPreflight }),
  });
}
