const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadBackgroundHarness(options = {}) {
  const state = {
    ...(options.deviceToken ? { posterproDeviceToken: options.deviceToken } : {}),
    ...(options.storedTabId === null ? {} : { posterproAutomationTabId: options.storedTabId ?? 17 }),
    ...(options.activeJob ? { posterproActiveJob: { ...options.activeJob } } : {}),
  };
  const listeners = [];
  let updateAttempts = 0;
  let reloadAttempts = 0;
  let identityCalls = 0;
  let currentTab = { id: 17, url: "https://www.mercari.com/previous", status: "complete", ...(options.currentTab || {}) };
  const chrome = {
    runtime: {
      id: "posterpro-extension-test",
      getManifest: () => ({ version: "0.3.70" }),
      onInstalled: { addListener: (listener) => listeners.push(listener) },
      onStartup: { addListener: (listener) => listeners.push(listener) },
      onMessage: { addListener: (listener) => listeners.push(listener) },
    },
    alarms: { create() {}, onAlarm: { addListener: (listener) => listeners.push(listener) } },
    storage: {
      local: {
        async get(keys) { return Object.fromEntries(keys.map((key) => [key, state[key]])); },
        async set(values) { Object.assign(state, values); },
        async remove(key) { delete state[key]; },
      },
      sync: { async get(defaults) { return defaults; }, async set() {} },
    },
    tabs: {
      async get(id) { if (id !== currentTab.id) throw new Error(`No tab with id: ${id}`); return { ...currentTab }; },
      async query() { return [{ ...currentTab, active: true }]; },
      async update(id, change) {
        updateAttempts += 1;
        if (updateAttempts < 3) throw new Error("Tabs cannot be edited right now (user may be dragging a tab).");
        currentTab = { ...currentTab, ...change };
        return { ...currentTab };
      },
      async create({ url }) {
        if (!options.allowCreate) throw new Error("Unexpected replacement tab creation");
        const id = Number(currentTab.id || 17) + 1;
        currentTab = { id, url, status: "complete", ...(options.currentTab || {}), id };
        return { id, url: undefined, status: "loading" };
      },
      async reload(id) { if (id !== currentTab.id) throw new Error(`No tab with id: ${id}`); reloadAttempts += 1; options.onReload?.(reloadAttempts); },
      async remove(id) { options.onTabRemove?.(id); },
      onUpdated: { addListener: (listener) => listeners.push(listener), removeListener() {} },
      async sendMessage(id, message) {
        if (options.onTabMessage) return options.onTabMessage(id, message);
        return options.identityProvider ? options.identityProvider(identityCalls++) : options.identity || {
        extension_version: "0.3.70",
        content_script_version: "0.3.70",
        content_script_build_id: "posterpro-0.3.70-facebook-background-queue-focus-safety",
        content_script_injected_at: "2026-10-02T10:00:00.000Z",
        page_loaded_at: "2026-10-02T10:00:00.000Z",
      }; },
    },
    debugger: {
      async getTargets() { return options.debuggerTargets || []; },
      async attach(target, version) { options.onDebuggerAttach?.(target, version); },
      async sendCommand(target, method, params) {
        options.onDebuggerCommand?.(target, method, params);
        if (options.failDebuggerCommand === params.type) throw new Error(`CDP ${params.type} failed`);
      },
      async detach(target) { options.onDebuggerDetach?.(target); },
    },
    scripting: { async executeScript() { return []; } },
  };
  const source = `${fs.readFileSync(path.join(__dirname, "..", "background.js"), "utf8")}\nglobalThis.__backgroundTest = { openAutomationTab, rememberAutomationTab, facebookTabHygiene, encodeFacebookContextProbe, makeExecutionDiagnostic, prepareDiagnosticContent, waitForTabComplete, requireMarketplaceFormReady, activateValidatedMarketplaceControl, handleMarketplaceControlRequest, probeActiveFacebookDescriptionDom, runFacebookReconciliationTask, reconcileLostFacebookSubmit, validateFacebookSubmitIdentity, isExpiredPreSubmitFacebookCreate, renewRunningMarketplaceJobLease, pollMarketplaceQueue, sendFacebookFormFillWithWatchdog, downloadCanonicalImages, setFacebookFormProgress, activeFacebookFormProgress, setQueuePollActive: (value) => { queuePollActive = value; } };`;
  const context = { chrome, URL, Date, Promise, setTimeout, clearTimeout, AbortController, console, crypto: { randomUUID: () => "test-uuid" }, fetch: options.fetch || (async () => { throw new Error("Unexpected network access"); }) };
  context.globalThis = context;
  vm.runInNewContext(source, context);
  return { api: context.__backgroundTest, state, get updateAttempts() { return updateAttempts; }, get reloadAttempts() { return reloadAttempts; } };
}

test("failed listing form preserves exact adapter evidence for the durable job result", () => {
  const harness = loadBackgroundHarness();
  const result = { ok: false, marketplace: "facebook", page_state: "FORM_CHANGED", error_code: "MARKETPLACE_FORM_CHANGED", field_results: [{ field: "title", filled: false }] };
  assert.throws(() => harness.api.requireMarketplaceFormReady(result, "facebook"), (error) => {
    assert.equal(error.code, "MARKETPLACE_FORM_CHANGED");
    assert.deepEqual(JSON.parse(JSON.stringify(error.result)), result);
    return true;
  });
});

test("only an expired identity-free Facebook FORM_FILLING CREATE is eligible for same-job recovery", () => {
  const harness = loadBackgroundHarness();
  const active = { id: 75, marketplace: "facebook", action: "CREATE", status: "FORM_FILLING" };
  const remote = { id: 75, marketplace: "facebook", action: "CREATE", status: "FORM_FILLING",
    lease_expires_at: "2026-10-05T15:40:00.000Z", external_listing_id: null, external_url: null, result: null };
  const now = Date.parse("2026-10-05T15:41:00.000Z");
  assert.equal(harness.api.isExpiredPreSubmitFacebookCreate(active, remote, now), true);
  assert.equal(harness.api.isExpiredPreSubmitFacebookCreate(active, { ...remote, status: "SUBMITTING" }, now), false);
  assert.equal(harness.api.isExpiredPreSubmitFacebookCreate(active, { ...remote, external_listing_id: "123" }, now), false);
  assert.equal(harness.api.isExpiredPreSubmitFacebookCreate(active, { ...remote, result: { submission_performed: true } }, now), false);
  assert.equal(harness.api.isExpiredPreSubmitFacebookCreate(active, { ...remote, lease_expires_at: "2026-10-05T15:42:00.000Z" }, now), false);
  assert.equal(harness.api.isExpiredPreSubmitFacebookCreate({ ...active, id: 76 }, remote, now), false);
});

test("queue alarm renews active Facebook job lease while the one queue execution is still running", async () => {
  const requests = [];
  const harness = loadBackgroundHarness({
    deviceToken: "paired-token",
    activeJob: { id: 75, marketplace: "facebook", action: "CREATE", status: "FORM_FILLING" },
    fetch: async (url, request) => {
      requests.push({ url: String(url), method: request.method });
      return { ok: true, async text() { return JSON.stringify({ ok: true }); } };
    },
  });
  harness.api.setQueuePollActive(true);
  await harness.api.pollMarketplaceQueue();
  assert.equal(requests.some((item) => item.url.endsWith("/browser-extension/heartbeat")), true);
  assert.equal(requests.some((item) => item.url.endsWith("/browser-extension/jobs/75/lease")), true);
  assert.equal(requests.some((item) => item.url.endsWith("/browser-extension/jobs/claim")), false);
});

test("read-only Facebook description probe is scoped to the active CREATE tab and stores returned evidence", async () => {
  const probe = { description_labels: [{ text: "Description" }], candidates: [{ tag_name: "div", is_contenteditable: true }] };
  const harness = loadBackgroundHarness({
    currentTab: { id: 17, url: "https://www.facebook.com/marketplace/create/item", status: "complete" },
    onTabMessage(_tabId, message) {
      if (message.action === "posterpro_content_script_identity") return { extension_version: "0.3.70", content_script_version: "0.3.70", content_script_build_id: "posterpro-0.3.70-facebook-background-queue-focus-safety" };
      if (message.action === "posterpro_probe_facebook_description_dom") return { ok: true, read_only: true, content_script_build_id: "posterpro-0.3.70-facebook-background-queue-focus-safety", probe };
      throw new Error(`Unexpected content message ${message.action}`);
    },
  });
  const result = await harness.api.probeActiveFacebookDescriptionDom();
  assert.equal(result.ok, true);
  assert.equal(result.read_only, true);
  assert.deepEqual(JSON.parse(JSON.stringify(result.probe)), probe);
  assert.equal(harness.state.posterproFacebookDescriptionDomProbe.ok, true);
});

test("read-only Facebook description probe refuses a non-Facebook or non-CREATE tab", async () => {
  const harness = loadBackgroundHarness({ currentTab: { id: 17, url: "https://www.facebook.com/marketplace/item/123/", status: "complete" } });
  const result = await harness.api.probeActiveFacebookDescriptionDom();
  assert.equal(result.ok, false);
  assert.equal(result.error_code, "FACEBOOK_DESCRIPTION_PROBE_WRONG_PAGE");
});

test("automation tab edit retries Chrome's transient tab-drag error without creating a duplicate tab", async () => {
  const harness = loadBackgroundHarness();
  const trace = harness.api.makeExecutionDiagnostic();
  const tab = await harness.api.openAutomationTab("https://www.mercari.com/sell/", trace);
  assert.equal(harness.updateAttempts, 3);
  assert.equal(tab.id, 17);
  assert.equal(harness.state.posterproAutomationTabId, 17);
  assert.equal(trace.events.some((event) => event.stage === "AUTOMATION_TAB_UPDATE" && event.status === "succeeded"), true);
  assert.equal(trace.error_code, undefined);
});

test("fresh Facebook CREATE context never reuses matching URL and preserves the previous tab until baseline validation", async () => {
  const removed = [];
  const harness = loadBackgroundHarness({
    allowCreate: true,
    currentTab: { id: 17, url: "https://www.facebook.com/marketplace/create/item", status: "complete" },
    onTabRemove: (id) => removed.push(id),
  });
  const trace = harness.api.makeExecutionDiagnostic();
  const fresh = await harness.api.openAutomationTab("https://www.facebook.com/marketplace/create/item", trace, { fresh: true });
  assert.equal(fresh.id, 18);
  assert.equal(fresh.previous_automation_tab_id, 17);
  assert.equal(harness.state.posterproAutomationTabId, 18);
  assert.deepEqual(removed, []);
});

test("diagnostic validates fresh chrome.tabs.get URL instead of the stale tab returned by create", async () => {
  const harness = loadBackgroundHarness({ storedTabId: null, allowCreate: true, currentTab: { id: 17, url: "https://www.mercari.com/sell/", status: "complete" } });
  const trace = harness.api.makeExecutionDiagnostic();
  const staleCreatedTab = await harness.api.openAutomationTab("https://www.mercari.com/sell/", trace);
  assert.equal(staleCreatedTab.url, undefined);
  await harness.api.waitForTabComplete(staleCreatedTab.id, 1000);
  const identity = await harness.api.prepareDiagnosticContent(staleCreatedTab, trace, "https://www.mercari.com/sell/");
  assert.equal(identity.content_script_version, "0.3.70");
  assert.equal(trace.last_successful_stage, "IDENTITY_VERIFIED");
  assert.equal(trace.events.some((event) => event.stage === "VALIDATE_TARGET_URL" && event.status === "succeeded"), true);
  assert.notEqual(trace.error_message, "Failed to construct 'URL': Invalid URL");
});

test("stale marketplace content identity forces one reload and current-build reinjection before proceeding", async () => {
  const staleIdentity = {
    extension_version: "0.3.29", content_script_version: "0.3.29",
    content_script_build_id: "posterpro-0.3.29-job1043-live-delivery-picker-and-category-depth",
  };
  const currentIdentity = {
    extension_version: "0.3.70", content_script_version: "0.3.70",
    content_script_build_id: "posterpro-0.3.70-facebook-background-queue-focus-safety",
    content_script_injected_at: "2026-10-03T14:00:00.000Z", page_loaded_at: "2026-10-03T14:00:00.000Z",
  };
  let pageIdentity = staleIdentity;
  const harness = loadBackgroundHarness({
    identityProvider: () => pageIdentity,
    onReload: () => { pageIdentity = currentIdentity; },
  });
  const trace = harness.api.makeExecutionDiagnostic();
  const identity = await harness.api.prepareDiagnosticContent({ id: 17 }, trace, "https://www.mercari.com/sell/");
  assert.equal(harness.reloadAttempts, 1);
  assert.equal(identity.content_script_version, "0.3.70");
  assert.equal(trace.stale_content_script.page_content_script_version, "0.3.29");
  assert.equal(trace.last_successful_stage, "IDENTITY_VERIFIED");
  assert.equal(trace.events.some((event) => event.stage === "STALE_CONTENT_SCRIPT_RELOAD" && event.status === "succeeded"), true);
});

test("Facebook CREATE identity is accepted only with title-price-bound exact item evidence", () => {
  const harness = loadBackgroundHarness();
  const payload = { title: "Exact item", price: 69.99 };
  const trusted = harness.api.validateFacebookSubmitIdentity({
    identity_evidence: "NEW_POST_SUBMIT_ITEM_LINK_EXACT_TITLE_PRICE",
    external_listing_id: "12345", external_url: "https://www.facebook.com/marketplace/item/12345/",
    observed_title: "Exact item", observed_price: "70",
  }, payload);
  assert.equal(trusted.external_listing_id, "12345");
  assert.equal(harness.api.validateFacebookSubmitIdentity({
    external_listing_id: "old-id", external_url: "https://www.facebook.com/marketplace/item/old-id/",
    observed_title: "Exact item", observed_price: "70",
  }, payload), null, "an unbound first Marketplace link is never trusted");
  assert.equal(harness.api.validateFacebookSubmitIdentity({
    identity_evidence: "DIRECT_ITEM_PAGE_EXACT_TITLE_PRICE",
    external_listing_id: "12345", external_url: "https://www.facebook.com/marketplace/item/12345/",
    observed_title: "Different item", observed_price: "70",
  }, payload), null);
  assert.equal(harness.api.validateFacebookSubmitIdentity({
    identity_evidence: "DIRECT_ITEM_PAGE_EXACT_TITLE_PRICE",
    external_listing_id: "12345", external_url: "https://www.facebook.com/marketplace/item/12345/",
    observed_title: "Exact item", observed_price: "68.99",
  }, payload), null);
  assert.equal(harness.api.validateFacebookSubmitIdentity({
    identity_evidence: "DIRECT_ITEM_PAGE_EXACT_TITLE_PRICE",
    external_listing_id: "other", external_url: "https://www.facebook.com/marketplace/item/12345/",
    observed_title: "Exact item", observed_price: "70",
  }, payload), null);
});

test("diagnostic distinguishes an absent current tab URL from a malformed URL", async (t) => {
  const requestedUrl = "https://www.mercari.com/sell/";
  for (const fixture of [
    { name: "missing current and pending URL", currentTab: { id: 17, status: "complete", url: undefined, pendingUrl: undefined }, expected: "AUTOMATION_TAB_URL_UNAVAILABLE" },
    { name: "malformed current URL", currentTab: { id: 17, status: "complete", url: "not a URL" }, expected: "AUTOMATION_TAB_URL_INVALID" },
  ]) {
    await t.test(fixture.name, async () => {
      const harness = loadBackgroundHarness({ currentTab: fixture.currentTab });
      const trace = harness.api.makeExecutionDiagnostic();
      await assert.rejects(
        harness.api.prepareDiagnosticContent({ id: 17, url: undefined, status: "loading" }, trace, requestedUrl),
        (error) => error.code === fixture.expected && error.executionDiagnostic?.stage === "VALIDATE_TARGET_URL",
      );
    });
  }
});

test("reconciliation-only task reads authenticated seller state and records the exact match without a submit action", async () => {
  const calls = [];
  const identity = {
    extension_version: "0.3.70", content_script_version: "0.3.70",
    content_script_build_id: "posterpro-0.3.70-facebook-background-queue-focus-safety",
  };
  const harness = loadBackgroundHarness({
    deviceToken: "paired-token",
    currentTab: { id: 17, url: "https://www.facebook.com/marketplace/you/selling/", status: "complete" },
    onTabMessage: async (_id, message) => {
      calls.push(message.action);
      if (message.action === "posterpro_content_script_identity") return identity;
      if (message.action === "posterpro_reconcile_facebook_seller_listing") return {
        state: "FOUND", source: "authenticated_facebook_seller_listings", page_url: "https://www.facebook.com/marketplace/you/selling/",
        external_listing_id: "99887766", external_url: "https://www.facebook.com/marketplace/item/99887766/",
        exact_title_match: true, observed_title: "Exact listing", exact_price_match: true, observed_price: "29.99",
      };
      if (message.action === "posterpro_submit_operator_authorized_listing") throw new Error("CREATE must never run during reconciliation");
      throw new Error(`Unexpected content action: ${message.action}`);
    },
    fetch: async (_url, request) => {
      const body = JSON.parse(request.body);
      calls.push("POST_RECONCILIATION_RESULT");
      assert.equal(body.state, "FOUND");
      assert.equal(body.external_listing_id, "99887766");
      return { ok: true, async text() { return JSON.stringify({ job_id: 41, state: "FOUND", status: "COMPLETED" }); } };
    },
  });
  const result = await harness.api.runFacebookReconciliationTask({
    id: 41, marketplace: "facebook", action: "CREATE", known_remote_presence: false,
    payload: { marketplace_payload: { title: "Exact listing", price: 29.99 } },
  });
  assert.equal(result.status, "COMPLETED");
  assert.equal(calls.includes("posterpro_submit_operator_authorized_listing"), false);
  assert.deepEqual(calls, ["posterpro_content_script_identity", "posterpro_reconcile_facebook_seller_listing", "POST_RECONCILIATION_RESULT"]);
});

test("lost post-Publish response performs read-only reconciliation and never sends a second Publish", async () => {
  const actions = [];
  const submittedStates = [];
  const identity = {
    extension_version: "0.3.70", content_script_version: "0.3.70",
    content_script_build_id: "posterpro-0.3.70-facebook-background-queue-focus-safety",
  };
  const harness = loadBackgroundHarness({
    deviceToken: "paired-token",
    currentTab: { id: 17, url: "https://www.facebook.com/marketplace/you/selling/", status: "complete" },
    onTabMessage: async (_id, message) => {
      actions.push(message.action);
      if (message.action === "posterpro_content_script_identity") return identity;
      if (message.action === "posterpro_reconcile_facebook_seller_listing") return {
        state: "FOUND", source: "authenticated_facebook_seller_listings", page_url: "https://www.facebook.com/marketplace/you/selling/",
        external_listing_id: "99887766", external_url: "https://www.facebook.com/marketplace/item/99887766/",
        exact_title_match: true, observed_title: "Exact listing", exact_price_match: true, observed_price: "29.99",
      };
      if (message.action === "posterpro_submit_operator_authorized_listing") throw new Error("A lost response must not replay Publish");
      throw new Error(`Unexpected content action: ${message.action}`);
    },
    fetch: async (_url, request) => {
      const body = JSON.parse(request.body);
      submittedStates.push(body.status);
      assert.equal(body.external_listing_id, "99887766");
      actions.push(`state:${body.status}`);
      return { ok: true, async text() { return JSON.stringify({ id: 52, status: body.status, external_listing_id: "99887766" }); } };
    },
  });
  const result = await harness.api.reconcileLostFacebookSubmit(
    { id: 52 }, 17, { title: "Exact listing", price: 29.99 }, new Error("The message port closed after Publish"),
  );
  assert.equal(result.status, "SUBMITTED_PENDING_REVIEW");
  assert.deepEqual(submittedStates, ["SUBMITTED", "SUBMITTED_PENDING_REVIEW"]);
  assert.deepEqual(actions, ["posterpro_content_script_identity", "posterpro_reconcile_facebook_seller_listing", "state:SUBMITTED", "state:SUBMITTED_PENDING_REVIEW"]);
  assert.equal(actions.filter((action) => action === "posterpro_submit_operator_authorized_listing").length, 0);
});

test("Facebook form watchdog cancels a stalled category stage and marks only settled pre-submit work retryable", async () => {
  let finishFill;
  const harness = loadBackgroundHarness({
    onTabMessage: async (_id, message) => {
      if (message.action === "posterpro_fill_listing") return new Promise((resolve) => { finishFill = resolve; });
      if (message.action === "posterpro_cancel_facebook_form_fill") {
        finishFill?.({ ok: false, submission_performed: false, error_code: "FACEBOOK_FORM_FILL_CANCELLED" });
        return { cancelled: true };
      }
      throw new Error(`Unexpected tab action ${message.action}`);
    },
  });
  const now = Date.now();
  harness.api.activeFacebookFormProgress.set(76, { startedAt: now, lastProgressAt: now, stage: "CATEGORY_RESOLUTION_STARTED", tabId: 17 });
  await assert.rejects(
    harness.api.sendFacebookFormFillWithWatchdog(17, 76, {}, [], { noProgressMs: 25, maxDurationMs: 200, cancelDrainMs: 25 }),
    (error) => error.code === "FACEBOOK_CATEGORY_RESOLUTION_TIMEOUT" && error.safe_pre_submit_retry === true && error.last_progress_stage === "CATEGORY_RESOLUTION_STARTED",
  );
});

test("Facebook form watchdog resets its no-progress interval when progress advances", async () => {
  const harness = loadBackgroundHarness({
    onTabMessage: async (_id, message) => {
      if (message.action === "posterpro_fill_listing") return new Promise((resolve) => setTimeout(() => resolve({ ok: true }), 75));
      throw new Error(`Unexpected cancellation ${message.action}`);
    },
  });
  const progress = { startedAt: Date.now(), lastProgressAt: Date.now(), stage: "TITLE_SET", tabId: 17 };
  harness.api.activeFacebookFormProgress.set(77, progress);
  setTimeout(() => { progress.stage = "PRICE_SET"; progress.lastProgressAt = Date.now(); }, 20);
  setTimeout(() => { progress.stage = "CATEGORY_SET"; progress.lastProgressAt = Date.now(); }, 48);
  const result = await harness.api.sendFacebookFormFillWithWatchdog(17, 77, {}, [], { noProgressMs: 35, maxDurationMs: 200, cancelDrainMs: 20 });
  assert.deepEqual(result, { ok: true });
  assert.equal(progress.stage, "CATEGORY_SET");
});

test("Facebook image downloader aborts an individual image fetch with an explicit pre-submit code", async () => {
  const harness = loadBackgroundHarness({
    fetch: async (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })), { once: true });
    }),
  });
  await assert.rejects(
    harness.api.downloadCanonicalImages({ image_urls: ["https://media.example/item.jpg"] }, "facebook", null, { perImageTimeoutMs: 5 }),
    (error) => error.code === "FACEBOOK_IMAGE_DOWNLOAD_TIMEOUT" && error.safe_pre_submit_retry === true,
  );
});

test("Facebook progress persistence posts page_url from the safe tab URL and advances watchdog only after backend acknowledgement", async () => {
  let posted = null;
  const harness = loadBackgroundHarness({
    deviceToken: "paired-token",
    currentTab: { id: 17, url: "https://www.facebook.com/marketplace/create/item?secret=ignored", status: "complete" },
    fetch: async (url, request) => {
      posted = { url: String(url), body: JSON.parse(request.body) };
      return { ok: true, async text() { return JSON.stringify({ ok: true, last_progress_stage: "CLEAN_FORM_VERIFIED" }); } };
    },
  });
  const result = await harness.api.setFacebookFormProgress(91, 17, "CLEAN_FORM_VERIFIED", "completed");
  assert.equal(result.ok, true);
  assert.equal(posted.body.page_url, "https://www.facebook.com/marketplace/create/item");
  assert.equal(posted.body.content_build_id, "posterpro-0.3.70-facebook-background-queue-focus-safety");
  assert.equal(posted.body.stage, "CLEAN_FORM_VERIFIED");
  assert.equal(harness.api.activeFacebookFormProgress.get(91).lastProgressAt > 0, true);
});

test("fresh Facebook context hygiene reports the stored automation tab without treating it as orphaned", async () => {
  const harness = loadBackgroundHarness({
    storedTabId: 17,
    currentTab: { id: 17, url: "https://www.facebook.com/marketplace/create/item", status: "complete" },
  });
  await harness.api.rememberAutomationTab(17);
  const hygiene = await harness.api.facebookTabHygiene();
  assert.equal(hygiene.total_facebook_tabs, 1);
  assert.equal(hygiene.total_marketplace_create_tabs, 1);
  assert.equal(hygiene.posterpro_automation_tabs, 1);
  assert.equal(hygiene.orphan_create_tabs, 0);
  assert.equal(hygiene.stored_automation_tab_id, 17);
});

test("read-only context probe evidence encoder preserves structured bounded data", () => {
  const expected = { tab_id: 17, workflow_stage: "DETAILS", photo_preview_count: 0, draft_restore_evidence: false };
  const encoded = loadBackgroundHarness().api.encodeFacebookContextProbe(expected);
  assert.match(encoded, /^CTX69:(?:x[A-Za-z0-9_-]+)(?:\.x[A-Za-z0-9_-]+)*$/);
  const segments = encoded.slice("CTX69:".length).split(".").map((part) => part.slice(1)).join("").replace(/-/g, "+").replace(/_/g, "/");
  const padded = segments + "=".repeat((4 - segments.length % 4) % 4);
  const decoded = JSON.parse(Buffer.from(padded, "base64").toString("utf8"));
  assert.deepEqual(decoded, expected);
});
