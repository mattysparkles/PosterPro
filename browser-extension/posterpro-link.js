(() => {
  const VERSION = "0.2.1";
  const PAGE_SOURCE = "posterpro-settings";
  const EXTENSION_SOURCE = "posterpro-extension";
  let lastTrustedAuthorizationClick = 0;
  let pairingInProgress = false;

  function report(type, details = {}) {
    window.postMessage({ source: EXTENSION_SOURCE, type, version: VERSION, ...details }, location.origin);
  }

  document.addEventListener("click", (event) => {
    if (!event.isTrusted) return;
    const button = event.target?.closest?.("[data-posterpro-extension-authorize]");
    if (button) lastTrustedAuthorizationClick = Date.now();
  }, true);

  window.addEventListener("message", async (event) => {
    const data = event.data || {};
    if (event.source !== window || event.origin !== location.origin || data.source !== PAGE_SOURCE) return;
    if (data.type === "CHECK_EXTENSION") {
      report("PRESENCE");
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

  report("PRESENCE");
})();
