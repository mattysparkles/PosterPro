(() => {
  const extensionRuntime = globalThis.chrome?.runtime || null;
  const VERSION = extensionRuntime?.getManifest?.().version || "unknown";
  const PAGE_SOURCE = "posterpro-settings";
  const EXTENSION_SOURCE = "posterpro-extension";
  // Pairing-code creation is an authenticated network round trip. Keep the
  // trusted-click authorization long enough for that request, while still
  // requiring a recent, real operator gesture.
  const AUTHORIZATION_CLICK_WINDOW_MS = 60000;
  let lastTrustedAuthorizationClick = 0;
  let pairingInProgress = false;

  function sendExtensionMessage(message) {
    const runtime = globalThis.chrome?.runtime;
    if (!runtime || typeof runtime.sendMessage !== "function") {
      const error = new Error("PosterPro extension messaging is unavailable in this tab. Reload the extension at chrome://extensions, then refresh this PosterPro page and try pairing again.");
      error.code = "EXTENSION_RUNTIME_UNAVAILABLE";
      throw error;
    }
    return runtime.sendMessage(message);
  }

  function report(type, details = {}) {
    window.postMessage({ source: EXTENSION_SOURCE, type, version: VERSION, ...details }, location.origin);
  }

  async function reportPresence() {
    try {
      const state = await sendExtensionMessage({ action: "get_connection_status" });
      const device = state?.ok ? state.device : null;
      report("PRESENCE", { device_id: Number.isInteger(device?.id) ? device.id : null, paired: Boolean(device?.id) });
    } catch (error) {
      const message = String(error?.message || error || "");
      if (error?.code === "EXTENSION_RUNTIME_UNAVAILABLE") {
        report("CONTEXT_INVALIDATED", { error: message, error_code: error.code });
        return;
      }
      // Chrome invalidates content-script contexts while an unpacked
      // extension is reloaded. Tell the page exactly what happened so it can
      // refresh once instead of presenting a misleading pairing failure.
      if (/context invalidated|extension context/i.test(message)) {
        report("CONTEXT_INVALIDATED", { error: "The PosterPro extension was reloaded. Refreshing the page will reconnect it." });
      } else {
        report("PRESENCE", { device_id: null, paired: false });
      }
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
    if (data.type === "CHECK_EXTENSION_UPDATE") {
      try {
        const result = await sendExtensionMessage({ action: "check_for_update" });
        report("EXTENSION_UPDATE_RESULT", result || { ok: false, status: "unknown" });
      } catch (error) {
      report("EXTENSION_UPDATE_RESULT", { ok: false, status: "error", error_code: error?.code || "EXTENSION_UPDATE_FAILED", error: String(error?.message || error).slice(0, 240) });
      }
      return;
    }
    if (data.type === "OPEN_MERCARI_SELL") {
      try {
        const result = await sendExtensionMessage({ action: "open_marketplace_tab", marketplace: "mercari" });
        report("MARKETPLACE_TAB_OPENED", { marketplace: "mercari", ...(result || { ok: false }) });
      } catch (error) {
        report("MARKETPLACE_TAB_OPENED", { ok: false, marketplace: "mercari", error_code: error?.code || "MARKETPLACE_TAB_OPEN_FAILED", error: String(error?.message || error).slice(0, 220) });
      }
      return;
    }
    if (data.type !== "PAIR_EXTENSION") return;
    if (pairingInProgress) return;
    if (!lastTrustedAuthorizationClick) {
      report("AUTHORIZATION_FAILED", { error_code: "TRUSTED_CLICK_REQUIRED", error: "Click Pair this browser in PosterPro, then retry." });
      return;
    }
    if (Date.now() - lastTrustedAuthorizationClick > AUTHORIZATION_CLICK_WINDOW_MS) {
      lastTrustedAuthorizationClick = 0;
      report("AUTHORIZATION_FAILED", { error_code: "PAIRING_AUTHORIZATION_EXPIRED", error: "The pairing request took too long. Click Pair this browser again and retry." });
      return;
    }
    const code = String(data.pairing_code || "").trim();
    if (code.length < 16 || code.length > 128) {
      lastTrustedAuthorizationClick = 0;
      report("AUTHORIZATION_FAILED", { error_code: "INVALID_PAIRING_CODE", error: "PosterPro returned an invalid pairing code. Refresh the page and try again." });
      return;
    }
    pairingInProgress = true;
    lastTrustedAuthorizationClick = 0;
    try {
      const response = await sendExtensionMessage({ action: "pair_device", pairingCode: code, deviceName: "PosterPro browser" });
      if (!response?.ok) throw new Error(response?.error || "Browser authorization failed.");
      report("AUTHORIZED", { device: response.device || null });
    } catch (error) {
      report("AUTHORIZATION_FAILED", { error_code: error?.code || "EXTENSION_PAIRING_FAILED", error: String(error?.message || error).slice(0, 240) });
    } finally {
      pairingInProgress = false;
    }
  });

  void reportPresence();
})();
