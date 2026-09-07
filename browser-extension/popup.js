const DEFAULT_SETTINGS = {
  posterproBaseUrl: "https://posterpro.sparkleserver.site",
  defaultMarketplace: "mercari",
};

const marketplaceFromHostname = (hostname) => {
  const value = String(hostname || "").toLowerCase();
  if (value.includes("mercari")) return "mercari";
  if (value.includes("facebook")) return "facebook";
  if (value.includes("poshmark")) return "poshmark";
  if (value.includes("etsy")) return "etsy";
  if (value.includes("depop")) return "depop";
  if (value.includes("whatnot")) return "whatnot";
  if (value.includes("vinted")) return "vinted";
  return "mercari";
};

const state = {
  snapshot: null,
  lastImport: null,
};

const el = (id) => document.getElementById(id);

function setStatus(message) {
  el("status").textContent = message;
}

function renderSnapshot(snapshot) {
  state.snapshot = snapshot;
  const tab = snapshot?.tab || {};
  el("siteLabel").textContent = `${snapshot?.marketplace || "unknown"} · ${tab.hostname || tab.url || "unknown site"}`;
  setStatus(JSON.stringify(snapshot, null, 2));
}

async function loadState() {
  const response = await chrome.runtime.sendMessage({ action: "get_state" });
  if (!response?.ok) {
    return;
  }
  const settings = response.settings || {};
  const local = response.local || {};
  el("posterproBaseUrl").value = settings.posterproBaseUrl || DEFAULT_SETTINGS.posterproBaseUrl;
  el("defaultMarketplace").value = settings.defaultMarketplace || DEFAULT_SETTINGS.defaultMarketplace;
  if (local.posterproLastSessionSnapshot) {
    renderSnapshot(local.posterproLastSessionSnapshot);
  }
  if (local.posterproLastImportAt) {
    el("importLabel").textContent = new Date(local.posterproLastImportAt).toLocaleString();
    state.lastImport = local.posterproLastImportResponse || null;
  }
}

async function saveSettings() {
  await chrome.storage.sync.set({
    posterproBaseUrl: el("posterproBaseUrl").value.trim() || DEFAULT_SETTINGS.posterproBaseUrl,
    defaultMarketplace: el("defaultMarketplace").value.trim().toLowerCase() || DEFAULT_SETTINGS.defaultMarketplace,
  });
}

async function captureSession() {
  await saveSettings();
  setStatus("Capturing session from the active marketplace tab...");
  const response = await chrome.runtime.sendMessage({ action: "capture_session" });
  if (!response?.ok) {
    throw new Error(response?.error || "Unable to capture session.");
  }
  renderSnapshot(response.snapshot);
  return response.snapshot;
}

async function sendToPosterPro() {
  await saveSettings();
  const snapshot = state.snapshot || (await captureSession());
  const settings = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  const payload = {
    marketplace: String(el("defaultMarketplace").value || settings.defaultMarketplace || DEFAULT_SETTINGS.defaultMarketplace).toLowerCase(),
    account_key: `${String(el("defaultMarketplace").value || settings.defaultMarketplace || DEFAULT_SETTINGS.defaultMarketplace).toLowerCase()}-main`,
    display_name: `${String(el("defaultMarketplace").value || settings.defaultMarketplace || DEFAULT_SETTINGS.defaultMarketplace).toUpperCase()} browser session`,
    login_handle: snapshot?.page?.hostname || snapshot?.tab?.hostname || "",
    notes: "Captured from PosterPro browser-extension scaffold.",
    workflow_state: "ready",
    import_mode: "browser_assist",
    publish_mode: "browser_assist",
    shipping_scope: "shipping_only",
    renewal_mode: "manual",
    bridge_session_state: "ready",
    session_payload: snapshot,
  };
  setStatus(`Sending ${payload.marketplace} session to PosterPro...`);
  const response = await chrome.runtime.sendMessage({ action: "send_to_posterpro", payload });
  if (!response?.ok) {
    throw new Error(response?.error || "PosterPro rejected the session.");
  }
  state.lastImport = response.result || null;
  el("importLabel").textContent = new Date().toLocaleString();
  setStatus(JSON.stringify(response.result, null, 2));
}

async function copySession() {
  const snapshot = state.snapshot || (await captureSession());
  await navigator.clipboard.writeText(JSON.stringify(snapshot, null, 2));
  setStatus("Session JSON copied to clipboard.");
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadState();

  const currentTab = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = currentTab[0];
  if (tab?.url) {
    el("defaultMarketplace").value = marketplaceFromHostname(new URL(tab.url).hostname);
  }

  el("captureButton").addEventListener("click", async () => {
    try {
      await captureSession();
    } catch (error) {
      setStatus(error.message || String(error));
    }
  });

  el("sendButton").addEventListener("click", async () => {
    try {
      await sendToPosterPro();
    } catch (error) {
      setStatus(error.message || String(error));
    }
  });

  el("copyButton").addEventListener("click", async () => {
    try {
      await copySession();
    } catch (error) {
      setStatus(error.message || String(error));
    }
  });
});
