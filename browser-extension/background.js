const DEFAULT_SETTINGS = {
  posterproBaseUrl: "https://posterpro.sparkleserver.site",
  defaultMarketplace: "mercari",
};

const MARKETPLACE_HOST_HINTS = [
  { marketplace: "facebook", match: "facebook.com" },
  { marketplace: "mercari", match: "mercari.com" },
  { marketplace: "poshmark", match: "poshmark.com" },
  { marketplace: "etsy", match: "etsy.com" },
  { marketplace: "depop", match: "depop.com" },
  { marketplace: "whatnot", match: "whatnot.com" },
  { marketplace: "vinted", match: "vinted.com" },
  { marketplace: "offerup", match: "offerup.com" },
];

const EXTENSION_VERSION = "0.2.0";
let queuePollActive = false;

function apiRoot(baseUrl) {
  const root = String(baseUrl || "").trim().replace(/\/+$/, "");
  return root.endsWith("/api") ? root : `${root}/api`;
}

async function getAgentSettings() {
  const settings = await loadSettings();
  const local = await chrome.storage.local.get(["posterproDeviceToken", "posterproActiveJob"]);
  return { api: apiRoot(settings.posterproBaseUrl), token: local.posterproDeviceToken || "", activeJob: local.posterproActiveJob || null };
}

async function deviceRequest(path, options = {}) {
  const { api, token } = await getAgentSettings();
  if (!token) throw new Error("Pair this extension with a one-time code from PosterPro Settings first.");
  const response = await fetch(`${api}${path}`, {
    ...options,
    headers: { ...(options.headers || {}), Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!response.ok) throw new Error(data?.detail?.message || data?.detail || `PosterPro request failed (${response.status})`);
  return data;
}

async function setJobState(jobId, status, details = {}) {
  return deviceRequest(`/browser-extension/jobs/${encodeURIComponent(jobId)}/state`, {
    method: "POST",
    body: JSON.stringify({ status, ...details }),
  });
}

function waitForTabComplete(tabId, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("Marketplace page did not finish loading before timeout."));
    }, timeoutMs);
    const listener = (updatedId, changeInfo, tab) => {
      if (updatedId === tabId && (changeInfo.status === "complete" || tab?.status === "complete")) {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve(tab);
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((tab) => {
      if (tab?.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve(tab);
      }
    }).catch((error) => {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      reject(error);
    });
  });
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunk));
  }
  return btoa(binary);
}

async function downloadCanonicalImages(payload, marketplace) {
  const maxCount = marketplace === "poshmark" ? 16 : marketplace === "mercari" ? 10 : 10;
  const urls = Array.isArray(payload.image_urls) ? payload.image_urls.slice(0, maxCount) : [];
  const images = [];
  let totalBytes = 0;
  for (let index = 0; index < urls.length; index += 1) {
    const response = await fetch(String(urls[index]), { credentials: "omit", cache: "no-store" });
    if (!response.ok) throw new Error(`Could not download canonical photo ${index + 1} (HTTP ${response.status}).`);
    const type = response.headers.get("content-type") || "";
    if (!type.toLowerCase().startsWith("image/")) throw new Error(`Canonical photo ${index + 1} did not return image content.`);
    const blob = await response.blob();
    totalBytes += blob.size;
    if (blob.size <= 0 || blob.size > 8 * 1024 * 1024 || totalBytes > 24 * 1024 * 1024) {
      throw new Error("Canonical photos exceed the safe extension upload size limit.");
    }
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const extension = type.includes("png") ? "png" : type.includes("webp") ? "webp" : "jpg";
    images.push({ data_url: `data:${type};base64,${bytesToBase64(bytes)}`, file_name: `posterpro-${index + 1}.${extension}` });
  }
  return images;
}

async function runClaimedJob(job) {
  const jobId = job.id;
  await chrome.storage.local.set({ posterproActiveJob: job });
  await setJobState(jobId, "NAVIGATING");
  const snapshot = job.payload?.marketplace_payload || {};
  const startUrl = String(snapshot.start_url || "");
  if (!/^https:\/\//i.test(startUrl)) throw new Error("Marketplace create URL is missing or not HTTPS.");
  if (String(job.action || "").toUpperCase() === "END") {
    const host = new URL(startUrl).hostname.toLowerCase();
    const hostMatch = {
      facebook: ["facebook.com"], mercari: ["mercari.com"], poshmark: ["poshmark.com"],
      vinted: ["vinted.com"], etsy: ["etsy.com"], offerup: ["offerup.com"],
      depop: ["depop.com"], whatnot: ["whatnot.com"],
    }[String(job.marketplace || "").toLowerCase()] || [];
    if (!hostMatch.some((domain) => host === domain || host.endsWith(`.${domain}`))) {
      throw new Error("Stored external listing URL does not match the requested marketplace.");
    }
    const tab = await chrome.tabs.create({ url: startUrl, active: true });
    await waitForTabComplete(tab.id);
    const review = {
      ok: true,
      stage: "AWAITING_OPERATOR_REVIEW",
      marketplace: job.marketplace,
      action: "END",
      page_url: new URL(startUrl).origin + new URL(startUrl).pathname,
      external_listing_id: job.external_listing_id || snapshot.external_listing_id || null,
      external_url: startUrl,
      populated_fields: [],
      missing_required_fields: ["operator_must_end_listing_and_confirm"],
      submission_performed: false,
    };
    await chrome.storage.local.set({ posterproActiveJob: { ...job, tab_id: tab.id, status: "AWAITING_OPERATOR_REVIEW", fill_result: review } });
    await setJobState(jobId, "AWAITING_OPERATOR_REVIEW", { result: review });
    return review;
  }
  const tab = await chrome.tabs.create({ url: startUrl, active: true });
  await waitForTabComplete(tab.id);
  await setJobState(jobId, "FORM_FILLING");
  const images = await downloadCanonicalImages(snapshot, job.marketplace);
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["marketplace-adapters.js", "content.js"] });
  const result = await chrome.tabs.sendMessage(tab.id, {
    action: "posterpro_fill_listing",
    marketplace: job.marketplace,
    payload: snapshot,
    images,
  });
  if (!result?.ok) throw new Error(result?.error || "Marketplace adapter could not fill the listing form.");
  await chrome.storage.local.set({ posterproActiveJob: { ...job, tab_id: tab.id, status: "AWAITING_OPERATOR_REVIEW", fill_result: result } });
  await setJobState(jobId, "AWAITING_OPERATOR_REVIEW", { result });
  return result;
}

async function pollMarketplaceQueue() {
  if (queuePollActive) return;
  queuePollActive = true;
  try {
    const { token, activeJob } = await getAgentSettings();
    if (!token) return;
    await deviceRequest("/browser-extension/heartbeat", { method: "POST", body: JSON.stringify({ browser: "Chrome", extension_version: EXTENSION_VERSION }) });
    if (activeJob?.id) return;
    const response = await deviceRequest("/browser-extension/jobs/claim", { method: "POST", body: JSON.stringify({}) });
    if (!response?.job) return;
    await chrome.storage.local.set({ posterproLastJob: response.job });
    try {
      const result = await runClaimedJob(response.job);
      await chrome.storage.local.set({ posterproLastJob: { ...response.job, status: "AWAITING_OPERATOR_REVIEW", fill_result: result } });
    } catch (error) {
      await setJobState(response.job.id, "FAILED", { error_code: "EXTENSION_EXECUTION_FAILED", error_detail: String(error?.message || error).slice(0, 1800) }).catch(() => {});
      await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...response.job, status: "FAILED", error: String(error?.message || error) } });
    }
  } catch (error) {
    await chrome.storage.local.set({ posterproAgentError: String(error?.message || error), posterproAgentErrorAt: new Date().toISOString() });
  } finally {
    queuePollActive = false;
  }
}

async function completeActiveJob({ externalListingId, externalUrl }) {
  const { activeJob } = await getAgentSettings();
  if (!activeJob?.id) throw new Error("There is no active assisted job awaiting operator review.");
  const isEnd = String(activeJob.action || "").toUpperCase() === "END";
  const resolvedId = externalListingId || activeJob.external_listing_id || activeJob.payload?.external_listing_id;
  const resolvedUrl = externalUrl || activeJob.external_url || activeJob.payload?.external_url;
  if (!resolvedId && !resolvedUrl) throw new Error("A confirmed marketplace listing ID or URL is required.");
  const id = activeJob.id;
  await setJobState(id, "SUBMITTING");
  await setJobState(id, "SUBMITTED", { external_listing_id: resolvedId || null, external_url: resolvedUrl || null });
  const result = await setJobState(id, "COMPLETED", {
    external_listing_id: resolvedId || null,
    external_url: resolvedUrl || null,
    result: isEnd ? { operator_confirmed_end: true } : { operator_confirmed_submission: true },
  });
  await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: result });
  await pollMarketplaceQueue();
  return result;
}

async function pairDevice(pairingCode, deviceName) {
  const { api } = await getAgentSettings();
  const response = await fetch(`${api}/browser-extension/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pairing_code: pairingCode, device_name: deviceName || "PosterPro browser", browser: "Chrome", extension_version: EXTENSION_VERSION }),
  });
  const text = await response.text();
  let result = null;
  try { result = text ? JSON.parse(text) : null; } catch { result = { detail: text }; }
  if (!response.ok) throw new Error(result?.detail || `Pairing failed (${response.status})`);
  await chrome.storage.local.set({ posterproDeviceToken: result.device_token, posterproDevice: result.device, posterproActiveJob: null, posterproAgentError: null });
  await pollMarketplaceQueue();
  return result.device;
}

async function loadSettings() {
  const stored = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  return {
    posterproBaseUrl: String(stored.posterproBaseUrl || DEFAULT_SETTINGS.posterproBaseUrl).trim().replace(/\/+$/, ""),
    defaultMarketplace: String(stored.defaultMarketplace || DEFAULT_SETTINGS.defaultMarketplace).trim().toLowerCase(),
  };
}

function inferMarketplace(url) {
  const normalized = String(url || "").toLowerCase();
  const hint = MARKETPLACE_HOST_HINTS.find((item) => normalized.includes(item.match));
  return hint ? hint.marketplace : "mercari";
}

function safeJson(value) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return null;
  }
}

async function captureActiveSession() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id || !tab.url) {
    throw new Error("No active marketplace tab was found.");
  }
  const [pageSnapshot, cookies] = await Promise.all([
    chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        title: document.title,
        url: window.location.href,
        hostname: window.location.hostname,
      }),
    }),
    chrome.cookies.getAll({ url: tab.url }),
  ]);

  const snapshot = {
    captured_at: new Date().toISOString(),
    marketplace: inferMarketplace(tab.url),
    session_material_exported: false,
    tab: {
      id: tab.id,
      title: tab.title || null,
      url: `${new URL(tab.url).origin}${new URL(tab.url).pathname}`,
    },
    page: safeJson(pageSnapshot?.[0]?.result) || {},
    cookies_present: Boolean((cookies || []).length),
  };
  if (snapshot.page && typeof snapshot.page === "object") {
    snapshot.page.url = snapshot.tab.url;
  }
  await chrome.storage.local.set({ posterproLastSessionSnapshot: snapshot });
  return snapshot;
}

async function sendSessionToPosterPro(payload) {
  const settings = await loadSettings();
  const baseUrl = apiRoot(settings.posterproBaseUrl);
  if (!baseUrl) {
    throw new Error("PosterPro base URL is not configured in extension options.");
  }
  const response = await fetch(`${baseUrl}/browser-extension/sessions/import`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!response.ok) {
    const detail = data?.detail || data?.message || `PosterPro import failed (${response.status})`;
    throw new Error(String(detail));
  }
  await chrome.storage.local.set({
    posterproLastImportResponse: data,
    posterproLastImportAt: new Date().toISOString(),
  });
  return data;
}

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  await chrome.storage.sync.set({
    posterproBaseUrl: stored.posterproBaseUrl || DEFAULT_SETTINGS.posterproBaseUrl,
    defaultMarketplace: stored.defaultMarketplace || DEFAULT_SETTINGS.defaultMarketplace,
  });
  chrome.alarms.create("posterpro-marketplace-poll", { periodInMinutes: 0.5 });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("posterpro-marketplace-poll", { periodInMinutes: 0.5 });
  pollMarketplaceQueue();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "posterpro-marketplace-poll") pollMarketplaceQueue();
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== "object") {
    return false;
  }

  const action = String(message.action || "").toLowerCase();
  if (action === "capture_session") {
    captureActiveSession()
      .then((snapshot) => sendResponse({ ok: true, snapshot }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (action === "send_to_posterpro") {
    sendSessionToPosterPro(message.payload || {})
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (action === "get_state") {
    Promise.all([
      chrome.storage.sync.get(DEFAULT_SETTINGS),
      chrome.storage.local.get(["posterproLastSessionSnapshot", "posterproLastImportResponse", "posterproLastImportAt", "posterproDevice", "posterproDeviceToken", "posterproActiveJob", "posterproLastJob", "posterproAgentError", "posterproAgentErrorAt"]),
    ]).then(([settings, local]) => {
      sendResponse({ ok: true, settings, local });
    });
    return true;
  }

  if (action === "pair_device") {
    pairDevice(String(message.pairingCode || "").trim(), String(message.deviceName || "PosterPro browser"))
      .then((device) => sendResponse({ ok: true, device }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (action === "poll_queue") {
    pollMarketplaceQueue()
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (action === "complete_active_job") {
    completeActiveJob({ externalListingId: String(message.externalListingId || "").trim(), externalUrl: String(message.externalUrl || "").trim() })
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  return false;
});
