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
  if (value.includes("offerup")) return "offerup";
  return "mercari";
};

const state = {
  snapshot: null,
  lastImport: null,
  agent: null,
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

function renderAgent(local = {}) {
  state.agent = local;
  const device = local.posterproDevice || null;
  const job = local.posterproActiveJob || null;
  el("deviceLabel").textContent = device ? `${device.name} · paired` : (local.posterproDeviceToken ? "Paired" : "Not paired");
  el("activeJobLabel").textContent = job ? `#${job.id} · ${job.marketplace} · ${job.action} · ${job.status || "active"}` : "None";
  const result = job?.fill_result || {};
  el("jobFields").textContent = job
    ? `Filled: ${(result.populated_fields || []).join(", ") || "none"}. Review required: ${(result.missing_required_fields || []).join(", ") || "confirm details and submit manually"}. Automated submission is disabled.`
    : "The extension checks the tenant-scoped queue while paired.";
  el("agentError").textContent = local.posterproAgentError ? `Connection/job error: ${local.posterproAgentError}` : "";
  el("completeButton").disabled = !job || job.status !== "AWAITING_OPERATOR_REVIEW";
  const isEnd = String(job?.action || "").toUpperCase() === "END";
  el("externalListingId").value = job?.external_listing_id || job?.payload?.external_listing_id || "";
  el("externalUrl").value = job?.external_url || job?.payload?.external_url || "";
  el("externalListingId").placeholder = isEnd ? "Existing external listing ID" : "Enter after you submit";
  el("externalUrl").placeholder = isEnd ? "Existing marketplace listing URL" : "https://…";
  el("completeButton").textContent = isEnd ? "I ended it — record result" : "I submitted it — record result";
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
  renderAgent(local);
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
    notes: "Connection metadata only. Marketplace cookies and storage credentials are not exported; pair the browser extension for assisted jobs.",
    workflow_state: "draft",
    import_mode: "manual",
    publish_mode: "manual_review",
    shipping_scope: "shipping_only",
    renewal_mode: "manual",
    bridge_session_state: "draft",
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

  el("pairButton").addEventListener("click", async () => {
    setStatus("Pairing this browser with PosterPro…");
    const response = await chrome.runtime.sendMessage({ action: "pair_device", pairingCode: el("pairingCode").value, deviceName: "PosterPro browser" });
    if (!response?.ok) {
      el("agentError").textContent = response?.error || "Pairing failed.";
      setStatus("Pairing failed.");
      return;
    }
    el("pairingCode").value = "";
    setStatus(`Paired as ${response.device?.name || "PosterPro browser"}.`);
    await loadState();
  });

  el("pollButton").addEventListener("click", async () => {
    setStatus("Checking the PosterPro marketplace queue…");
    const response = await chrome.runtime.sendMessage({ action: "poll_queue" });
    if (!response?.ok) el("agentError").textContent = response?.error || "Queue check failed.";
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await loadState();
    setStatus(response?.ok ? "Queue checked. Any eligible job is processed to operator review only." : "Queue check failed.");
  });

  el("completeButton").addEventListener("click", async () => {
    const activeJob = state.agent?.posterproActiveJob;
    const isEnd = String(activeJob?.action || "").toUpperCase() === "END";
    if (!window.confirm(isEnd ? "Confirm that you ended this exact listing on the marketplace? This records the result in PosterPro." : "Confirm that you already submitted this listing on the marketplace? This only records the result in PosterPro.")) return;
    const response = await chrome.runtime.sendMessage({
      action: "complete_active_job",
      externalListingId: el("externalListingId").value,
      externalUrl: el("externalUrl").value,
    });
    if (!response?.ok) {
      el("agentError").textContent = response?.error || "Could not record the marketplace result.";
      return;
    }
    el("externalListingId").value = "";
    el("externalUrl").value = "";
    el("agentError").textContent = "Marketplace result recorded.";
    await loadState();
  });
});
