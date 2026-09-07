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
];

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
        selection: String(window.getSelection?.()?.toString?.() || "").trim(),
        localStorage: Object.fromEntries(Object.entries(window.localStorage || {})),
        sessionStorage: Object.fromEntries(Object.entries(window.sessionStorage || {})),
        bodyText: String(document.body?.innerText || "").slice(0, 5000),
      }),
    }),
    chrome.cookies.getAll({ url: tab.url }),
  ]);

  const snapshot = {
    captured_at: new Date().toISOString(),
    marketplace: inferMarketplace(tab.url),
    tab: {
      id: tab.id,
      title: tab.title || null,
      url: tab.url,
    },
    page: safeJson(pageSnapshot?.[0]?.result) || {},
    cookies: (cookies || []).map((cookie) => ({
      name: cookie.name,
      value: cookie.value,
      domain: cookie.domain,
      path: cookie.path,
      secure: cookie.secure,
      httpOnly: cookie.httpOnly,
      sameSite: cookie.sameSite,
      expirationDate: cookie.expirationDate || null,
    })),
  };
  await chrome.storage.local.set({ posterproLastSessionSnapshot: snapshot });
  return snapshot;
}

async function sendSessionToPosterPro(payload) {
  const settings = await loadSettings();
  const baseUrl = settings.posterproBaseUrl;
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
      chrome.storage.local.get(["posterproLastSessionSnapshot", "posterproLastImportResponse", "posterproLastImportAt"]),
    ]).then(([settings, local]) => {
      sendResponse({ ok: true, settings, local });
    });
    return true;
  }

  return false;
});
