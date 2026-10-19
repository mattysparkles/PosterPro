const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function makeHarness(overrides = {}) {
  const buildId = "posterpro-0.3.70-facebook-background-queue-focus-safety";
  const state = {
    posterproDeviceToken: "paired-device-token",
    posterproActiveJob: {
      id: 59, action: "CREATE", marketplace: "facebook", tab_id: 17,
      status: "SUBMITTING", browser_input_nonce: "one-use-token",
      browser_input_stage: "FACEBOOK_SHIPPING_UPDATE", browser_input_used: false,
      browser_input_content_build_id: buildId,
      ...overrides.activeJob,
    },
  };
  const counts = { attach: 0, detach: 0, commands: [] };
  const contentHitTests = [];
  const focusCalls = [];
  const cdpNode = (tag, text, attrs = {}, parentElement = null) => ({
    nodeType: 1, tagName: tag.toUpperCase(), innerText: text, textContent: text,
    isConnected: true, parentElement, getAttribute(name) { return attrs[name] || null; },
  });
  const shippingDialogNode = cdpNode("div", "Change shipping method", { role: "dialog" });
  const updateButtonNode = cdpNode("div", "Update", { role: "button" }, shippingDialogNode);
  const updateSpanNode = cdpNode("span", "Update", {}, updateButtonNode);
  let activeTab = overrides.tabActive ?? true;
  let focusedWindowId = overrides.windowFocused === false ? (overrides.lastFocusedWindow?.id || 2) : 5;
  const tabContext = { id: 17, windowId: 5, status: "complete", highlighted: true, get active() { return activeTab; }, url: overrides.currentTabUrl || "https://www.facebook.com/marketplace/create/item" };
  const windowContext = { id: 5, state: "normal" };
  const chrome = {
    runtime: {
      id: "posterpro-extension-test",
      onInstalled: { addListener() {} }, onStartup: { addListener() {} }, onMessage: { addListener() {} },
    },
    alarms: { create() {}, onAlarm: { addListener() {} } },
    storage: { local: {
      async get(keys) { return Object.fromEntries(keys.map((key) => [key, state[key]])); },
      async set(values) { Object.assign(state, values); },
    }, sync: { async get(defaults) { return defaults; }, async set() {} } },
    windows: {
      async get(id) { assert.equal(id, 5); return { ...windowContext, focused: focusedWindowId === id }; },
      async getLastFocused() { return overrides.lastFocusedWindow || { id: focusedWindowId, focused: true }; },
      async update(id, change) { focusCalls.push({ kind: "window", id, change }); if (change.focused) focusedWindowId = id; return { ...windowContext, id, focused: focusedWindowId === id }; },
    },
    tabs: {
      onUpdated: { addListener() {}, removeListener() {} },
      async get(id) { assert.equal(id, 17); return { ...tabContext, active: activeTab }; },
      async query({ active, windowId }) { return active && windowId === 5 && activeTab ? [{ id: 17 }] : []; },
      async getZoom(id) { assert.equal(id, 17); return 1; },
      async update(id, change) { focusCalls.push({ kind: "tab", id, change }); if (id === 17) activeTab = Boolean(change.active); return { ...tabContext, id, active: activeTab }; },
      async sendMessage(id, message) {
        assert.equal(id, 17);
        if (message.action === "posterpro_reconcile_facebook_seller_listing") {
          return overrides.reconciliationResult || {
            state: "FOUND", external_listing_id: "FB-55",
            external_url: "https://www.facebook.com/marketplace/item/FB-55/",
            exact_title_match: true, exact_price_match: true,
            description_verification: { matches: true, expected_hash: "hash", observed_hash: "hash", expected_length: 20, observed_length: 20 },
          };
        }
        if (message.action === "posterpro_resolve_facebook_update_target_after_foreground") {
          if (overrides.postTargetResponse) return overrides.postTargetResponse;
          const postTarget = { ...request.target, ...(overrides.postTarget || {}),
            content_script_build_id: buildId, evidence_source: "content-script-live-target",
            hit_target_inside: true, is_connected: true, document_contains: true,
            current_dialog_match: true, enabled: true,
          };
          postTarget.rect = overrides.postTarget?.rect || { left: postTarget.left, top: postTarget.top,
            right: postTarget.left + postTarget.width, bottom: postTarget.top + postTarget.height,
            width: postTarget.width, height: postTarget.height };
          postTarget.viewport_environment = overrides.postTarget?.viewport_environment || {
            inner_width: postTarget.viewport_width, inner_height: postTarget.viewport_height,
          };
          return { ok: true, target: postTarget, stability_samples: [{ sample: 1 }, { sample: 2 }] };
        }
        if (message.action === "posterpro_validate_facebook_update_activation_point") {
          contentHitTests.push(message);
          return overrides.integerHitTestResponse || { ok: true, target_inside_update: true,
            activation_coordinate: message.activation_coordinate,
            current_dialog: { role: "dialog", heading: "Change shipping method", generation: 1 },
            update_target: { actionable_ancestor: { role: "button", text: "Update" } },
            content_integer_hit_test: { target: { tag: "span", label: "Update" }, target_inside_update: true,
              ancestry: [{ tag: "span" }, { tag: "div", role: "button", label: "Update" }] } };
        }
        if (message.action === "posterpro_resolve_facebook_description_save_after_foreground") {
          const target = { ...request.target, ...message.expected_identity, x: 50, y: 35, left: 10, top: 20, width: 80, height: 30, viewport_width: 1280, viewport_height: 800, label: "Save", role: "button", dialog_heading: null, hit_target_inside: true, is_connected: true, document_contains: true, current_dialog_match: true, repair_form_identity_verified: true, enabled: true, evidence_source: "content-script-live-target", content_script_build_id: buildId };
          target.rect = { left: 10, top: 20, right: 90, bottom: 50, width: 80, height: 30 };
          target.viewport_environment = { inner_width: 1280, inner_height: 800 };
          return { ok: true, target };
        }
        if (message.action === "posterpro_validate_facebook_description_save_activation_point") {
          contentHitTests.push(message);
          return { ok: true, target_inside_control: true, repair_form_identity_verified: true, activation_coordinate: message.activation_coordinate };
        }
        return overrides.identity || { extension_version: "0.3.70", content_script_version: "0.3.70", content_script_build_id: buildId };
      },
    },
    debugger: {
      async getTargets() { return overrides.targets || []; },
      async attach(target, version) { counts.attach += 1; assert.equal(target.tabId, 17); assert.equal(version, "1.3"); if (overrides.failAttach) throw new Error("attach refused"); },
      async sendCommand(target, method, params) {
        counts.commands.push({ target, method, params });
        if (overrides.failCommand === (params.type || method)) throw new Error(`${params.type || method} refused`);
        if (method === "Page.getLayoutMetrics") return {
          cssLayoutViewport: { pageX: 0, pageY: 0, clientWidth: 1280, clientHeight: 800 },
          cssVisualViewport: { pageX: 0, pageY: 0, offsetX: 0, offsetY: 0, clientWidth: 1280, clientHeight: 800, scale: 1 },
          pageScaleFactor: 1,
        };
        if (method === "DOM.getNodeForLocation") {
          assert.equal(Number.isInteger(params.x), true);
          assert.equal(Number.isInteger(params.y), true);
          return overrides.browserHit || { nodeId: 3, backendNodeId: 3, frameId: "frame-1" };
        }
        if (method === "DOM.resolveNode") return { object: { type: "object", objectId: "hit-node-object" } };
        if (method === "Runtime.callFunctionOn") {
          if (overrides.failAncestry) throw new Error("runtime ancestry unavailable");
          let value = overrides.ancestry;
          if (!value) {
            const root = overrides.browserHitDisagree ? updateSpanNode : updateSpanNode;
            const fn = Function(`return (${params.functionDeclaration})`)();
            value = fn.call(root, ...params.arguments.map((item) => item.value));
            if (overrides.browserHitDisagree) value = value.slice(0, 1);
          }
          return { result: { type: "object", value } };
        }
        if (method === "Runtime.releaseObject") return {};
      },
      async detach(target) { counts.detach += 1; assert.equal(target.tabId, 17); if (overrides.failDetach) throw new Error("detach refused"); },
    },
    scripting: { async executeScript() { return []; } },
  };
  const source = `${fs.readFileSync(path.join(__dirname, "..", "background.js"), "utf8")}\nglobalThis.__test = { activateValidatedMarketplaceControl, handleMarketplaceControlRequest, releaseMarketplaceFocusLease, makeCanonicalActivationPoint, reconcileFacebookDescriptionRepair };`;
  const context = { chrome, URL, Promise, Date, Set, Number, String, Object, Math, setTimeout, clearTimeout,
    fetch: async () => ({ ok: true, async text() { return "{}"; } }) };
  context.globalThis = context;
  vm.runInNewContext(source, context);
  const request = {
    action: "posterpro_marketplace_control_activation", authorization_nonce: "one-use-token",
    marketplace: "facebook", expected_origin: "https://www.facebook.com",
    expected_stage: "FACEBOOK_SHIPPING_UPDATE", expected_transition: "SHIPPING_DIALOG_CLOSED",
    target: {
      x: 50, y: 35, left: 10, top: 20, width: 80, height: 30,
      viewport_width: 1280, viewport_height: 800,
      label: "Update", role: "button", dialog_heading: "Change shipping method",
      hit_target_inside: true, is_connected: true, document_contains: true,
      current_dialog_match: true, enabled: true, evidence_source: "content-script-live-target",
      content_script_build_id: buildId,
    },
  };
  const sender = {
    id: "posterpro-extension-test", frameId: 0,
    tab: { id: 17, url: "https://www.facebook.com/marketplace/create/item" },
  };
  return { api: context.__test, request, sender, state, counts, focusCalls, contentHitTests };
}

test("lost description-repair Save response reconciles read-only and never authorizes another Save", async () => {
  const h = makeHarness({
    currentTabUrl: "https://www.facebook.com/marketplace/item/FB-55/",
    reconciliationResult: {
      state: "FOUND", external_listing_id: "FB-55",
      external_url: "https://www.facebook.com/marketplace/item/FB-55/",
      exact_title_match: true, exact_price_match: true,
      description_verification: { matches: true, expected_hash: "hash", observed_hash: "hash", expected_length: 20, observed_length: 20 },
    },
  });
  const job = { id: 8, external_listing_id: "FB-55", external_url: "https://www.facebook.com/marketplace/item/FB-55/" };
  const snapshot = { repair_scope: "facebook_description_only", repair_expected_remote_id: "FB-55", repair_expected_remote_url: job.external_url, repair_expected_title: "Known item", repair_expected_price: 29.99, title: "Known item", price: 29.99, description: "canonical description" };
  const saved = await h.api.reconcileFacebookDescriptionRepair(job, 17, snapshot, { save_activation_count: 1, submission_performed: true });
  assert.equal(saved.status, "COMPLETED", JSON.stringify(saved));
  assert.equal(saved.repair_state, "FACEBOOK_DESCRIPTION_REPAIR_SUCCESS");
  assert.equal(saved.external_listing_id, job.external_listing_id);
  assert.equal(saved.external_url, job.external_url);
  assert.equal(saved.remote_description_verification.matches, true);
  assert.equal(h.counts.commands.length, 0);

  const absent = makeHarness({ currentTabUrl: "https://www.facebook.com/marketplace/item/FB-55/", reconciliationResult: {
    state: "FOUND", external_listing_id: "FB-55", external_url: job.external_url,
    exact_title_match: true, exact_price_match: true,
    description_verification: { matches: false, expected_hash: "hash", observed_hash: null, expected_length: 20, observed_length: 0 },
  } });
  const failed = await absent.api.reconcileFacebookDescriptionRepair(job, 17, snapshot, { save_activation_count: 1, submission_performed: true });
  assert.equal(failed.status, "FAILED");
  assert.equal(failed.error_code, "FACEBOOK_DESCRIPTION_REPAIR_FAILED");
  assert.equal(absent.counts.commands.length, 0);

  const ambiguous = makeHarness({ currentTabUrl: "https://www.facebook.com/marketplace/item/FB-55/", reconciliationResult: { state: "AMBIGUOUS" } });
  const unknown = await ambiguous.api.reconcileFacebookDescriptionRepair(job, 17, snapshot, { save_activation_count: 1, submission_performed: true });
  assert.equal(unknown.status, "AWAITING_OPERATOR_REVIEW");
  assert.equal(unknown.error_code, "FACEBOOK_DESCRIPTION_REPAIR_OUTCOME_UNKNOWN");
  assert.equal(ambiguous.counts.commands.length, 0);
});

test("validated Marketplace control uses exactly one scoped CDP move/press/release and detaches", async () => {
  const h = makeHarness();
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.activation_method, "chrome_debugger_cdp_input");
  assert.deepEqual(Array.from(result.commands), ["mouseMoved", "mousePressed", "mouseReleased"]);
  assert.equal(result.command_results.every((item) => item.status === "resolved"), true);
  assert.equal(result.attach_result.status, "resolved");
  assert.equal(result.protocol_layout_metrics.cssVisualViewport.scale, 1);
  assert.equal(result.content_browser_hit_test_agree, true);
  assert.deepEqual(JSON.parse(JSON.stringify(result.activation_coordinate)), { x: 50, y: 35, rounding_method: "Math.round_stabilized_center" });
  assert.deepEqual(JSON.parse(JSON.stringify(h.contentHitTests[0].activation_coordinate)), JSON.parse(JSON.stringify(result.activation_coordinate)));
  assert.deepEqual(JSON.parse(JSON.stringify(result.browser_hit_test.raw_node)), { node_id: 3, backend_node_id: 3 });
  assert.equal(result.browser_hit_test.ancestry.some((node) => node.role === "button" && node.label === "Update"), true);
  assert.equal(result.browser_hit_test.ancestry.some((node) => node.role === "dialog" && node.label === "Change shipping method"), true);
  assert.equal(result.cdp_dialog_ancestry_proven, true);
  assert.equal(result.browser_hit_test.ancestry_source, "Runtime.callFunctionOn");
  assert.equal(h.counts.commands.some((item) => item.method === "Runtime.releaseObject"), true);
  assert.equal(h.counts.attach, 1);
  assert.equal(h.counts.detach, 1);
  assert.deepEqual(h.counts.commands.filter((item) => item.method === "Input.dispatchMouseEvent").map((item) => item.params.type), ["mouseMoved", "mousePressed", "mouseReleased"]);
  const inputCommands = h.counts.commands.filter((item) => item.method === "Input.dispatchMouseEvent");
  assert.ok(inputCommands.every((item) => item.params.x === 50 && item.params.y === 35));
  const browserHitCommand = h.counts.commands.find((item) => item.method === "DOM.getNodeForLocation");
  assert.deepEqual([browserHitCommand.params.x, browserHitCommand.params.y], [50, 35]);
  assert.equal(h.state.posterproActiveJob.browser_input_used, true);
  assert.equal(result.coordinate_space, "main_frame_viewport_css_pixels");
});

test("canonical rounded integer coordinate is shared by content hit test, CDP hit test, and every input event", async () => {
  const h = makeHarness({ postTarget: { x: 50.4, y: 35.6 } });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.deepEqual(JSON.parse(JSON.stringify(result.activation_coordinate)), { x: 50, y: 36, rounding_method: "Math.round_stabilized_center" });
  assert.deepEqual(JSON.parse(JSON.stringify(h.contentHitTests[0].activation_coordinate)), JSON.parse(JSON.stringify(result.activation_coordinate)));
  const domHit = h.counts.commands.find((item) => item.method === "DOM.getNodeForLocation");
  assert.deepEqual([domHit.params.x, domHit.params.y], [50, 36]);
  const inputCommands = h.counts.commands.filter((item) => item.method === "Input.dispatchMouseEvent");
  assert.deepEqual(inputCommands.map((item) => [item.params.type, item.params.x, item.params.y]), [
    ["mouseMoved", 50, 36], ["mousePressed", 50, 36], ["mouseReleased", 50, 36],
  ]);
  assert.equal(inputCommands.every((item) => Number.isInteger(item.params.x) && Number.isInteger(item.params.y)), true);
  assert.equal(h.contentHitTests.length, 1);
});

test("integer content hit-test rejection prevents debugger attach and all input", async () => {
  const h = makeHarness({ integerHitTestResponse: { ok: false, error_code: "FACEBOOK_INTEGER_HIT_TEST_OUTSIDE_UPDATE", target_inside_update: false } });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.ok, false);
  assert.equal(result.error_code, "FACEBOOK_INTEGER_HIT_TEST_OUTSIDE_UPDATE");
  assert.equal(result.content_integer_hit_test.target_inside_update, false);
  assert.equal(h.counts.attach, 0);
  assert.equal(h.counts.commands.length, 0);
  assert.equal(h.counts.detach, 0);
});

test("inactive Facebook automation tab is never focused or activated by background control", async () => {
  const h = makeHarness({ tabActive: false, windowFocused: false, lastFocusedWindow: { id: 2, focused: true } });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.ok, false);
  assert.equal(result.error_code, "MARKETPLACE_CONTROL_OPERATOR_FOCUS_REQUIRED");
  assert.equal(result.browser_context.before.active, false);
  assert.equal(result.browser_context.before.window_focused, false);
  assert.deepEqual(h.focusCalls, []);
  assert.equal(h.counts.attach, 0);
  assert.equal(h.counts.commands.length, 0);
});

test("CDP uses only stabilized coordinates without background focus changes", async () => {
  const h = makeHarness({ postTarget: { x: 88, y: 59, left: 48, top: 44 } });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.pre_foreground_target.x, 50);
  assert.equal(result.post_foreground_target.x, 88);
  assert.equal(result.coordinate_deltas.x, 38);
  assert.deepEqual(h.counts.commands.filter((item) => item.method === "Input.dispatchMouseEvent").map((item) => [item.params.x, item.params.y]), [[88, 59], [88, 59], [88, 59]]);
  assert.equal(result.browser_context.foreground.active, true);
  assert.equal(result.browser_context.focus_changed, false);
  assert.deepEqual(h.focusCalls, []);
});

test("browser-side hit-test disagreement is persisted and aborts before any mouse input", async () => {
  const h = makeHarness({ browserHitDisagree: true });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.ok, false);
  assert.equal(result.error_code, "MARKETPLACE_CONTENT_BROWSER_HIT_TEST_DISAGREE");
  assert.equal(result.browser_hit_test.ancestry[0].tag, "span");
  assert.equal(h.counts.commands.some((item) => item.method === "Input.dispatchMouseEvent"), false);
  assert.equal(h.counts.detach, 1);
});

test("CDP resolves the exact hit backend node and bounded ancestry proves SPAN to Update button", async () => {
  const h = makeHarness({ browserHit: { backendNodeId: 536, frameId: "frame-facebook" } });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.browser_hit_test.raw_node.backend_node_id, 536);
  assert.equal(result.browser_hit_test.ancestry[0].tag, "span");
  assert.equal(result.browser_hit_test.ancestry[1].tag, "div");
  assert.equal(result.browser_hit_test.ancestry[1].role, "button");
  assert.equal(result.browser_hit_test.ancestry[1].label, "Update");
  const resolve = h.counts.commands.find((item) => item.method === "DOM.resolveNode");
  assert.deepEqual(JSON.parse(JSON.stringify(resolve.params)), { backendNodeId: 536, objectGroup: "posterpro-facebook-hit-ancestry" });
  const inspect = h.counts.commands.find((item) => item.method === "Runtime.callFunctionOn");
  assert.equal(inspect.params.returnByValue, true);
  assert.match(inspect.params.functionDeclaration, /Math\.min\(8/);
});

test("connected Update button ancestry passes when dialog is beyond the bounded CDP walk", async () => {
  const ancestry = [
    { tag: "span", label: "Update", connected: true },
    { tag: "div", role: "button", label: "Update", connected: true },
    ...Array.from({ length: 6 }, () => ({ tag: "div", label: "Update", connected: true })),
  ];
  const h = makeHarness({ ancestry });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.content_browser_hit_test_agree, true);
  assert.equal(result.cdp_dialog_ancestry_proven, false);
  assert.deepEqual(Array.from(result.commands), ["mouseMoved", "mousePressed", "mouseReleased"]);
});

test("CDP dialog ancestry is retained as supporting evidence when present", async () => {
  const h = makeHarness();
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.content_browser_hit_test_agree, true);
  assert.equal(result.cdp_dialog_ancestry_proven, true);
});

test("Update-labeled SPAN without enclosing Update button ancestry does not pass the gate", async () => {
  const h = makeHarness({ ancestry: [{ tag: "span", label: "Update", connected: true }] });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.content_browser_hit_test_agree, false);
  assert.equal(result.error_code, "MARKETPLACE_CONTENT_BROWSER_HIT_TEST_DISAGREE");
  assert.equal(h.counts.commands.some((item) => item.method === "Input.dispatchMouseEvent"), false);
});

test("disconnected CDP Update button ancestor does not pass agreement", async () => {
  const h = makeHarness({ ancestry: [
    { tag: "span", label: "Update", connected: true },
    { tag: "div", role: "button", label: "Update", connected: false },
    { tag: "div", role: "dialog", label: "Change shipping method", connected: true },
  ] });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.content_browser_hit_test_agree, false);
  assert.equal(h.counts.commands.some((item) => item.method === "Input.dispatchMouseEvent"), false);
});

test("content coordinate mismatch or non-current dialog blocks activation", async () => {
  const makeContent = (activation_coordinate, heading) => ({
    ok: true, target_inside_update: true, activation_coordinate,
    current_dialog: { role: "dialog", heading, generation: 1 },
    update_target: { actionable_ancestor: { role: "button", text: "Update" } },
    content_integer_hit_test: { target_inside_update: true },
  });
  const wrongCoordinate = makeHarness({ integerHitTestResponse: makeContent({ x: 51, y: 35 }, "Change shipping method") });
  const coordinateResult = await wrongCoordinate.api.handleMarketplaceControlRequest(wrongCoordinate.request, wrongCoordinate.sender);
  assert.equal(coordinateResult.content_browser_hit_test_agree, false);
  assert.equal(wrongCoordinate.counts.commands.some((item) => item.method === "Input.dispatchMouseEvent"), false);

  const wrongDialog = makeHarness({ integerHitTestResponse: makeContent({ x: 50, y: 35 }, "Other dialog") });
  const dialogResult = await wrongDialog.api.handleMarketplaceControlRequest(wrongDialog.request, wrongDialog.sender);
  assert.equal(dialogResult.content_browser_hit_test_agree, false);
  assert.equal(wrongDialog.counts.commands.some((item) => item.method === "Input.dispatchMouseEvent"), false);
});

test("CDP ancestry failure is unavailable evidence, not disagreement, and sends no input", async () => {
  const h = makeHarness({ failAncestry: true });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.browser_hit_test.ok, false);
  assert.equal(result.browser_hit_test.error_code, "CDP_HIT_NODE_ANCESTRY_UNAVAILABLE");
  assert.equal(result.content_browser_hit_test_agree, null);
  assert.equal(result.error_code, "CDP_HIT_NODE_ANCESTRY_UNAVAILABLE");
  assert.equal(h.counts.commands.some((item) => item.method === "Input.dispatchMouseEvent"), false);
});

test("failed CDP hit-test command is reported as unavailable, not as a content/browser disagreement", async () => {
  const h = makeHarness({ failCommand: "DOM.getNodeForLocation" });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.ok, false);
  assert.equal(result.browser_hit_test, null);
  assert.equal(result.content_browser_hit_test_agree, null);
  assert.notEqual(result.error_code, "MARKETPLACE_CONTENT_BROWSER_HIT_TEST_DISAGREE");
  assert.equal(result.command_results.find((item) => item.label === "browserHitTest").status, "rejected");
  assert.equal(h.counts.commands.some((item) => item.method === "Input.dispatchMouseEvent"), false);
  assert.equal(h.counts.detach, 1);
});

test("post-foreground geometry instability is diagnosed without attaching or clicking", async () => {
  const h = makeHarness({ postTargetResponse: { ok: false, error_code: "MARKETPLACE_CONTROL_GEOMETRY_UNSTABLE", stability_samples: [{ sample: 1 }, { sample: 2 }] } });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.ok, false);
  assert.equal(result.error_code, "MARKETPLACE_CONTROL_GEOMETRY_UNSTABLE");
  assert.equal(h.counts.attach, 0);
  assert.equal(h.counts.commands.length, 0);
});

test("marketplace-control CDP rejects non-Facebook origin, wrong tab, and unauthenticated work", async () => {
  const nonFacebook = makeHarness();
  const wrongOrigin = { ...nonFacebook.sender, tab: { ...nonFacebook.sender.tab, url: "https://www.mercari.com/sell/" } };
  assert.equal((await nonFacebook.api.handleMarketplaceControlRequest(nonFacebook.request, wrongOrigin)).error_code, "MARKETPLACE_CONTROL_ORIGIN_REJECTED");
  assert.equal(nonFacebook.counts.attach, 0);

  const wrongTab = makeHarness();
  assert.equal((await wrongTab.api.handleMarketplaceControlRequest(wrongTab.request, { ...wrongTab.sender, tab: { ...wrongTab.sender.tab, id: 18 } })).error_code, "MARKETPLACE_CONTROL_WRONG_TAB");
  assert.equal(wrongTab.counts.attach, 0);

  const noJob = makeHarness({ activeJob: { action: "DIAGNOSTIC" } });
  assert.equal((await noJob.api.handleMarketplaceControlRequest(noJob.request, noJob.sender)).error_code, "MARKETPLACE_CONTROL_NO_AUTHORIZED_CREATE");
  assert.equal(noJob.counts.attach, 0);

  const staleTabUrl = makeHarness({ currentTabUrl: "https://www.mercari.com/sell/" });
  assert.equal((await staleTabUrl.api.handleMarketplaceControlRequest(staleTabUrl.request, staleTabUrl.sender)).error_code, "MARKETPLACE_CONTROL_ORIGIN_REJECTED");
  assert.equal(staleTabUrl.counts.attach, 0);
});

test("marketplace-control CDP rejects invalid geometry and caller-supplied arbitrary coordinates", async () => {
  const missing = makeHarness();
  delete missing.request.target.width;
  assert.equal((await missing.api.handleMarketplaceControlRequest(missing.request, missing.sender)).error_code, "MARKETPLACE_CONTROL_TARGET_GEOMETRY_REJECTED");

  const arbitrary = makeHarness();
  arbitrary.request.target.x = 500;
  arbitrary.request.target.y = 450;
  arbitrary.request.command = "Runtime.evaluate";
  assert.equal((await arbitrary.api.handleMarketplaceControlRequest(arbitrary.request, arbitrary.sender)).error_code, "MARKETPLACE_CONTROL_TARGET_GEOMETRY_REJECTED");
  assert.equal(arbitrary.counts.attach, 0);
  assert.equal(arbitrary.counts.commands.length, 0);
});

test("marketplace-control CDP failure still releases the mouse and detaches exactly once", async () => {
  const h = makeHarness({ failCommand: "mousePressed" });
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.ok, false);
  assert.equal(result.error_code, "MARKETPLACE_CONTROL_INPUT_FAILED");
  assert.equal(h.counts.attach, 1);
  assert.equal(h.counts.detach, 1);
  assert.deepEqual(h.counts.commands.filter((item) => item.method === "Input.dispatchMouseEvent").map((item) => item.params.type), ["mouseMoved", "mousePressed", "mouseReleased"]);
});

test("marketplace-control authorization is one-use and an attached DevTools target is left alone", async () => {
  const replay = makeHarness();
  assert.equal((await replay.api.handleMarketplaceControlRequest(replay.request, replay.sender)).ok, true);
  assert.equal((await replay.api.handleMarketplaceControlRequest(replay.request, replay.sender)).error_code, "MARKETPLACE_CONTROL_AUTHORIZATION_INVALID_OR_USED");
  assert.equal(replay.counts.attach, 1);

  const devtools = makeHarness({ targets: [{ tabId: 17, attached: true }] });
  const blocked = await devtools.api.handleMarketplaceControlRequest(devtools.request, devtools.sender);
  assert.equal(blocked.error_code, "MARKETPLACE_CONTROL_DEBUGGER_ALREADY_ATTACHED");
  assert.equal(devtools.counts.attach, 0);
  assert.equal(devtools.counts.detach, 0);
});

test("description repair UPDATE gets exactly one trusted Save activation and rejects CREATE jobs", async () => {
  const h = makeHarness({
    currentTabUrl: "https://www.facebook.com/marketplace/item/FB-55/edit/",
    activeJob: {
      id: 901, action: "UPDATE", marketplace: "facebook", status: "SUBMITTING",
      browser_input_stage: "FACEBOOK_DESCRIPTION_REPAIR_SAVE", browser_input_used: false,
      external_listing_id: "FB-55", external_url: "https://www.facebook.com/marketplace/item/FB-55/",
      payload: { marketplace_payload: { repair_scope: "facebook_description_only", repair_expected_remote_id: "FB-55", repair_expected_remote_url: "https://www.facebook.com/marketplace/item/FB-55/" } },
    },
    ancestry: [
      { tag: "span", connected: true, label: "Save" },
      { tag: "button", connected: true, role: "button", label: "Save" },
    ],
  });
  h.request.expected_stage = "FACEBOOK_DESCRIPTION_REPAIR_SAVE";
  h.request.expected_transition = "DESCRIPTION_REPAIR_SAVE_ACCEPTED";
  h.request.expected_identity = { title: "Known item", price: 29.99 };
  h.request.target = { ...h.request.target, label: "Save", dialog_heading: null, repair_form_identity_verified: true };
  h.sender.tab.url = "https://www.facebook.com/marketplace/item/FB-55/edit/";
  const result = await h.api.handleMarketplaceControlRequest(h.request, h.sender);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.invocation_count, 1);
  assert.deepEqual(Array.from(result.commands), ["mouseMoved", "mousePressed", "mouseReleased"]);
  assert.equal(h.counts.attach, 1);
  assert.equal(h.counts.detach, 1);
  assert.equal(h.counts.commands.filter((item) => item.method === "Input.dispatchMouseEvent").length, 3);
  assert.equal(h.state.posterproActiveJob.browser_input_used, true);

  const create = makeHarness();
  create.request.expected_stage = "FACEBOOK_DESCRIPTION_REPAIR_SAVE";
  create.request.expected_transition = "DESCRIPTION_REPAIR_SAVE_ACCEPTED";
  create.request.target = { ...create.request.target, label: "Save", dialog_heading: null, repair_form_identity_verified: true };
  assert.equal((await create.api.handleMarketplaceControlRequest(create.request, create.sender)).error_code, "MARKETPLACE_CONTROL_NO_AUTHORIZED_DESCRIPTION_REPAIR");
  assert.equal(create.counts.attach, 0);
});
