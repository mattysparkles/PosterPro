const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

const DEFAULT_TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_API_TIMEOUT_MS || 15000);

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
    throw new Error(
      data?.detail
        || data?.message
        || text
        || `Request failed (${response.status} ${response.statusText})`
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

export async function fetchListings() {
  return jsonFetch(`${API_BASE}/listings`);
}

export async function backfillVineListingImages(options = {}) {
  const url = new URL(`${API_BASE}/listings/vine/backfill-images`, window.location.origin);
  if (options.includeArchived != null) url.searchParams.set("include_archived", String(Boolean(options.includeArchived)));
  if (options.forceRefresh != null) url.searchParams.set("force_refresh", String(Boolean(options.forceRefresh)));
  if (options.strictMatch != null) url.searchParams.set("strict_match", String(Boolean(options.strictMatch)));
  if (options.onlyMissingImages != null) url.searchParams.set("only_missing_images", String(Boolean(options.onlyMissingImages)));
  if (Number.isFinite(Number(options.limit))) url.searchParams.set("limit", String(Number(options.limit)));
  return jsonFetch(url.toString().replace(window.location.origin, ""), {
    method: "POST",
  });
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

export async function createMarketplaceImportJob(body) {
  return jsonFetch(`${API_BASE}/imports/marketplaces/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function bulkImportMarketplaces(body = {}) {
  return jsonFetch(`${API_BASE}/imports/marketplaces/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchMarketplaceImportJobs() {
  return jsonFetch(`${API_BASE}/imports/marketplaces/jobs`);
}

export async function fetchMarketplaceJobsOverview() {
  return jsonFetch(`${API_BASE}/marketplace-jobs/overview`);
}

export async function fetchCrosspostJob(jobId) {
  return jsonFetch(`${API_BASE}/marketplace-crosspost-jobs/${jobId}`);
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

export async function createVineInventory(batchId, itemIds, includeLocked = true, includeCancelled = false) {
  return jsonFetch(`${API_BASE}/imports/vine/batches/${batchId}/create-inventory`, {
    method: "POST",
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
  window.open(`${API_BASE}/sales/reports/sales.csv?user_id=${userId}`, '_blank', 'noopener,noreferrer');
}

export function downloadInventoryReportCsv(userId) {
  window.open(`${API_BASE}/sales/reports/inventory.csv?user_id=${userId}`, '_blank', 'noopener,noreferrer');
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
  return jsonFetch(`${API_BASE}/import/google-photos/watch`);
}

export async function updateGooglePhotosWatch(body) {
  return jsonFetch(`${API_BASE}/import/google-photos/watch`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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
