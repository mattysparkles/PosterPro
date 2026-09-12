const el = (id) => document.getElementById(id);
let agentState = null;

function request(message) {
  return new Promise((resolve) => chrome.runtime.sendMessage(message, resolve));
}

function setDiagnostic(value) {
  el("diagnostics").textContent = String(value || "No diagnostic events.");
}

async function openPosterPro(path = "/jobs") {
  const settings = await chrome.storage.sync.get({ posterproBaseUrl: "https://posterpro.sparkleserver.site" });
  const base = String(settings.posterproBaseUrl || "https://posterpro.sparkleserver.site").replace(/\/+$/, "");
  await chrome.tabs.create({ url: `${base}${path}` });
}

function render(local = {}) {
  agentState = local;
  const device = local.posterproDevice;
  const job = local.posterproActiveJob;
  const paired = Boolean(device && local.posterproDeviceToken);
  const paused = Boolean(local.posterproAutomationPaused);
  el("connectionStatus").textContent = !paired ? "Not connected" : local.posterproAgentError ? "Connection needs attention" : "Connected · automatic";
  el("deviceLabel").textContent = device ? `${device.name || "Browser"} · ${device.browser || "Chrome"}` : "This browser";
  el("versionLabel").textContent = device?.extension_version || "Not paired";
  el("heartbeatLabel").textContent = device?.last_seen_at ? new Date(device.last_seen_at).toLocaleString() : "Waiting for first heartbeat";
  el("activeJobLabel").textContent = job ? `#${job.id} · ${job.marketplace} · ${job.action} · ${job.status}` : "None";
  const result = job?.fill_result || {};
  el("jobSummary").textContent = job
    ? `${job.status === "AWAITING_OPERATOR_REVIEW" ? "The marketplace form is ready. Review it in PosterPro Jobs; this popup will not submit it." : "PosterPro is processing the job automatically."} ${result.populated_fields?.length ? `Fields prepared: ${result.populated_fields.join(", ")}.` : ""}`
    : paired ? "Eligible jobs are claimed and handled automatically. Human review appears in PosterPro Jobs." : "Authorize once to connect this browser to your PosterPro account.";
  el("agentError").textContent = local.posterproAgentError || "";
  el("pairingSection").hidden = paired;
  el("openJobButton").hidden = !job;
  el("pauseButton").hidden = !paired;
  el("pauseButton").textContent = paused ? "Resume automation" : "Pause automation";
  setDiagnostic(local.posterproAgentError ? `${local.posterproAgentError}${local.posterproAgentErrorAt ? ` · ${new Date(local.posterproAgentErrorAt).toLocaleString()}` : ""}` : "No diagnostic events.");
}

async function loadState() {
  const response = await request({ action: "get_state" });
  if (response?.ok) render(response.local || {});
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadState();
  el("openPosterProButton").addEventListener("click", () => void openPosterPro("/jobs"));
  el("openJobButton").addEventListener("click", () => {
    const job = agentState?.posterproActiveJob;
    if (job?.id) void openPosterPro(`/jobs?tab=assisted&type=assisted&jobId=${encodeURIComponent(job.id)}`);
  });
  el("retryButton").addEventListener("click", async () => {
    const response = await request({ action: "poll_queue" });
    if (!response?.ok) el("agentError").textContent = response?.error || "Reconnect failed.";
    await loadState();
  });
  el("pauseButton").addEventListener("click", async () => {
    const paused = !agentState?.posterproAutomationPaused;
    const response = await request({ action: "set_automation_paused", paused });
    if (!response?.ok) el("agentError").textContent = response?.error || "Could not update automation state.";
    await loadState();
  });
  el("pairButton").addEventListener("click", async () => {
    const result = await request({ action: "pair_device", pairingCode: el("pairingCode").value.trim(), deviceName: "PosterPro browser" });
    if (!result?.ok) {
      el("agentError").textContent = result?.error || "Authorization failed.";
      return;
    }
    el("pairingCode").value = "";
    await loadState();
  });
});
