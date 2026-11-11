const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const VERSION = "0.3.103";
const BUILD = `posterpro-${VERSION}-facebook-taxonomy-window-tab-discovery`;
const BLANK_EVIDENCE = { ok: true, blank: true, form_ready: true, category_control_found: true,
  inspected_fields: [{ field: "Title", tag: "input", value_present: false, placeholder_present: true }],
  populated_fields: [], photo_preview_count: 0, selected_file_count: 0 };

function makeBinding(overrides = {}) {
  return { device_id: 94, authorized_session_id: "authorized-session-0123456789",
    collection_session_id: "collector-session-012345678901", window_id: 2, tab_id: 18,
    extension_version: VERSION, status: "ACTIVE", checkpoint_revision: 6, current_path: [],
    categories_discovered: 0, verified_leaves: 0, complete_schemas: 0, ...overrides };
}

function harness(options = {}) {
  const state = { posterproBaseUrl: "https://posterpro.sparkleserver.site", posterproDeviceToken: "secret-device-token",
    posterproDevice: { id: 94, extension_version: VERSION }, ...(options.state || {}) };
  const requests = [];
  const sent = [];
  const tabQueries = [];
  const focusedUpdates = [];
  const queryCountsByWindow = new Map();
  const createdWindowIds = [];
  const tabs = new Map([[17, { id: 17, windowId: 1, url: options.operatorUrl || "https://example.com/work", status: "complete", active: true }]]);
  const windows = new Map([[1, { id: 1, focused: true }]]);
  let nextTab = 17;
  let nextWindow = 1;
  let listener;
  if (options.bound !== false) {
    const binding = options.binding || options.state?.posterproFacebookTaxonomyCollectorBinding || makeBinding();
    state.posterproFacebookTaxonomyCollectorBinding = binding;
    state.posterproTaxonomyCollectionTabId = binding.tab_id;
    windows.set(binding.window_id, { id: binding.window_id, focused: false });
    tabs.set(binding.tab_id, { id: binding.tab_id, windowId: binding.window_id,
      url: "https://www.facebook.com/marketplace/create/item", status: "complete", active: true });
    nextTab = Math.max(nextTab, binding.tab_id);
    nextWindow = Math.max(nextWindow, binding.window_id);
  }
  if (options.pendingWindow) {
    const pending = options.pendingWindow;
    state.posterproFacebookTaxonomyPendingWindow = pending;
    windows.set(pending.window_id, { id: pending.window_id, focused: false });
    const pendingTabId = Number(options.pendingTabId || pending.tab_id);
    if (Number.isInteger(pendingTabId)) tabs.set(pendingTabId, { id: pendingTabId, windowId: pending.window_id,
      url: "https://www.facebook.com/marketplace/create/item", status: "complete", active: true });
    if (Number.isInteger(pendingTabId)) nextTab = Math.max(nextTab, pendingTabId);
    nextWindow = Math.max(nextWindow, pending.window_id);
  }
  const chrome = {
    runtime: { getManifest: () => ({ version: VERSION }), onInstalled: { addListener() {} }, onStartup: { addListener() {} },
      onMessage: { addListener(fn) { listener = fn; } }, get lastError() { return null; } },
    alarms: { create() {}, onAlarm: { addListener() {} } },
    storage: {
      local: { async get(keys) { return Object.fromEntries(keys.map((key) => [key, state[key]])); }, async set(value) { Object.assign(state, value); }, async remove(key) { delete state[key]; } },
      sync: { async get(defaults) { return defaults; }, async set() {} },
    },
    tabs: {
      async query(query = {}) {
        tabQueries.push({ ...query });
        const rows = [...tabs.values()].filter((tab) => !Number.isInteger(query.windowId) || tab.windowId === query.windowId);
        if (!Number.isInteger(query.windowId)) return rows;
        const attempts = (queryCountsByWindow.get(query.windowId) || 0) + 1;
        queryCountsByWindow.set(query.windowId, attempts);
        if (options.neverExposeCollectorTab && createdWindowIds.includes(query.windowId)) return [];
        const exposeAfter = Number(options.exposeCollectorTabAfterQueries || 0);
        return createdWindowIds.includes(query.windowId) && attempts < exposeAfter ? [] : rows;
      },
      async get(id) { const tab = tabs.get(id); if (!tab) throw new Error(`No tab with id: ${id}`); return { ...tab }; },
      async create({ url, windowId }) { const id = ++nextTab; const ownerWindow = windowId || 1; const tab = { id, windowId: ownerWindow, url, status: "complete", active: false }; tabs.set(id, tab); return { ...tab }; },
      async sendMessage(id, message) {
        sent.push({ id, message });
        if (options.onMessage) return options.onMessage({ id, message, sent });
        if (message.action === "posterpro_taxonomy_collector_probe") return options.probe || {
          identity: { content_script_version: VERSION, taxonomy_collector_build_id: BUILD }, adapter_available: true,
          traversal_available: true, blank_form: true, create_form_ready: true, blank_form_evidence: BLANK_EVIDENCE, collector_running: false,
        };
        if (message.action === "posterpro_start_facebook_taxonomy_collection") return options.startResult || {
          ok: true, status: "RUNNING", stage: "RUNNING", roots_discovered: 8, captured_nodes: 8, checkpoint_revision: 7,
        };
        if (message.action === "posterpro_taxonomy_test_command") return { ok: true, command: message.command,
          items: [{ label: "Household", category_id: null }], submission_performed: false };
        return { ok: true };
      },
    },
    windows: {
      async get(id) { const win = windows.get(id); if (!win) throw new Error(`No window ${id}`); return { ...win }; },
      async getAll() { return [...windows.values()].map((win) => ({ ...win })); },
      async create({ url, focused, type }) {
        const id = ++nextWindow; createdWindowIds.push(id); windows.set(id, { id, focused: Boolean(options.creationStealsFocus), type });
        if (options.creationStealsFocus) windows.set(1, { id: 1, focused: false });
        const tab = await chrome.tabs.create({ url, windowId: id });
        const result = { id, focused: Boolean(options.creationStealsFocus), type };
        if (options.createTabsMode !== "undefined") result.tabs = options.createTabsMode === "empty" ? [] : [tab];
        return result;
      },
      async update(id, changes) { focusedUpdates.push({ id, changes }); for (const [key, win] of windows) windows.set(key, { ...win, focused: key === id && changes.focused === true }); return windows.get(id); },
    },
    scripting: { async executeScript({ target, files }) { if (options.injectionError) throw new Error(options.injectionError); return [{ target, files }]; } },
  };
  let serverBinding = state.posterproFacebookTaxonomyCollectorBinding || null;
  const fetch = async (url, request) => {
    requests.push({ url: String(url), method: request.method, body: request.body });
    const endpoint = String(url);
    if (endpoint.endsWith("/browser-extension/heartbeat")) return response({ device: { id: 94, extension_version: VERSION, update_required: false, last_seen_at: new Date().toISOString() } });
    if (endpoint.endsWith("/collector-binding") && request.method === "POST") {
      const body = JSON.parse(request.body); serverBinding = makeBinding({ ...body, device_id: 94, authorized_session_id: "authorized-session-0123456789", status: body.status });
      return response({ ok: true, authorized_session_id: serverBinding.authorized_session_id, binding: serverBinding });
    }
    if (endpoint.endsWith("/collector-binding") && request.method === "PUT") {
      const body = JSON.parse(request.body); serverBinding = { ...serverBinding, ...body };
      return response({ ok: true, authorized_session_id: serverBinding.authorized_session_id, binding: serverBinding });
    }
    if (endpoint.endsWith("/collector-binding")) return response({ binding: serverBinding });
    if (endpoint.endsWith("/browser-extension/taxonomy-test/jobs/claim")) return response({ job: null });
    if (endpoint.includes("/taxonomy-test/jobs/") && endpoint.endsWith("/result")) return response({ ok: true, status: "COMPLETED" });
    if (endpoint.endsWith("/collector-diagnostic")) return response({ ok: true });
    return response({ revision: 6, checkpoint: { marketplace: "facebook", frontier: [], categories: {}, status: "in_progress" } });
  };
  const backgroundSource = fs.readFileSync(path.join(__dirname, "..", "background.js"), "utf8")
    .replace("const FACEBOOK_TAXONOMY_TAB_DISCOVERY_TIMEOUT_MS = 12000;",
      `const FACEBOOK_TAXONOMY_TAB_DISCOVERY_TIMEOUT_MS = ${Number(options.discoveryTimeoutMs || 12000)};`);
  const source = `${backgroundSource}\nglobalThis.__test = { startFacebookTaxonomyCollection, runTaxonomyTestJob, claimAndRunTaxonomyTestJob, ensureDedicatedFacebookTaxonomyWindow, discoverFacebookCreateTab };`;
  const context = { chrome, fetch, URL, Date, Promise, setTimeout, clearTimeout, AbortController, console, crypto: { randomUUID: () => "new-collector-session-0123456789" } };
  context.globalThis = context;
  vm.runInNewContext(source, context);
  return { api: context.__test, state, requests, tabs, windows, sent, tabQueries, focusedUpdates, createdWindowIds,
    send(message) { return new Promise((resolve) => listener(message, {}, resolve)); } };
}
function response(data) { return { ok: true, async text() { return JSON.stringify(data); } }; }

test("start creates and uses a dedicated non-focused window without querying or activating the operator tab", async () => {
  const h = harness({ bound: false });
  const result = await h.api.startFacebookTaxonomyCollection(true);
  assert.equal(result.ok, true, JSON.stringify(result));
  const binding = h.state.posterproFacebookTaxonomyCollectorBinding;
  assert.equal(binding.window_id, 2);
  assert.equal(binding.tab_id, 18);
  assert.equal(binding.extension_version, VERSION);
  assert.equal(h.windows.get(2).focused, false);
  assert.equal(h.tabs.get(17).active, true);
  assert.ok(h.sent.every((entry) => entry.id === binding.tab_id));
  assert.ok(h.tabQueries.every((query) => Number.isInteger(query.windowId) && query.windowId === binding.window_id));
  assert.equal(h.focusedUpdates.length, 0);
  const createRequest = h.requests.find((entry) => entry.url.endsWith("/collector-binding") && entry.method === "POST");
  assert.equal(JSON.parse(createRequest.body).operator_opt_in, true);
});

test("windows.create may omit tabs; exact-window query still discovers and binds the Facebook tab", async () => {
  const h = harness({ bound: false, createTabsMode: "undefined" });
  const result = await h.api.startFacebookTaxonomyCollection(true);
  assert.equal(result.ok, true);
  assert.equal(h.createdWindowIds.length, 1);
  assert.equal(h.state.posterproFacebookTaxonomyCollectorBinding.window_id, 2);
  assert.equal(h.state.posterproFacebookTaxonomyCollectorBinding.tab_id, 18);
  assert.ok(h.tabQueries.some((query) => query.windowId === 2));
  assert.equal(h.windows.get(2).focused, false);
});

test("empty create tabs and delayed exact-window query are handled with bounded discovery", async () => {
  const h = harness({ bound: false, createTabsMode: "empty", exposeCollectorTabAfterQueries: 4 });
  const result = await h.api.startFacebookTaxonomyCollection(true);
  assert.equal(result.ok, true);
  assert.ok(result.window_discovery_evidence.attempts >= 4);
  assert.equal(h.state.posterproFacebookTaxonomyCollectorBinding.window_id, 2);
  assert.equal(h.state.posterproFacebookTaxonomyCollectorBinding.tab_id, 18);
  assert.equal(h.createdWindowIds.length, 1);
  assert.equal(h.focusedUpdates.length, 0);
});

test("failed discovery leaves a recoverable owned-window marker and retry reuses it without creating a duplicate", async () => {
  const first = harness({ bound: false, createTabsMode: "undefined", neverExposeCollectorTab: true, discoveryTimeoutMs: 80 });
  const failed = await first.api.startFacebookTaxonomyCollection(true);
  assert.equal(failed.ok, false);
  assert.equal(failed.error_code, "COLLECTOR_TAB_DISCOVERY_TIMEOUT");
  assert.equal(failed.window_discovery_evidence.window_id, 2);
  assert.equal(first.createdWindowIds.length, 1);
  assert.equal(first.state.posterproFacebookTaxonomyCollectorBinding, undefined);
  assert.equal(first.state.posterproFacebookTaxonomyPendingWindow.window_id, 2);
  const diagnostic = first.requests.find((request) => request.url.endsWith("/collector-diagnostic"));
  assert.equal(JSON.parse(diagnostic.body).window_discovery_evidence.window_id, 2);

  const resumed = harness({ bound: false, state: first.state, pendingWindow: first.state.posterproFacebookTaxonomyPendingWindow, pendingTabId: 18 });
  const recovered = await resumed.api.startFacebookTaxonomyCollection(true);
  assert.equal(recovered.ok, true, JSON.stringify(recovered));
  assert.equal(resumed.createdWindowIds.length, 0);
  assert.equal(resumed.windows.size, 2);
  assert.equal(resumed.state.posterproFacebookTaxonomyCollectorBinding.window_id, 2);
  assert.equal(resumed.state.posterproFacebookTaxonomyCollectorBinding.tab_id, 18);
  assert.equal(resumed.state.posterproFacebookTaxonomyPendingWindow, undefined);
  assert.equal(resumed.focusedUpdates.length, 0);
});

test("tab discovery returns a bounded, sanitized failure when no tab ever appears", async () => {
  const h = harness({ bound: false });
  await assert.rejects(h.api.discoverFacebookCreateTab(99, { timeoutMs: 60 }), (error) => {
    assert.equal(error.code, "COLLECTOR_TAB_DISCOVERY_TIMEOUT");
    assert.equal(error.evidence.window_id, 99);
    assert.ok(error.evidence.attempts >= 1);
    assert.ok(Array.isArray(error.evidence.last_query_tabs));
    return true;
  });
});

test("multiple starts and service-worker reconstruction reuse the persisted window and tab", async () => {
  const h = harness({ bound: false });
  const first = await h.api.startFacebookTaxonomyCollection(true);
  assert.equal(first.ok, true);
  const countAfterFirst = h.windows.size;
  const second = await h.api.startFacebookTaxonomyCollection(true);
  assert.equal(second.ok, true);
  assert.equal(h.windows.size, countAfterFirst);
  assert.equal(h.state.posterproFacebookTaxonomyCollectorBinding.tab_id, 18);
  const restartedWorker = harness({ state: h.state });
  const resumed = await restartedWorker.api.startFacebookTaxonomyCollection(true);
  assert.equal(resumed.ok, true);
  assert.equal(restartedWorker.windows.size, 2);
  assert.equal(restartedWorker.state.posterproFacebookTaxonomyCollectorBinding.tab_id, 18);
});

test("extension upgrade reuses the same bound window and refreshes the server binding version", async () => {
  const prior = makeBinding({ extension_version: "0.3.101" });
  const h = harness({ binding: prior });
  const result = await h.api.startFacebookTaxonomyCollection(true);
  assert.equal(result.ok, true);
  assert.equal(h.windows.size, 2);
  assert.equal(h.state.posterproFacebookTaxonomyCollectorBinding.window_id, 2);
  assert.equal(h.state.posterproFacebookTaxonomyCollectorBinding.tab_id, 18);
  assert.equal(h.state.posterproFacebookTaxonomyCollectorBinding.extension_version, VERSION);
  assert.equal(h.requests.some((request) => request.url.endsWith("/collector-binding") && request.method === "POST"), false);
});

test("a closed bound collector window is recovered with a new session while the operator tab remains untouched", async () => {
  const h = harness();
  h.windows.delete(2);
  h.tabs.delete(18);
  const result = await h.api.startFacebookTaxonomyCollection(true);
  assert.equal(result.ok, true);
  const binding = h.state.posterproFacebookTaxonomyCollectorBinding;
  assert.equal(binding.window_id, 3);
  assert.equal(binding.tab_id, 19);
  assert.equal(h.tabs.get(17).active, true);
  assert.ok(h.sent.every((entry) => entry.id === 19));
});

test("creation focus change fails closed and does not execute scripts in a focused collector", async () => {
  const h = harness({ bound: false, creationStealsFocus: true });
  const result = await h.api.startFacebookTaxonomyCollection(true);
  assert.equal(result.ok, false);
  assert.equal(result.error_code, "BACKGROUND_WINDOW_FOCUS_REQUIRED");
  assert.equal(h.sent.some((entry) => entry.message.action === "posterpro_start_facebook_taxonomy_collection"), false);
  assert.equal(h.focusedUpdates.length, 0);
});

test("bridge command rejects stale or different tab bindings and cannot fall back to an operator tab", async () => {
  const h = harness();
  await assert.rejects(() => h.api.runTaxonomyTestJob({ job_id: "stale", device_id: 94, command: "INSPECT_FORM",
    parameters: {}, collection_session_id: "old-collector-session-0123456789", collector_window_id: 2, collector_tab_id: 18 }), /not bound/i);
  assert.equal(h.sent.some((entry) => entry.message.action === "posterpro_taxonomy_test_command"), false);
  await assert.rejects(() => h.api.runTaxonomyTestJob({ job_id: "wrong-tab", device_id: 94, command: "INSPECT_FORM",
    parameters: {}, collection_session_id: h.state.posterproFacebookTaxonomyCollectorBinding.collection_session_id,
    collector_window_id: 2, collector_tab_id: 17 }), /not bound/i);
  assert.equal(h.tabs.get(17).url, "https://example.com/work");
});

test("a bound bridge command returns the immutable session and exact window/tab identity", async () => {
  const h = harness();
  const binding = h.state.posterproFacebookTaxonomyCollectorBinding;
  const result = await h.api.runTaxonomyTestJob({ job_id: "bound-job", device_id: 94, command: "INSPECT_FORM", parameters: {},
    session_id: binding.authorized_session_id, collection_session_id: binding.collection_session_id,
    collector_window_id: binding.window_id, collector_tab_id: binding.tab_id });
  assert.equal(result.status, "COMPLETED");
  assert.equal(h.sent.at(-1).id, binding.tab_id);
  const posted = JSON.parse(h.requests.find((entry) => entry.url.endsWith("/bound-job/result")).body);
  assert.equal(posted.collection_session_id, binding.collection_session_id);
  assert.equal(posted.collector_window_id, binding.window_id);
  assert.equal(posted.collector_tab_id, binding.tab_id);
});

test("pause and stop target only the bound tab; only explicit Show Collector Window may focus it", async () => {
  const h = harness();
  const binding = h.state.posterproFacebookTaxonomyCollectorBinding;
  const pause = await h.send({ action: "posterpro_pause_facebook_taxonomy_collection", paused: true });
  assert.equal(pause.ok, true);
  assert.equal(h.sent.at(-1).id, binding.tab_id);
  const stop = await h.send({ action: "posterpro_stop_facebook_taxonomy_collection" });
  assert.equal(stop.ok, true);
  assert.equal(h.sent.at(-1).id, binding.tab_id);
  assert.equal(h.focusedUpdates.length, 0);
  const show = await h.send({ action: "posterpro_show_facebook_collector_window" });
  assert.equal(show.ok, true);
  assert.equal(h.focusedUpdates.at(-1).id, binding.window_id);
  assert.equal(h.focusedUpdates.at(-1).changes.focused, true);
});

test("a closed collector tab returns a controlled error instead of executing against the active unrelated tab", async () => {
  const h = harness();
  h.tabs.delete(18);
  const pause = await h.send({ action: "posterpro_pause_facebook_taxonomy_collection", paused: true });
  assert.equal(pause.ok, false);
  assert.equal(pause.error_code, "COLLECTOR_WINDOW_OR_TAB_MISSING");
  assert.equal(h.sent.some((entry) => entry.id === 17), false);
});
