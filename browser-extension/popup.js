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
  const updateRequired = Boolean(device?.update_required);
  const paused = Boolean(local.posterproAutomationPaused);
  const heartbeatAt = Date.parse(local.posterproLastSuccessfulHeartbeatAt || device?.last_seen_at || "");
  const errorAt = Date.parse(local.posterproAgentErrorAt || "");
  const staleConnectionError = Boolean(local.posterproAgentError && Number.isFinite(heartbeatAt)
    && Number.isFinite(errorAt) && heartbeatAt > errorAt);
  const agentError = staleConnectionError ? null : local.posterproAgentError;
  el("connectionStatus").textContent = !paired ? "Not connected" : updateRequired ? "Update required" : agentError ? "Connection needs attention" : "Connected · automatic";
  el("deviceLabel").textContent = device ? `${device.name || "Browser"} · ${device.browser || "Chrome"}` : "This browser";
  el("versionLabel").textContent = device?.extension_version || "Not paired";
  el("heartbeatLabel").textContent = device?.last_seen_at ? new Date(device.last_seen_at).toLocaleString() : "Waiting for first heartbeat";
  el("activeJobLabel").textContent = job ? `#${job.id} · ${job.marketplace} · ${job.action} · ${job.status}` : "None";
  const result = job?.fill_result || {};
  el("jobSummary").textContent = job
    ? `${job.status === "AWAITING_OPERATOR_REVIEW" ? "The marketplace form is ready. Review it in PosterPro Jobs; this popup will not submit it." : "PosterPro is processing the job automatically."} ${result.populated_fields?.length ? `Fields prepared: ${result.populated_fields.join(", ")}.` : ""}`
    : updateRequired ? "Reload this extension from the updated PosterPro browser-extension folder, then verify the version is current." : paired ? "Eligible jobs are claimed and handled automatically. Human review appears in PosterPro Jobs." : "Authorize once to connect this browser to your PosterPro account.";
  el("agentError").textContent = agentError || "";
  el("pairingSection").hidden = paired;
  el("openJobButton").hidden = !job;
  el("pauseButton").hidden = !paired;
  el("probeFacebookDescriptionButton").hidden = !paired;
  el("startFacebookTaxonomyButton").hidden = !paired || updateRequired;
  el("stopFacebookTaxonomyButton").hidden = !paired;
  el("pauseButton").textContent = paused ? "Resume automation" : "Pause automation";
  setDiagnostic(agentError ? `${agentError}${local.posterproAgentErrorAt ? ` · ${new Date(local.posterproAgentErrorAt).toLocaleString()}` : ""}` : "No diagnostic events.");
  const probe = local.posterproFacebookDescriptionDomProbe;
  const taxonomy = local.posterproFacebookTaxonomyCollection;
  const binding = local.posterproFacebookTaxonomyCollectorBinding;
  el("facebookTaxonomyStatus").textContent = taxonomy
    ? "Facebook seller-form collection: " + (taxonomy.stage || taxonomy.status) + " · " + taxonomy.status
      + (taxonomy.roots_discovered ? ` · ${taxonomy.roots_discovered} roots / ` : " · ") + (taxonomy.captured_nodes || 0) + " persisted nodes"
      + (taxonomy.checkpoint_revision ? ` · checkpoint r${taxonomy.checkpoint_revision}` : "")
      + ` · extension ${taxonomy.extension_version || "unknown"}`
      + (taxonomy.tab_url_path ? ` · ${taxonomy.tab_url_path}` : "")
      + (taxonomy.error_code ? ` · ${taxonomy.error_code}${taxonomy.error ? `: ${taxonomy.error}` : ""}` : "")
      + (taxonomy.blank_form_evidence?.populated_fields?.length
        ? ` · populated controls: ${taxonomy.blank_form_evidence.populated_fields.map((field) => field.field).filter(Boolean).join(", ")}` : "")
      + (taxonomy.blank_form_evidence?.form_ready === false ? " · Facebook form was not hydrated" : "")
      + (taxonomy.updated_at ? ` · ${new Date(taxonomy.updated_at).toLocaleString()}` : "")
    : "No Facebook seller-form collection has started in this browser.";
  el("pauseFacebookTaxonomyButton").hidden = !paired || !binding || !["RUNNING", "PAUSED"].includes(String(binding.status || "").toUpperCase());
  el("pauseFacebookTaxonomyButton").textContent = String(binding?.status || "").toUpperCase() === "PAUSED" ? "Resume collection" : "Pause collection";
  el("showFacebookCollectorWindowButton").hidden = !paired || !binding || !Number.isInteger(Number(binding.window_id));
  if (binding) {
    const bindingSummary = `Dedicated window ${binding.window_id} · tab ${binding.tab_id} · session ${String(binding.collection_session_id || "").slice(0, 12)} · ${binding.status || "unknown"}`
      + ` · ${binding.categories_discovered || 0} categories · ${binding.verified_leaves || 0} leaves · ${binding.complete_schemas || 0} schemas · checkpoint r${binding.checkpoint_revision || 0}`
      + (binding.last_successful_operation ? ` · last: ${binding.last_successful_operation}` : "")
      + (binding.last_error_code ? ` · error: ${binding.last_error_code}` : "");
    el("facebookTaxonomyStatus").textContent = `${el("facebookTaxonomyStatus").textContent} · ${bindingSummary}`;
  }
  if (probe) {
    el("facebookDescriptionProbe").hidden = false;
    el("facebookDescriptionProbe").textContent = JSON.stringify(probe, null, 2);
    el("copyFacebookDescriptionProbeButton").hidden = !probe.ok;
  }
}

async function loadState() {
  const response = await request({ action: "get_state" });
  if (response?.ok) render(response.local || {});
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadState();
  chrome.storage.onChanged?.addListener((changes, area) => {
    if (area === "local" && (changes.posterproFacebookTaxonomyCollection || changes.posterproFacebookTaxonomyError
      || changes.posterproFacebookTaxonomyCollectorBinding || changes.posterproAgentError || changes.posterproDevice || changes.posterproLastSuccessfulHeartbeatAt)) {
      void loadState();
    }
  });
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
  el("startFacebookTaxonomyButton").addEventListener("click", async () => {
    el("startFacebookTaxonomyButton").disabled = true;
    el("facebookTaxonomyStatus").textContent = "STARTING · creating or resuming the dedicated non-focused Facebook collector window…";
    const result = await request({ action: "posterpro_start_facebook_taxonomy_collection", resume: true });
    await loadState();
    if (result?.ok && result.status === "RUNNING" && Number(result.roots_discovered) > 0 && Number(result.captured_nodes) > 0 && Number(result.checkpoint_revision) > 0) {
      el("facebookTaxonomyStatus").textContent = `RUNNING · ${result.roots_discovered} root categories and ${result.captured_nodes} category observations persisted (checkpoint r${result.checkpoint_revision}).`;
    } else {
      const stage = result?.stage || "STARTING";
      const code = result?.error_code || "STARTUP_RESULT_NOT_CONFIRMED";
      const detail = result?.error || result?.message || "No root observation and durable checkpoint were confirmed.";
      el("facebookTaxonomyStatus").textContent = `${stage} · ${code}: ${detail}`;
    }
    el("startFacebookTaxonomyButton").disabled = false;
  });
  el("stopFacebookTaxonomyButton").addEventListener("click", async () => {
    const result = await request({ action: "posterpro_stop_facebook_taxonomy_collection" });
    el("facebookTaxonomyStatus").textContent = result?.ok
      ? "Stop requested; the current bounded interaction will checkpoint before stopping."
      : result?.error_code || "No running collection.";
    await loadState();
  });
  el("pauseFacebookTaxonomyButton").addEventListener("click", async () => {
    const isPaused = String(agentState?.posterproFacebookTaxonomyCollectorBinding?.status || "").toUpperCase() === "PAUSED";
    const result = isPaused
      ? await request({ action: "posterpro_start_facebook_taxonomy_collection", resume: true })
      : await request({ action: "posterpro_pause_facebook_taxonomy_collection", paused: true });
    el("facebookTaxonomyStatus").textContent = result?.ok
      ? (isPaused ? "RESUMED · continuing from the saved checkpoint." : "PAUSE REQUESTED · collector will stop at the next checkpoint.")
      : `PAUSE FAILED · ${result?.error_code || "UNKNOWN"}`;
    await loadState();
  });
  el("showFacebookCollectorWindowButton").addEventListener("click", async () => {
    const result = await request({ action: "posterpro_show_facebook_collector_window" });
    if (!result?.ok) el("facebookTaxonomyStatus").textContent = `SHOW WINDOW FAILED · ${result?.error_code || "UNKNOWN"}`;
    await loadState();
  });
  el("probeFacebookDescriptionButton").addEventListener("click", async () => {
    el("probeFacebookDescriptionButton").disabled = true;
    el("facebookDescriptionProbe").hidden = false;
    el("facebookDescriptionProbe").textContent = "Inspecting the active Facebook Marketplace CREATE page (read-only)…";
    const result = await request({ action: "probe_active_facebook_description_dom" });
    const output = JSON.stringify(result, null, 2);
    el("facebookDescriptionProbe").textContent = output;
    el("copyFacebookDescriptionProbeButton").hidden = !result?.ok;
    el("copyFacebookDescriptionProbeButton").onclick = async () => {
      try { await navigator.clipboard.writeText(output); el("copyFacebookDescriptionProbeButton").textContent = "Evidence copied"; }
      catch (_) { el("copyFacebookDescriptionProbeButton").textContent = "Copy unavailable"; }
    };
    if (!result?.ok) el("agentError").textContent = result?.error || result?.error_code || "Probe failed.";
    el("probeFacebookDescriptionButton").disabled = false;
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
