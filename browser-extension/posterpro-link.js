(() => {
  const VERSION = chrome.runtime.getManifest?.().version || "unknown";
  const PAGE_SOURCE = "posterpro-settings";
  const EXTENSION_SOURCE = "posterpro-extension";
  let lastTrustedAuthorizationClick = 0;
  let pairingInProgress = false;

  function report(type, details = {}) {
    window.postMessage({ source: EXTENSION_SOURCE, type, version: VERSION, ...details }, location.origin);
  }

  async function reportPresence() {
    try {
      const state = await chrome.runtime.sendMessage({ action: "get_connection_status" });
      const device = state?.ok ? state.device : null;
      report("PRESENCE", { device_id: Number.isInteger(device?.id) ? device.id : null, paired: Boolean(device?.id) });
    } catch {
      report("PRESENCE", { device_id: null, paired: false });
    }
  }

  // Keep the authenticated PosterPro page aware of a freshly loaded unpacked
  // extension. This avoids requiring a manual “check again” after Chrome
  // reloads the service worker or the user returns from chrome://extensions.
  if (typeof setInterval === "function") setInterval(() => { void reportPresence(); }, 10000);

  document.addEventListener("click", (event) => {
    if (!event.isTrusted) return;
    const button = event.target?.closest?.("[data-posterpro-extension-authorize]");
    if (button) lastTrustedAuthorizationClick = Date.now();
  }, true);

  window.addEventListener("message", async (event) => {
    const data = event.data || {};
    if (event.source !== window || event.origin !== location.origin || data.source !== PAGE_SOURCE) return;
    if (data.type === "CHECK_EXTENSION") {
      void reportPresence();
      return;
    }
    if (data.type !== "PAIR_EXTENSION") return;
    if (pairingInProgress || !lastTrustedAuthorizationClick || Date.now() - lastTrustedAuthorizationClick > 10000) return;
    const code = String(data.pairing_code || "").trim();
    if (code.length < 16 || code.length > 128) return;
    pairingInProgress = true;
    lastTrustedAuthorizationClick = 0;
    try {
      const response = await chrome.runtime.sendMessage({ action: "pair_device", pairingCode: code, deviceName: "PosterPro browser" });
      if (!response?.ok) throw new Error(response?.error || "Browser authorization failed.");
      report("AUTHORIZED", { device: response.device || null });
    } catch (error) {
      report("AUTHORIZATION_FAILED", { error: String(error?.message || error).slice(0, 240) });
    } finally {
      pairingInProgress = false;
    }
  });

  void reportPresence();
})();
