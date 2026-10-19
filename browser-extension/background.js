const DEFAULT_SETTINGS = {
  posterproBaseUrl: "https://posterpro.sparkleserver.site",
  defaultMarketplace: "mercari",
};

const MARKETPLACE_HOST_HINTS = [
  { marketplace: "facebook", match: "facebook.com" },
  { marketplace: "mercari", match: "mercari.com" },
  { marketplace: "poshmark", match: "poshmark.com" },
  { marketplace: "etsy", match: "etsy.com" },
  { marketplace: "depop", match: "depop.com" },
  { marketplace: "whatnot", match: "whatnot.com" },
  { marketplace: "vinted", match: "vinted.com" },
  { marketplace: "offerup", match: "offerup.com" },
];

const EXTENSION_VERSION = "0.3.70";
const FACEBOOK_FORM_NO_PROGRESS_MS = 150000;
const FACEBOOK_FORM_MAX_DURATION_MS = 8 * 60 * 1000;
const FACEBOOK_FORM_CANCEL_DRAIN_MS = 15000;
const FACEBOOK_BUILD_ID = "posterpro-0.3.70-facebook-background-queue-focus-safety";
const activeFacebookFormProgress = new Map();
const CDP_CONTROL_POLICIES = {
  "facebook:FACEBOOK_SHIPPING_UPDATE": {
    origin: "https://www.facebook.com", path_prefix: "/marketplace/create/",
    content_build: FACEBOOK_BUILD_ID,
    label: "Update", role: "button", dialog_heading: "Change shipping method",
    expected_transition: "SHIPPING_DIALOG_CLOSED",
  },
  "facebook:FACEBOOK_DESCRIPTION_REPAIR_SAVE": {
    origin: "https://www.facebook.com", path_prefix: "/marketplace/",
    content_build: FACEBOOK_BUILD_ID,
    label: "Save", label_aliases: ["Save", "Update", "Save changes", "Update listing"], role: "button", dialog_heading: null,
    expected_transition: "DESCRIPTION_REPAIR_SAVE_ACCEPTED",
    repair_only: true,
  },
};
let queuePollActive = false;
let activeJobLeaseRenewal = false;
const marketplaceControlRequestsClaimed = new Set();
const marketplaceFocusLeases = new Map();

  async function probeActiveFacebookDescriptionDom() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  let url;
  try { url = new URL(String(tab?.url || "")); } catch (_) { url = null; }
  if (!tab?.id || !url || url.origin !== "https://www.facebook.com" || !/^\/marketplace\/create(?:\/|$)/i.test(url.pathname)) {
    return { ok: false, error_code: "FACEBOOK_DESCRIPTION_PROBE_WRONG_PAGE" };
  }
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["marketplace-adapters.js", "content.js"] });
  const identity = await chrome.tabs.sendMessage(tab.id, { action: "posterpro_content_script_identity" });
  if (identity?.content_script_version !== EXTENSION_VERSION || identity?.extension_version !== EXTENSION_VERSION
    || identity?.content_script_build_id !== FACEBOOK_BUILD_ID) {
    return { ok: false, error_code: "FACEBOOK_DESCRIPTION_PROBE_BUILD_MISMATCH", identity };
  }
  const response = await chrome.tabs.sendMessage(tab.id, { action: "posterpro_probe_facebook_description_dom" });
  const result = { ...response, captured_for_tab: tab.id, captured_at: new Date().toISOString() };
  await chrome.storage.local.set({ posterproFacebookDescriptionDomProbe: result });
  return result;
}

function validMarketplaceControlGeometry(target, policy) {
  const validLabel = Array.isArray(policy?.label_aliases) ? policy.label_aliases.includes(target?.label) : target?.label === policy?.label;
  if (!target || !validLabel || target.role !== policy.role
    || (policy.dialog_heading && target.dialog_heading !== policy.dialog_heading)
    || (policy.repair_only ? target.repair_form_identity_verified !== true : target.hit_target_inside !== true)
    || target.is_connected !== true || target.document_contains !== true
    || target.current_dialog_match !== true || target.enabled !== true
    || target.evidence_source !== "content-script-live-target") return false;
  const { x, y, left, top, width, height, viewport_width: vw, viewport_height: vh } = target;
  if (![x, y, left, top, width, height, vw, vh].every(Number.isFinite)) return false;
  if (width <= 0 || height <= 0 || width > 1000 || height > 500 || vw <= 0 || vh <= 0 || vw > 10000 || vh > 10000) return false;
  const tolerance = 0.75;
  return Math.abs(x - (left + width / 2)) <= tolerance
    && Math.abs(y - (top + height / 2)) <= tolerance
    && x >= 0 && y >= 0 && x < vw && y < vh
    && left >= 0 && top >= 0 && left + width <= vw && top + height <= vh;
}

function summarizeCoordinateDeltas(before, after) {
  const a = before || {};
  const b = after || {};
  const rectA = a.rect || { left: a.left, top: a.top, width: a.width, height: a.height };
  const rectB = b.rect || { left: b.left, top: b.top, width: b.width, height: b.height };
  const centerA = a.center || { x: a.x, y: a.y };
  const centerB = b.center || { x: b.x, y: b.y };
  const vpA = a.viewport_environment || {};
  const vpB = b.viewport_environment || {};
  const visualA = vpA.visual_viewport || {};
  const visualB = vpB.visual_viewport || {};
  const delta = (left, right) => Number.isFinite(left) && Number.isFinite(right) ? Number((right - left).toFixed(3)) : null;
  return {
    x: delta(centerA.x, centerB.x), y: delta(centerA.y, centerB.y),
    left: delta(rectA.left, rectB.left), top: delta(rectA.top, rectB.top),
    width: delta(rectA.width, rectB.width), height: delta(rectA.height, rectB.height),
    viewport_width: delta(vpA.inner_width, vpB.inner_width),
    viewport_height: delta(vpA.inner_height, vpB.inner_height),
    document_client_width: delta(vpA.document_client_width, vpB.document_client_width),
    document_client_height: delta(vpA.document_client_height, vpB.document_client_height),
    visual_viewport_width: delta(visualA.width, visualB.width),
    visual_viewport_height: delta(visualA.height, visualB.height),
    visual_viewport_scale: delta(visualA.scale, visualB.scale),
    visual_viewport_offset_left: delta(visualA.offset_left, visualB.offset_left),
    visual_viewport_offset_top: delta(visualA.offset_top, visualB.offset_top),
    device_pixel_ratio: delta(vpA.device_pixel_ratio, vpB.device_pixel_ratio),
    scroll_x: delta(vpA.scroll_x, vpB.scroll_x), scroll_y: delta(vpA.scroll_y, vpB.scroll_y),
  };
}

function marketplaceDomHitTestEvidence(hitTest, policy = null) {
  if (!hitTest?.ok || !Array.isArray(hitTest.ancestry)) return {
    cdp_exact_hit_is_update_text: false,
    cdp_connected_update_button_ancestor: false,
    cdp_dialog_ancestry_proven: false,
  };
  const rows = hitTest.ancestry;
  const targetLabels = policy?.label_aliases || [policy?.label || "Update"];
  return {
    cdp_exact_hit_is_update_text: rows[0]?.connected === true && /^update$/i.test(String(rows[0]?.label || "")),
    cdp_connected_update_button_ancestor: rows.some((row) => row.connected === true && row.role === "button" && /^update$/i.test(String(row.label || ""))),
    cdp_exact_hit_is_control_text: rows[0]?.connected === true && targetLabels.some((label) => String(rows[0]?.label || "").trim().toLowerCase() === String(label).toLowerCase()),
    cdp_connected_control_button_ancestor: rows.some((row) => row.connected === true && row.role === "button" && targetLabels.some((label) => String(row.label || "").trim().toLowerCase() === String(label).toLowerCase())),
    cdp_dialog_ancestry_proven: rows.some((row) => row.role === "dialog"
      && row.connected === true && /change shipping method/i.test(String(row.label || row.aria_label || "")))
  };
}

function marketplaceDomHitTestMatches(hitTest, target, contentHit, coordinate, policy = null) {
  if (policy?.repair_only) {
    const evidence = marketplaceDomHitTestEvidence(hitTest, policy);
    return target?.repair_form_identity_verified === true
      && target.is_connected === true && target.document_contains === true && target.enabled === true
      && contentHit?.ok === true && contentHit.target_inside_control === true
      && contentHit.repair_form_identity_verified === true
      && contentHit.activation_coordinate?.x === coordinate?.x
      && contentHit.activation_coordinate?.y === coordinate?.y
      && evidence.cdp_exact_hit_is_control_text === true
      && evidence.cdp_connected_control_button_ancestor === true;
  }
  if (!target?.hit_target_inside || target.is_connected !== true || target.document_contains !== true
    || target.current_dialog_match !== true || target.enabled !== true
    || target.role !== "button" || !/^update$/i.test(String(target.label || ""))) return false;
  const contentButton = contentHit?.update_target?.actionable_ancestor;
  const contentDialog = contentHit?.current_dialog;
  const sameCoordinate = contentHit?.activation_coordinate?.x === coordinate?.x
    && contentHit?.activation_coordinate?.y === coordinate?.y;
  const contentProvesCurrentUpdate = contentHit?.ok === true && contentHit?.target_inside_update === true
    && contentHit?.content_integer_hit_test?.target_inside_update === true
    && contentDialog?.role === "dialog" && contentDialog?.heading === "Change shipping method"
    && contentButton?.role === "button" && /^update$/i.test(String(contentButton?.text || ""))
    && sameCoordinate;
  const browserEvidence = marketplaceDomHitTestEvidence(hitTest);
  return contentProvesCurrentUpdate && browserEvidence.cdp_exact_hit_is_update_text
    && browserEvidence.cdp_connected_update_button_ancestor;
}

async function reResolveFacebookUpdateAfterForeground(tabId, nonce, expectedBuild) {
  return chrome.tabs.sendMessage(tabId, {
    action: "posterpro_resolve_facebook_update_target_after_foreground",
    authorization_nonce: nonce,
    expected_content_build_id: expectedBuild,
    expected_stage: "FACEBOOK_SHIPPING_UPDATE",
  });
}

async function reResolveFacebookDescriptionSaveAfterForeground(tabId, nonce, expectedBuild, expectedIdentity) {
  return chrome.tabs.sendMessage(tabId, {
    action: "posterpro_resolve_facebook_description_save_after_foreground",
    authorization_nonce: nonce,
    expected_content_build_id: expectedBuild,
    expected_stage: "FACEBOOK_DESCRIPTION_REPAIR_SAVE",
    expected_identity: expectedIdentity,
  });
}

async function describeBrowserHitNode(issueCommand, x, y) {
  if (!Number.isInteger(x) || !Number.isInteger(y) || x < 0 || y < 0 || x > 2147483647 || y > 2147483647) {
    return { ok: false, error_code: "MARKETPLACE_CONTROL_ACTIVATION_COORDINATE_INVALID", raw_node: null, ancestry: [] };
  }
  const location = await issueCommand("DOM.getNodeForLocation", {
    x, y, includeUserAgentShadowDOM: true,
  }, "browserHitTest");
  if (!location || (!Number.isInteger(location.nodeId) && !Number.isInteger(location.backendNodeId))) {
    return { ok: false, error_code: "CDP_HIT_TEST_NODE_UNAVAILABLE", raw_node: location || null, ancestry: [] };
  }
  const rawNode = { node_id: Number.isInteger(location.nodeId) ? location.nodeId : null,
    backend_node_id: Number.isInteger(location.backendNodeId) ? location.backendNodeId : null };
  let objectId = null;
  try {
    const resolved = await issueCommand("DOM.resolveNode", {
      ...(Number.isInteger(location.nodeId) ? { nodeId: location.nodeId } : { backendNodeId: location.backendNodeId }),
      objectGroup: "posterpro-facebook-hit-ancestry",
    }, "resolveHitNode");
    objectId = resolved?.object?.objectId || null;
    if (typeof objectId !== "string" || !objectId) {
      return { ok: false, error_code: "CDP_HIT_NODE_ANCESTRY_UNAVAILABLE", raw_node: rawNode,
        frame_id: location.frameId || null, ancestry: [], ancestry_source: "Runtime.callFunctionOn" };
    }
    // Fixed, read-only function. It walks only the exact hit node and at most
    // eight ancestors, returning a small structural summary (never page data).
    const functionDeclaration = `function(maxDepth) {
      const rows = [];
      let node = this && this.nodeType === 1 ? this : this && this.parentElement;
      const clean = value => String(value || '').slice(0, 500).replace(/\\s+/g, ' ').trim().slice(0, 100);
      for (let i = 0; node && i < Math.min(8, Math.max(1, Number(maxDepth) || 8)); i += 1, node = node.parentElement) {
        rows.push({ tag: String(node.tagName || '').slice(0, 24).toLowerCase(),
          role: clean(node.getAttribute && node.getAttribute('role')) || null,
          aria_label: clean(node.getAttribute && node.getAttribute('aria-label')) || null,
          title: clean(node.getAttribute && node.getAttribute('title')) || null,
          label: clean(node.innerText || node.textContent), connected: node.isConnected === true });
      }
      return rows;
    }`;
    const evaluated = await issueCommand("Runtime.callFunctionOn", {
      objectId, functionDeclaration, arguments: [{ value: 8 }], returnByValue: true, awaitPromise: false,
    }, "inspectHitNodeAncestry");
    const ancestry = Array.isArray(evaluated?.result?.value) ? evaluated.result.value : [];
    if (evaluated?.exceptionDetails || ancestry.length === 0) {
      return { ok: false, error_code: "CDP_HIT_NODE_ANCESTRY_UNAVAILABLE", raw_node: rawNode,
        frame_id: location.frameId || null, ancestry, ancestry_source: "Runtime.callFunctionOn" };
    }
    return { ok: true, raw_node: rawNode, frame_id: location.frameId || null,
      ancestry, ancestry_source: "Runtime.callFunctionOn", max_ancestors: 8 };
  } catch (error) {
    return { ok: false, error_code: "CDP_HIT_NODE_ANCESTRY_UNAVAILABLE",
      error: String(error?.message || error).slice(0, 180), raw_node: rawNode,
      frame_id: location.frameId || null, ancestry: [], ancestry_source: "Runtime.callFunctionOn" };
  } finally {
    if (objectId) {
      try { await issueCommand("Runtime.releaseObject", { objectId }, "releaseHitNodeObject"); }
      catch (_) { /* bounded diagnostic object cleanup is best effort */ }
    }
  }
}

function makeCanonicalActivationPoint(target) {
  const rect = target?.rect || {
    left: target?.left, top: target?.top,
    right: Number(target?.left) + Number(target?.width),
    bottom: Number(target?.top) + Number(target?.height),
  };
  const viewport = target?.viewport_environment || {};
  const values = [target?.x, target?.y, rect.left, rect.top, rect.right, rect.bottom,
    viewport.inner_width, viewport.inner_height];
  if (!values.every(Number.isFinite) || rect.right <= rect.left || rect.bottom <= rect.top) {
    return { ok: false, error_code: "MARKETPLACE_CONTROL_ACTIVATION_RECT_INVALID" };
  }
  // CSS hit-test coordinates address half-open element boxes. Keep the nearest
  // rounded center when possible; clamp only to the nearest integer interior
  // coordinate if rounding lands outside the validated rectangle.
  const minX = Math.ceil(rect.left);
  const maxX = Math.ceil(rect.right) - 1;
  const minY = Math.ceil(rect.top);
  const maxY = Math.ceil(rect.bottom) - 1;
  if (minX > maxX || minY > maxY) return { ok: false, error_code: "MARKETPLACE_CONTROL_NO_INTEGER_INTERIOR_POINT" };
  const roundedX = Math.round(target.x);
  const roundedY = Math.round(target.y);
  const activationX = Math.max(minX, Math.min(maxX, roundedX));
  const activationY = Math.max(minY, Math.min(maxY, roundedY));
  const roundingMethod = activationX === roundedX && activationY === roundedY
    ? "Math.round_stabilized_center" : "nearest_integer_clamped_inside_stabilized_rect";
  const int32 = (value) => Number.isInteger(value) && value >= 0 && value <= 2147483647;
  const insideViewport = activationX < viewport.inner_width && activationY < viewport.inner_height;
  const insideRect = activationX >= rect.left && activationX < rect.right
    && activationY >= rect.top && activationY < rect.bottom;
  if (!int32(activationX) || !int32(activationY) || !insideViewport || !insideRect) {
    return { ok: false, error_code: "MARKETPLACE_CONTROL_ACTIVATION_COORDINATE_INVALID",
      activation_coordinate: { x: activationX, y: activationY, rounding_method: roundingMethod } };
  }
  return { ok: true, activation_coordinate: { x: activationX, y: activationY, rounding_method: roundingMethod } };
}

async function readMarketplaceTabContext(tabId) {
  const tab = await chrome.tabs.get(tabId);
  const window = Number.isInteger(tab?.windowId) ? await chrome.windows.get(tab.windowId) : null;
  const activeTabs = Number.isInteger(tab?.windowId)
    ? await chrome.tabs.query({ active: true, windowId: tab.windowId }) : [];
  let zoom = null;
  try { zoom = await chrome.tabs.getZoom(tabId); } catch (_) { /* unavailable in some Chromium contexts */ }
  let parsed = null;
  try { parsed = new URL(String(tab?.url || "")); } catch (_) { /* URL is separately policy-checked */ }
  return {
    tab_id: Number(tab?.id), active: Boolean(tab?.active), highlighted: Boolean(tab?.highlighted),
    window_id: Number.isInteger(tab?.windowId) ? tab.windowId : null, status: String(tab?.status || "unknown"),
    url: parsed ? { origin: parsed.origin, path: parsed.pathname.slice(0, 160) } : null,
    window_focused: Boolean(window?.focused), window_state: String(window?.state || "unknown"),
    active_tab_id: Number.isInteger(activeTabs?.[0]?.id) ? activeTabs[0].id : null,
    zoom_factor: Number.isFinite(zoom) ? zoom : null,
  };
}

async function foregroundMarketplaceTab(tabId) {
  // Marketplace automation is background-only.  Never activate a tab or
  // focus a Chrome window: callers must treat a non-foreground control as an
  // operator-visible prerequisite rather than changing desktop focus.
  const context = await readMarketplaceTabContext(tabId);
  return { before: context, after: context, changed: false };
}

async function restoreMarketplaceFocus(foreground) {
  // Kept for protocol compatibility with existing one-use control leases.
  // No focus was ever taken, therefore there is nothing to restore.
  return { attempted: false, restored: true, focus_changes_performed: false };
}

async function releaseMarketplaceFocusLease(request, sender) {
  const leaseId = String(request?.focus_lease_id || "");
  const tabId = Number(sender?.tab?.id);
  const lease = marketplaceFocusLeases.get(leaseId);
  if (!lease || !Number.isInteger(tabId) || lease.tab_id !== tabId) {
    return { ok: false, error_code: "MARKETPLACE_FOCUS_LEASE_INVALID" };
  }
  marketplaceFocusLeases.delete(leaseId);
  const { posterproActiveJob } = await chrome.storage.local.get(["posterproActiveJob"]);
  if (!posterproActiveJob || posterproActiveJob.id !== lease.job_id
    || posterproActiveJob.browser_input_nonce !== lease.authorization_nonce) {
    return { ok: false, error_code: "MARKETPLACE_FOCUS_LEASE_AUTHORIZATION_INVALID" };
  }
  const focusRestore = await restoreMarketplaceFocus(lease.foreground);
  let contextAfter = null;
  try { contextAfter = await readMarketplaceTabContext(tabId); } catch (_) { /* tab may have closed */ }
  return { ok: true, focus_restore: focusRestore, context_after_restore: contextAfter };
}

async function activateValidatedMarketplaceControl(request, sender) {
  const marketplace = String(request?.marketplace || "").toLowerCase();
  const expectedStage = String(request?.expected_stage || "");
  const expectedTransition = String(request?.expected_transition || "");
  const policy = CDP_CONTROL_POLICIES[`${marketplace}:${expectedStage}`];
  if (!policy || expectedTransition !== policy.expected_transition) return { ok: false, error_code: "MARKETPLACE_CONTROL_POLICY_REJECTED" };
  const target = request.target && typeof request.target === "object" ? request.target : null;
  const tabId = Number(sender?.tab?.id);
  const senderUrl = String(sender?.tab?.url || sender?.url || "");
  if (!Number.isInteger(tabId) || tabId < 0) return { ok: false, error_code: "MARKETPLACE_CONTROL_TAB_REQUIRED" };
  let parsed;
  try { parsed = new URL(senderUrl); } catch (_) { return { ok: false, error_code: "MARKETPLACE_CONTROL_ORIGIN_REJECTED" }; }
  if (`${parsed.protocol}//${parsed.hostname}` !== policy.origin || !parsed.pathname.startsWith(policy.path_prefix)
    || request.expected_origin !== policy.origin) {
    return { ok: false, error_code: "MARKETPLACE_CONTROL_ORIGIN_REJECTED" };
  }
  if (!validMarketplaceControlGeometry(target, policy)) return { ok: false, error_code: "MARKETPLACE_CONTROL_TARGET_GEOMETRY_REJECTED" };
  if (target.content_script_build_id !== policy.content_build) return { ok: false, error_code: "MARKETPLACE_CONTROL_CONTENT_BUILD_REJECTED" };

  const { posterproActiveJob } = await chrome.storage.local.get(["posterproActiveJob"]);
  const job = posterproActiveJob && typeof posterproActiveJob === "object" ? posterproActiveJob : null;
  const jobAction = String(job?.action || "").toUpperCase();
  const isAuthorizedRepair = Boolean(policy.repair_only && jobAction === "UPDATE"
    && job?.payload?.marketplace_payload?.repair_scope === "facebook_description_only"
    && job?.external_listing_id === job?.payload?.marketplace_payload?.repair_expected_remote_id
    && job?.external_url === job?.payload?.marketplace_payload?.repair_expected_remote_url);
  if (!job || String(job.marketplace || "").toLowerCase() !== "facebook"
    || (policy.repair_only ? !isAuthorizedRepair : jobAction !== "CREATE")) {
    return { ok: false, error_code: policy.repair_only ? "MARKETPLACE_CONTROL_NO_AUTHORIZED_DESCRIPTION_REPAIR" : "MARKETPLACE_CONTROL_NO_AUTHORIZED_CREATE" };
  }
  if (Number(job.tab_id) !== tabId) return { ok: false, error_code: "MARKETPLACE_CONTROL_WRONG_TAB" };
  if (!job.browser_input_nonce || job.browser_input_nonce !== request.authorization_nonce || job.browser_input_used === true) {
    return { ok: false, error_code: "MARKETPLACE_CONTROL_AUTHORIZATION_INVALID_OR_USED" };
  }
  if (job.browser_input_content_build_id !== target.content_script_build_id) {
    return { ok: false, error_code: "MARKETPLACE_CONTROL_CONTENT_BUILD_REJECTED" };
  }
  if (String(job.status || "").toUpperCase() !== "SUBMITTING" || job.browser_input_stage !== expectedStage) {
    return { ok: false, error_code: "MARKETPLACE_CONTROL_STAGE_REJECTED" };
  }
  const currentTab = await chrome.tabs.get(tabId);
  let currentUrl;
  try { currentUrl = new URL(String(currentTab?.url || "")); } catch (_) { return { ok: false, error_code: "MARKETPLACE_CONTROL_ORIGIN_REJECTED" }; }
  if (`${currentUrl.protocol}//${currentUrl.hostname}` !== policy.origin || !currentUrl.pathname.startsWith(policy.path_prefix)) {
    return { ok: false, error_code: "MARKETPLACE_CONTROL_ORIGIN_REJECTED" };
  }

  // Consume the one-use authorization before changing focus. Any failed
  // remeasurement/hit test is diagnostic evidence, never permission to replay.
  await chrome.storage.local.set({ posterproActiveJob: { ...job, browser_input_used: true } });
  if (policy.repair_only) {
    await setJobState(job.id, "SUBMITTING", {
      result: { description_repair: { state: "FACEBOOK_DESCRIPTION_REPAIR_IN_PROGRESS", save_attempt_count: 1, one_use_authorization_consumed_at: new Date().toISOString() } },
    });
  }
  const targetRef = { tabId };
  const targets = await chrome.debugger.getTargets();
  if (targets.some((item) => Number(item.tabId) === tabId && item.attached)) {
    return { ok: false, error_code: "MARKETPLACE_CONTROL_DEBUGGER_ALREADY_ATTACHED", invocation_count: 1 };
  }

  let foreground = null;
  let attached = false;
  let attachResult = { status: "not_attempted" };
  let focusError = null;
  let activationError = null;
  let postTarget = null;
  let activationCoordinate = null;
  let contentIntegerHitTest = null;
  let coordinateDeltas = null;
  let protocolLayout = null;
  let browserHitTest = null;
  let contentBrowserHitTestAgree = null;
  let releaseAttempted = false;
  const commandResults = [];
  const issueCommand = async (method, params, label) => {
    const record = { label, method, status: "sent", started_at_ms: Date.now() };
    commandResults.push(record);
    try {
      const result = await chrome.debugger.sendCommand(targetRef, method, params);
      record.status = "resolved";
      record.completed_at_ms = Date.now();
      return result;
    } catch (error) {
      record.status = "rejected";
      record.completed_at_ms = Date.now();
      record.error = String(error?.message || error).slice(0, 240);
      throw error;
    }
  };
  const send = async (type, liveTarget) => issueCommand("Input.dispatchMouseEvent", {
    type, x: liveTarget.x, y: liveTarget.y, button: type === "mouseMoved" ? "none" : "left",
    ...(type === "mousePressed" ? { buttons: 1, clickCount: 1 } : { buttons: 0 }),
  }, type);

  try {
    // Never steal desktop/tab focus for background automation. CDP control is
    // allowed only when the operator has already put the authenticated
    // marketplace tab in the foreground; otherwise preserve the browser state
    // and return a durable, non-submitting diagnostic.
    const existingContext = await readMarketplaceTabContext(tabId);
    if (!existingContext.window_focused || !existingContext.active) {
      activationError = "MARKETPLACE_CONTROL_OPERATOR_FOCUS_REQUIRED";
    }
    foreground = { before: existingContext, after: existingContext, changed: false };
    const [currentAuthorizedState, foregroundContext] = await Promise.all([
      chrome.storage.local.get(["posterproActiveJob"]), readMarketplaceTabContext(tabId),
    ]);
    const foregroundJob = currentAuthorizedState.posterproActiveJob;
    if (activationError || !foregroundContext.active || !foregroundContext.window_focused
      || foregroundContext.tab_id !== tabId || foregroundContext.url?.origin !== policy.origin
      || !foregroundContext.url?.path?.startsWith(policy.path_prefix)
      || !foregroundJob || foregroundJob.id !== job.id || foregroundJob.tab_id !== tabId
      || foregroundJob.browser_input_nonce !== request.authorization_nonce
      || foregroundJob.browser_input_stage !== expectedStage || foregroundJob.browser_input_used !== true
      || String(foregroundJob.status || "").toUpperCase() !== "SUBMITTING") {
      activationError = activationError || "MARKETPLACE_AUTH_OR_FOREGROUND_CHANGED_BEFORE_REMEASURE";
    }
    postTarget = activationError ? { ok: false, error_code: activationError }
      : policy.repair_only
        ? await reResolveFacebookDescriptionSaveAfterForeground(tabId, request.authorization_nonce, policy.content_build, request.expected_identity)
        : await reResolveFacebookUpdateAfterForeground(tabId, request.authorization_nonce, policy.content_build);
    if (!postTarget?.ok || !validMarketplaceControlGeometry(postTarget.target, policy)
      || postTarget.target?.content_script_build_id !== policy.content_build) {
      activationError = postTarget?.error_code || "MARKETPLACE_CONTROL_POST_FOREGROUND_TARGET_INVALID";
    } else {
      coordinateDeltas = summarizeCoordinateDeltas(target, postTarget.target);
      const canonical = makeCanonicalActivationPoint(postTarget.target);
      if (!canonical.ok) activationError = canonical.error_code;
      else activationCoordinate = canonical.activation_coordinate;
    }

    if (!activationError) {
      contentIntegerHitTest = await chrome.tabs.sendMessage(tabId, {
        action: policy.repair_only ? "posterpro_validate_facebook_description_save_activation_point" : "posterpro_validate_facebook_update_activation_point",
        authorization_nonce: request.authorization_nonce,
        expected_stage: expectedStage,
        expected_content_build_id: policy.content_build,
        expected_identity: request.expected_identity,
        activation_coordinate: activationCoordinate,
      });
      if (!contentIntegerHitTest?.ok || (policy.repair_only ? contentIntegerHitTest.target_inside_control !== true : contentIntegerHitTest.target_inside_update !== true)) {
        activationError = contentIntegerHitTest?.error_code || "MARKETPLACE_CONTENT_INTEGER_HIT_TEST_REJECTED";
      }
    }

    if (!activationError) {
      attachResult = { status: "sent" };
      await chrome.debugger.attach(targetRef, "1.3");
      attached = true;
      attachResult.status = "resolved";
      const metrics = await issueCommand("Page.getLayoutMetrics", {}, "layoutMetrics");
      const pick = (value) => value && typeof value === "object" ? {
        pageX: Number.isFinite(value.pageX) ? value.pageX : null,
        pageY: Number.isFinite(value.pageY) ? value.pageY : null,
        offsetX: Number.isFinite(value.offsetX) ? value.offsetX : null,
        offsetY: Number.isFinite(value.offsetY) ? value.offsetY : null,
        clientWidth: Number.isFinite(value.clientWidth) ? value.clientWidth : null,
        clientHeight: Number.isFinite(value.clientHeight) ? value.clientHeight : null,
        scale: Number.isFinite(value.scale) ? value.scale : null,
      } : null;
      protocolLayout = {
        cssLayoutViewport: pick(metrics?.cssLayoutViewport), cssVisualViewport: pick(metrics?.cssVisualViewport),
        pageScaleFactor: Number.isFinite(metrics?.pageScaleFactor) ? metrics.pageScaleFactor : null,
        protocolLayoutViewport: pick(metrics?.layoutViewport), protocolVisualViewport: pick(metrics?.visualViewport),
      };
      await issueCommand("DOM.enable", {}, "domEnable");
      browserHitTest = await describeBrowserHitNode(issueCommand, activationCoordinate.x, activationCoordinate.y);
      if (!browserHitTest?.ok) {
        activationError = browserHitTest?.error_code || "CDP_BROWSER_HIT_TEST_UNAVAILABLE";
      } else {
        browserHitTest = { ...browserHitTest, ...marketplaceDomHitTestEvidence(browserHitTest, policy) };
        contentBrowserHitTestAgree = marketplaceDomHitTestMatches(
          browserHitTest, postTarget.target, contentIntegerHitTest, activationCoordinate, policy,
        );
        if (!contentBrowserHitTestAgree) {
          activationError = "MARKETPLACE_CONTENT_BROWSER_HIT_TEST_DISAGREE";
        } else {
          // Every final page/CDP check and all three input commands share the
          // same validated post-foreground integer CSS-pixel coordinate.
          const liveTarget = { ...postTarget.target, ...activationCoordinate };
          await send("mouseMoved", liveTarget);
          releaseAttempted = true;
          await send("mousePressed", liveTarget);
        }
      }
    }
  } catch (error) {
    activationError = String(error?.message || error).slice(0, 240);
    if (attachResult.status === "sent") { attachResult.status = "rejected"; attachResult.error = activationError; }
    if (!foreground) focusError = activationError;
  } finally {
    if (attached && releaseAttempted) {
      try { await send("mouseReleased", { ...postTarget.target, ...activationCoordinate }); }
      catch (error) { activationError = activationError || String(error?.message || error).slice(0, 240); }
    }
    if (attached) {
      try { await chrome.debugger.detach(targetRef); }
      catch (error) { activationError = activationError || String(error?.message || error).slice(0, 240); }
    }
  }
  let focusLease = null;
  let focusRestore = { attempted: false, restored: false, deferred: false };
  const commandsAccepted = ["mouseMoved", "mousePressed", "mouseReleased"].every((name) => commandResults.some((item) => item.label === name && item.status === "resolved"));
  if (!activationError && commandsAccepted && contentBrowserHitTestAgree) {
    const focusLeaseId = `facebook:${job.id}:${tabId}:${Date.now()}:${Math.random().toString(36).slice(2, 10)}`;
    focusLease = {
      id: focusLeaseId, tab_id: tabId, job_id: job.id,
      authorization_nonce: request.authorization_nonce, foreground,
      created_at: new Date().toISOString(),
    };
    marketplaceFocusLeases.set(focusLeaseId, focusLease);
    focusRestore = { attempted: false, restored: false, deferred: true };
  } else {
    focusRestore = await restoreMarketplaceFocus(foreground);
  }
  let contextAfter = null;
  try { contextAfter = await readMarketplaceTabContext(tabId); } catch (_) { /* tab may have closed */ }
  return {
    ok: !activationError && commandsAccepted && contentBrowserHitTestAgree,
    activation_method: "chrome_debugger_cdp_input", invocation_count: 1,
    commands: commandResults.filter((item) => item.method === "Input.dispatchMouseEvent").map((item) => item.label),
    command_results: commandResults, attach_result: attachResult,
    browser_context: {
      before: foreground?.before || null, foreground: foreground?.after || null, after_restore: contextAfter,
      focused_for_input: Boolean(foreground?.after?.window_focused && foreground?.after?.active),
      focus_changed: Boolean(foreground?.changed), focus_error: focusError,
      focus_restore: focusRestore, focus_lease_id: focusLease?.id || null,
      focus_lease_held: Boolean(focusLease),
    },
    pre_foreground_target: target, post_foreground_target: postTarget?.target || null,
    activation_coordinate: activationCoordinate,
    content_integer_hit_test: contentIntegerHitTest,
    post_foreground_samples: postTarget?.stability_samples || [], coordinate_deltas: coordinateDeltas,
    protocol_layout_metrics: protocolLayout, browser_hit_test: browserHitTest,
    content_browser_hit_test_agree: contentBrowserHitTestAgree,
    cdp_dialog_ancestry_proven: browserHitTest?.cdp_dialog_ancestry_proven ?? null,
    coordinate_space: "main_frame_viewport_css_pixels", attached, detached: attached,
    error_code: activationError ? (activationError === "MARKETPLACE_CONTENT_BROWSER_HIT_TEST_DISAGREE"
      ? activationError : contentIntegerHitTest?.error_code || postTarget?.error_code
        || browserHitTest?.error_code || (activationError.includes("Invalid parameters") ? "CDP_BROWSER_HIT_TEST_COMMAND_REJECTED" : "MARKETPLACE_CONTROL_INPUT_FAILED")) : null,
    error: activationError,
  };
}

async function handleMarketplaceControlRequest(message, sender) {
  if (!message || message.action !== "posterpro_marketplace_control_activation") return { ok: false, error_code: "MARKETPLACE_CONTROL_ACTION_REJECTED" };
  if (sender?.id !== chrome.runtime.id || Number(sender?.frameId || 0) !== 0) return { ok: false, error_code: "MARKETPLACE_CONTROL_SENDER_REJECTED" };
  const { posterproActiveJob } = await chrome.storage.local.get(["posterproActiveJob"]);
  if (!posterproActiveJob || !message.authorization_nonce || posterproActiveJob.browser_input_nonce !== message.authorization_nonce) {
    return { ok: false, error_code: "MARKETPLACE_CONTROL_AUTHORIZATION_INVALID_OR_USED" };
  }
  const requestKey = `${posterproActiveJob.id}:${message.authorization_nonce}`;
  if (marketplaceControlRequestsClaimed.has(requestKey) || posterproActiveJob.browser_input_used === true) {
    return { ok: false, error_code: "MARKETPLACE_CONTROL_AUTHORIZATION_INVALID_OR_USED" };
  }
  marketplaceControlRequestsClaimed.add(requestKey);
  return activateValidatedMarketplaceControl(message, sender);
}

async function beginFacebookDescriptionRepairSave(message, sender) {
  const tabId = Number(sender?.tab?.id);
  const { posterproActiveJob } = await chrome.storage.local.get(["posterproActiveJob"]);
  const job = posterproActiveJob;
  const payload = job?.payload?.marketplace_payload || {};
  if (!job || String(job.marketplace || "").toLowerCase() !== "facebook"
    || String(job.action || "").toUpperCase() !== "UPDATE"
    || payload.repair_scope !== "facebook_description_only"
    || Number(job.tab_id) !== tabId
    || !message?.authorization_nonce || job.browser_input_nonce !== message.authorization_nonce
    || job.browser_input_used === true
    || String(job.status || "").toUpperCase() !== "FORM_FILLING"
    || String(message.external_listing_id || "") !== String(job.external_listing_id || "")
    || String(message.external_url || "") !== String(job.external_url || "")) {
    return { ok: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_SAVE_NOT_AUTHORIZED" };
  }
  await setJobState(job.id, "SUBMITTING", {
    result: { description_repair: { state: "FACEBOOK_DESCRIPTION_REPAIR_IN_PROGRESS", save_authorized: true, save_count: 0 } },
  });
  await chrome.storage.local.set({ posterproActiveJob: { ...job, status: "SUBMITTING", browser_input_stage: "FACEBOOK_DESCRIPTION_REPAIR_SAVE", browser_input_used: false } });
  return { ok: true, job_id: job.id, stage: "FACEBOOK_DESCRIPTION_REPAIR_SAVE" };
}

function apiRoot(baseUrl) {
  const root = String(baseUrl || "").trim().replace(/\/+$/, "");
  return root.endsWith("/api") ? root : `${root}/api`;
}

async function getAgentSettings() {
  const settings = await loadSettings();
  const local = await chrome.storage.local.get(["posterproDeviceToken", "posterproActiveJob"]);
  return { api: apiRoot(settings.posterproBaseUrl), token: local.posterproDeviceToken || "", activeJob: local.posterproActiveJob || null };
}

// Keep assisted automation out of the operator's active workspace.  Reuse one
// dedicated inactive tab instead of opening a new visible tab for every job.
function safeExecutionUrl(value) {
  try {
    const parsed = new URL(String(value || ""));
    return { origin: parsed.origin, path: parsed.pathname.slice(0, 240) };
  } catch (_) { return null; }
}

function makeExecutionDiagnostic() {
  return {
    background_version: chrome.runtime.getManifest?.().version || EXTENSION_VERSION,
    started_at: new Date().toISOString(),
    stage: "JOB_CLAIMED",
    last_successful_stage: "JOB_CLAIMED",
    tab_id: null,
    requested_url: null,
    page_url: null,
    document_ready_state: null,
    events: [],
  };
}

async function executionStage(trace, stage, errorCode, operation, details = {}) {
  trace.stage = stage;
  const event = { stage, status: "started", at: new Date().toISOString(), ...details };
  trace.events.push(event);
  trace.events = trace.events.slice(-20);
  await chrome.storage.local.set({ posterproLastExecutionDiagnostic: trace }).catch(() => {});
  try {
    const value = await operation();
    event.status = "succeeded";
    event.completed_at = new Date().toISOString();
    trace.last_successful_stage = stage;
    await chrome.storage.local.set({ posterproLastExecutionDiagnostic: trace }).catch(() => {});
    return value;
  } catch (cause) {
    const safeMessage = String(cause?.message || cause || "Unknown Chrome execution error").slice(0, 600);
    event.status = "failed";
    event.completed_at = new Date().toISOString();
    event.error_name = String(cause?.name || "Error").slice(0, 80);
    event.error_message = safeMessage;
    const resolvedErrorCode = cause?.code || errorCode;
    trace.error_code = resolvedErrorCode;
    trace.error_name = event.error_name;
    trace.error_message = safeMessage;
    trace.failed_at = event.completed_at;
    await chrome.storage.local.set({ posterproLastExecutionDiagnostic: trace }).catch(() => {});
    const error = new Error(safeMessage);
    error.code = resolvedErrorCode;
    error.executionDiagnostic = trace;
    throw error;
  }
}

async function retryTabEdit(operation, attempts = 4) {
  let lastError;
  const waits = [100, 250, 500];
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try { return await operation(); }
    catch (error) {
      lastError = error;
      if (!/tabs cannot be edited right now|user may be dragging a tab/i.test(String(error?.message || error)) || attempt >= attempts - 1) throw error;
      await new Promise((resolve) => setTimeout(resolve, waits[attempt] || 500));
    }
  }
  throw lastError;
}

async function openAutomationTab(url, trace = null, { fresh = false } = {}) {
  if (trace) trace.requested_url = safeExecutionUrl(url);
  const stored = trace
    ? await executionStage(trace, "AUTOMATION_TAB_ID_LOOKUP", "AUTOMATION_TAB_NOT_FOUND", () => chrome.storage.local.get(["posterproAutomationTabId"]))
    : await chrome.storage.local.get(["posterproAutomationTabId"]);
  const tabId = Number(stored.posterproAutomationTabId || 0);
  if (fresh) {
    if (tabId) await rememberAutomationTab(tabId);
    const tab = trace
      ? await executionStage(trace, "AUTOMATION_TAB_CREATE_FRESH", "AUTOMATION_TAB_CREATE_FAILED", () => chrome.tabs.create({ url, active: false }), { requested_url: safeExecutionUrl(url), replaced_tab_id: tabId || null })
      : await chrome.tabs.create({ url, active: false });
    if (trace) trace.tab_id = tab.id;
    await rememberAutomationTab(tab.id);
    await chrome.storage.local.set({ posterproAutomationTabId: tab.id });
    return { ...tab, previous_automation_tab_id: tabId && tabId !== Number(tab.id) ? tabId : null };
  }
  if (tabId) {
    let existing = null;
    try { existing = await chrome.tabs.get(tabId); }
    catch (error) {
      if (!/no tab with id|tab not found/i.test(String(error?.message || error))) throw Object.assign(new Error(String(error?.message || error)), { code: "AUTOMATION_TAB_NOT_FOUND" });
    }
    if (existing?.id) {
      await rememberAutomationTab(existing.id);
      if (trace) trace.tab_id = existing.id;
      if (existing.url !== url) {
        if (trace) await executionStage(trace, "AUTOMATION_TAB_UPDATE", "AUTOMATION_TAB_EDIT_FAILED", () => retryTabEdit(() => chrome.tabs.update(existing.id, { url, active: false })), { tab_id: existing.id, page_url: safeExecutionUrl(existing.url), requested_url: safeExecutionUrl(url) });
        else await retryTabEdit(() => chrome.tabs.update(existing.id, { url, active: false }));
        try { existing = await chrome.tabs.get(existing.id); } catch (_) { /* load waiter will report tab loss */ }
      }
      return existing;
    }
  }
  const tab = trace
    ? await executionStage(trace, "AUTOMATION_TAB_CREATE", "AUTOMATION_TAB_CREATE_FAILED", () => chrome.tabs.create({ url, active: false }), { requested_url: safeExecutionUrl(url) })
    : await chrome.tabs.create({ url, active: false });
  if (trace) trace.tab_id = tab.id;
  await rememberAutomationTab(tab.id);
  await chrome.storage.local.set({ posterproAutomationTabId: tab.id });
  return tab;
}

async function rememberAutomationTab(tabId, remove = false) {
  const normalized = Number(tabId);
  if (!Number.isInteger(normalized) || normalized <= 0) return;
  const stored = await chrome.storage.local.get(["posterproAutomationTabIds"]);
  const known = Array.isArray(stored.posterproAutomationTabIds) ? stored.posterproAutomationTabIds.map(Number).filter((id) => Number.isInteger(id) && id > 0) : [];
  const next = remove ? known.filter((id) => id !== normalized) : [...new Set([...known, normalized])].slice(-80);
  await chrome.storage.local.set({ posterproAutomationTabIds: next });
}

async function facebookTabHygiene() {
  const [tabs, stored] = await Promise.all([
    chrome.tabs.query({}), chrome.storage.local.get(["posterproAutomationTabId", "posterproAutomationTabIds"]),
  ]);
  const facebookTabs = (tabs || []).filter((tab) => {
    try { return new URL(String(tab.url || tab.pendingUrl || "")).hostname.toLowerCase().endsWith("facebook.com"); }
    catch (_) { return false; }
  });
  const createTabs = facebookTabs.filter((tab) => {
    try { return /^\/marketplace\/create(?:\/|$)/i.test(new URL(String(tab.url || tab.pendingUrl || "")).pathname); }
    catch (_) { return false; }
  });
  const storedId = Number(stored.posterproAutomationTabId || 0) || null;
  const ownedIds = new Set((Array.isArray(stored.posterproAutomationTabIds) ? stored.posterproAutomationTabIds : []).map(Number).filter((id) => Number.isInteger(id) && id > 0));
  if (storedId) ownedIds.add(storedId);
  const ownedFacebookTabs = facebookTabs.filter((tab) => ownedIds.has(Number(tab.id)));
  const orphanCreateTabs = createTabs.filter((tab) => ownedIds.has(Number(tab.id)) && Number(tab.id) !== storedId);
  const untrackedCreateTabs = createTabs.filter((tab) => !ownedIds.has(Number(tab.id)));
  return {
    total_facebook_tabs: facebookTabs.length,
    total_marketplace_create_tabs: createTabs.length,
    posterpro_automation_tabs: ownedFacebookTabs.length,
    orphan_create_tabs: orphanCreateTabs.length,
    untracked_create_tabs: untrackedCreateTabs.length,
    stored_automation_tab_id: storedId,
    current_probe_or_automation_tab_ids: createTabs.map((tab) => Number(tab.id)).slice(0, 20),
  };
}

async function closeAutomationTab(tabId) {
  const stored = await chrome.storage.local.get(["posterproAutomationTabId"]);
  if (Number(stored.posterproAutomationTabId || 0) !== Number(tabId || 0)) return;
  try { await chrome.tabs.remove(tabId); } catch (_) { /* already closed */ }
  await chrome.storage.local.remove("posterproAutomationTabId");
  await rememberAutomationTab(tabId, true);
}

function encodeFacebookContextProbe(value) {
  const binary = unescape(encodeURIComponent(JSON.stringify(value)));
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  let base64url = "";
  for (let index = 0; index < binary.length; index += 3) {
    const a = binary.charCodeAt(index);
    const hasB = index + 1 < binary.length;
    const hasC = index + 2 < binary.length;
    const b = hasB ? binary.charCodeAt(index + 1) : 0;
    const c = hasC ? binary.charCodeAt(index + 2) : 0;
    base64url += alphabet[(a >> 2) & 63] + alphabet[((a & 3) << 4) | (b >> 4)];
    if (hasB) base64url += alphabet[((b & 15) << 2) | (c >> 6)];
    if (hasC) base64url += alphabet[c & 63];
  }
  // Prefix each short segment so the backend's diagnostic sanitizer cannot
  // mistake a long hexadecimal-looking run for a secret/token.
  return `CTX69:${(base64url.match(/.{1,27}/g) || []).map((part) => `x${part}`).join(".")}`;
}

async function deviceRequest(path, options = {}) {
  const { api, token } = await getAgentSettings();
  if (!token) throw new Error("Pair this extension with a one-time code from PosterPro Settings first.");
  const response = await fetch(`${api}${path}`, {
    ...options,
    headers: { ...(options.headers || {}), Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!response.ok) throw new Error(data?.detail?.message || data?.detail || `PosterPro request failed (${response.status})`);
  return data;
}

async function setJobState(jobId, status, details = {}) {
  return deviceRequest(`/browser-extension/jobs/${encodeURIComponent(jobId)}/state`, {
    method: "POST",
    body: JSON.stringify({ status, ...details }),
  });
}

function raceWithTimeout(promise, timeoutMs, errorCode, message) {
  let timer;
  return Promise.race([
    Promise.resolve(promise),
    new Promise((_, reject) => {
      timer = setTimeout(() => {
        const error = new Error(message || errorCode);
        error.code = errorCode;
        reject(error);
      }, timeoutMs);
    }),
  ]).finally(() => clearTimeout(timer));
}

async function setFacebookFormProgress(jobId, tabId, stage, phase = "checkpoint", detail = {}) {
  const now = Date.now();
  const current = activeFacebookFormProgress.get(Number(jobId)) || { startedAt: now };
  current.stage = stage;
  current.phase = phase;
  current.tabId = Number(tabId);
  const tab = await chrome.tabs.get(Number(tabId)).catch(() => null);
  let pageUrl = null;
  try {
    const parsed = new URL(String(tab?.url || ""));
    if (parsed.protocol === "https:" && /(^|\.)facebook\.com$/i.test(parsed.hostname)) pageUrl = `${parsed.origin}${parsed.pathname.slice(0, 240)}`;
  } catch (_) { /* URL evidence is optional */ }
  try {
    const result = await raceWithTimeout(deviceRequest(`/browser-extension/jobs/${encodeURIComponent(jobId)}/progress`, {
      method: "POST",
      body: JSON.stringify({
        stage, phase, timestamp: new Date(now).toISOString(), extension_version: EXTENSION_VERSION,
        content_build_id: FACEBOOK_BUILD_ID, page_url: pageUrl,
        detail: detail && typeof detail === "object" ? detail : {},
      }),
    }), 8000, "FACEBOOK_PROGRESS_REPORT_TIMEOUT", "Progress checkpoint could not be persisted before timeout.");
    if (result?.ok !== true) throw Object.assign(new Error("Backend did not confirm durable progress persistence."), { code: "FACEBOOK_PROGRESS_REPORT_FAILED" });
    current.lastProgressAt = now;
    current.stage = stage;
    current.phase = phase;
    current.tabId = Number(tabId);
    activeFacebookFormProgress.set(Number(jobId), current);
    return result;
  } catch (error) {
    current.lastProgressPersistError = String(error?.message || error).slice(0, 160);
    activeFacebookFormProgress.set(Number(jobId), current);
    return { ok: false, error_code: error?.code || "FACEBOOK_PROGRESS_REPORT_FAILED", error: current.lastProgressPersistError };
  }
}

async function requireFacebookFormProgress(jobId, tabId, stage, phase = "checkpoint", detail = {}) {
  const result = await setFacebookFormProgress(jobId, tabId, stage, phase, detail);
  if (result?.ok !== true) throw Object.assign(new Error(result?.error || "Durable Facebook form progress is unavailable."), { code: result?.error_code || "FACEBOOK_PROGRESS_REPORT_FAILED" });
  return result;
}

function facebookFormTimeoutCode(stage) {
  if (/IMAGE_DOWNLOAD/.test(String(stage || ""))) return "FACEBOOK_IMAGE_DOWNLOAD_TIMEOUT";
  if (/CATEGORY/.test(String(stage || ""))) return "FACEBOOK_CATEGORY_RESOLUTION_TIMEOUT";
  if (/IMAGE_UPLOAD/.test(String(stage || ""))) return "FACEBOOK_IMAGE_UPLOAD_TIMEOUT";
  if (/FORM_READY/.test(String(stage || ""))) return "FACEBOOK_FORM_READY_TIMEOUT";
  if (/ADVANCING|READY_TO_SUBMIT/.test(String(stage || ""))) return "FACEBOOK_STAGE_ADVANCE_TIMEOUT";
  return "CONTENT_SCRIPT_RESPONSE_TIMEOUT";
}

async function sendFacebookFormFillWithWatchdog(tabId, jobId, payload, images, policy = {}) {
  const noProgressMs = Number.isFinite(policy.noProgressMs) ? Math.max(1, policy.noProgressMs) : FACEBOOK_FORM_NO_PROGRESS_MS;
  const maxDurationMs = Number.isFinite(policy.maxDurationMs) ? Math.max(noProgressMs, policy.maxDurationMs) : FACEBOOK_FORM_MAX_DURATION_MS;
  const cancelDrainMs = Number.isFinite(policy.cancelDrainMs) ? Math.max(1, policy.cancelDrainMs) : FACEBOOK_FORM_CANCEL_DRAIN_MS;
  const progress = activeFacebookFormProgress.get(Number(jobId)) || { startedAt: Date.now() };
  progress.startedAt ||= Date.now();
  progress.lastProgressAt ||= Date.now();
  progress.stage ||= "FACEBOOK_FORM_READY";
  progress.tabId = Number(tabId);
  activeFacebookFormProgress.set(Number(jobId), progress);
  const fillRequest = Promise.resolve(chrome.tabs.sendMessage(tabId, {
    action: "posterpro_fill_listing", marketplace: "facebook", payload, images,
    internal_job_id: Number(jobId),
  }));
  let settled = false;
  let value;
  let failure;
  let watchdogFailure = false;
  fillRequest.then((result) => { settled = true; value = result; }, (error) => { settled = true; failure = error; });
  while (!settled) {
    const now = Date.now();
    if (now - progress.startedAt >= maxDurationMs) {
      failure = Object.assign(new Error("Facebook form-fill exceeded its bounded overall execution time."), { code: facebookFormTimeoutCode(progress.stage), safe_pre_submit_retry: false, timeout_kind: "overall" });
      watchdogFailure = true;
      break;
    }
    const idleFor = now - (progress.lastProgressAt || progress.startedAt);
    if (idleFor >= noProgressMs) {
      failure = Object.assign(new Error(`Facebook form fill made no progress for ${Math.round(idleFor / 1000)} seconds at ${progress.stage || "unknown stage"}.`), { code: facebookFormTimeoutCode(progress.stage), safe_pre_submit_retry: false, timeout_kind: "no_progress" });
      watchdogFailure = true;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, Math.min(1000, Math.max(1, noProgressMs - idleFor))));
  }
  if (failure) {
    if (!watchdogFailure) {
      failure.code ||= "CONTENT_SCRIPT_RESPONSE_FAILED";
      failure.safe_pre_submit_retry = true;
      failure.last_progress_stage = progress.stage || null;
      failure.last_progress_at = progress.lastProgressAt ? new Date(progress.lastProgressAt).toISOString() : null;
      throw failure;
    }
    let cancellation = null;
    try {
      cancellation = await raceWithTimeout(chrome.tabs.sendMessage(tabId, {
        action: "posterpro_cancel_facebook_form_fill", internal_job_id: Number(jobId), reason: failure.code,
      }), 5000, "FACEBOOK_FORM_CANCEL_ACK_TIMEOUT", "Content script did not acknowledge cancellation.");
    } catch (_) { cancellation = null; }
    if (cancellation?.cancelled === true) {
      await Promise.race([fillRequest.then(() => true, () => true), new Promise((resolve) => setTimeout(() => resolve(false), cancelDrainMs))]);
      if (settled) {
        failure.safe_pre_submit_retry = true;
        failure.last_progress_stage = progress.stage || null;
        failure.last_progress_at = progress.lastProgressAt ? new Date(progress.lastProgressAt).toISOString() : null;
        throw failure;
      }
    }
    failure.code = "FACEBOOK_FORM_CANCEL_UNCONFIRMED";
    failure.safe_pre_submit_retry = false;
    failure.last_progress_stage = progress.stage || null;
    failure.last_progress_at = progress.lastProgressAt ? new Date(progress.lastProgressAt).toISOString() : null;
    throw failure;
  }
  if (failure) throw failure;
  return value;
}

const FACEBOOK_RECONCILIATION_URL = "https://www.facebook.com/marketplace/you/selling/";
const FACEBOOK_RECONCILIATION_BUILD = FACEBOOK_BUILD_ID;

async function facebookReconciliationOnTab(tabId, payload, knownRemotePresence = false) {
  let tab = await chrome.tabs.get(tabId);
  let parsed;
  try { parsed = new URL(tab.url || tab.pendingUrl || ""); }
  catch (_) { throw new Error("Facebook reconciliation tab URL is unavailable."); }
  if (parsed.protocol !== "https:" || !/(^|\.)facebook\.com$/i.test(parsed.hostname)) {
    throw new Error("Read-only Facebook reconciliation is restricted to the authenticated Facebook tab.");
  }
  if (!/^\/marketplace\/(?:item\/|you\/selling)/i.test(parsed.pathname)) {
    await chrome.tabs.update(tabId, { url: FACEBOOK_RECONCILIATION_URL, active: false });
    await waitForTabComplete(tabId, 60000, { allowInteractiveFallback: true });
    tab = await chrome.tabs.get(tabId);
  }
  const inspect = async () => {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["marketplace-adapters.js", "content.js"] });
    const identity = await chrome.tabs.sendMessage(tabId, { action: "posterpro_content_script_identity" });
    if (identity?.extension_version !== EXTENSION_VERSION
      || identity?.content_script_version !== EXTENSION_VERSION
      || identity?.content_script_build_id !== FACEBOOK_RECONCILIATION_BUILD) {
      throw new Error("Facebook reconciliation content identity does not match the loaded extension.");
    }
    return chrome.tabs.sendMessage(tabId, {
      action: "posterpro_reconcile_facebook_seller_listing",
      expected_title: payload?.title,
      expected_price: payload?.price,
      expected_description: payload?.description,
      known_remote_presence: knownRemotePresence === true,
    });
  };
  let result = await inspect();
  // A navigation to an item that does not exactly match is not a reason to
  // stop: continue with a read-only exact search in the authenticated seller
  // listings page, never with another Publish.
  if (result?.state === "AMBIGUOUS" && /^\/marketplace\/item\//i.test(parsed.pathname)) {
    await chrome.tabs.update(tabId, { url: FACEBOOK_RECONCILIATION_URL, active: false });
    await waitForTabComplete(tabId, 60000, { allowInteractiveFallback: true });
    tab = await chrome.tabs.get(tabId);
    result = await inspect();
  }
  return result;
}

async function runFacebookReconciliationTask(task) {
  const payload = task?.payload?.marketplace_payload || {};
  if (String(task?.marketplace || "").toLowerCase() !== "facebook"
    || String(task?.action || "").toUpperCase() !== "CREATE"
    || !Number.isInteger(Number(task?.id)) || !payload.title || !Number(payload.price)) {
    throw new Error("Refusing malformed or non-Facebook read-only reconciliation task.");
  }
  const stored = await chrome.storage.local.get(["posterproAutomationTabId"]);
  let tabId = Number(stored.posterproAutomationTabId || 0);
  let tab = null;
  if (tabId) {
    try { tab = await chrome.tabs.get(tabId); } catch (_) { tab = null; }
  }
  let temporaryTabId = null;
  if (!tab || !/(^|\.)facebook\.com$/i.test(new URL(tab.url || "https://invalid.invalid").hostname)) {
    tab = await chrome.tabs.create({ url: FACEBOOK_RECONCILIATION_URL, active: false });
    tabId = Number(tab.id);
    temporaryTabId = tabId;
  }
  try {
    if (!/^https:\/\//i.test(String(tab.url || ""))) await waitForTabComplete(tabId, 60000, { allowInteractiveFallback: true });
    const result = await facebookReconciliationOnTab(tabId, payload, task.known_remote_presence === true);
    const evidence = {
      ...result,
      source: result?.source || "authenticated_facebook_seller_listings",
      page_url: result?.page_url || (await chrome.tabs.get(tabId)).url,
      authenticated_account_context: true,
      ...(result?.evidence && typeof result.evidence === "object" ? result.evidence : {}),
    };
    return await deviceRequest(`/browser-extension/facebook-reconciliation/${encodeURIComponent(task.id)}/result`, {
      method: "POST",
      body: JSON.stringify({
        state: ["FOUND", "NOT_FOUND"].includes(result?.state) ? result.state : "AMBIGUOUS",
        external_listing_id: result?.external_listing_id || null,
        external_url: result?.external_url || null,
        evidence,
      }),
    });
  } finally {
    if (temporaryTabId) {
      try { await chrome.tabs.remove(temporaryTabId); } catch (_) { /* temporary reconciliation tab already closed */ }
    }
  }
}

async function reconcileLostFacebookSubmit(job, tabId, snapshot, reason) {
  const evidenceBase = {
    source: "authenticated_facebook_seller_listings",
    authenticated_account_context: true,
    exact_search_used: false,
    page_url: "https://www.facebook.com/marketplace/you/selling/",
    response_loss: String(reason?.message || reason || "message channel closed").slice(0, 240),
  };
  let observation;
  try { observation = await facebookReconciliationOnTab(tabId, snapshot, false); }
  catch (error) {
    observation = { state: "AMBIGUOUS", source: evidenceBase.source, page_url: evidenceBase.page_url, error_code: "POST_SUBMIT_RECONCILIATION_UNAVAILABLE", error: String(error?.message || error).slice(0, 240) };
  }
  const result = {
    submission_performed: true,
    remote_reconciliation: {
      status: observation?.state === "FOUND" ? "FOUND" : observation?.state === "NOT_FOUND" ? "NOT_FOUND" : "AMBIGUOUS",
      confirmation: observation?.state === "FOUND" ? "REMOTE_RECONCILIATION_FOUND" : observation?.state === "NOT_FOUND" ? "REMOTE_RECONCILIATION_NOT_FOUND" : "REMOTE_RECONCILIATION_AMBIGUOUS",
      source: observation?.source || evidenceBase.source,
      observed_at: new Date().toISOString(),
      evidence: { ...evidenceBase, ...observation },
      safe_same_child_retry: observation?.state === "NOT_FOUND",
    },
  };
  if (observation?.state === "FOUND" && observation.external_listing_id && observation.external_url) {
    const reconciledResult = {
      ...result,
      external_listing_id: observation.external_listing_id,
      external_url: observation.external_url,
      identity_capture_state: "AUTHENTICATED_TITLE_PRICE_MATCH",
      remote_description_verification: observation.description_verification || null,
    };
    await setJobState(job.id, "SUBMITTED", {
      external_listing_id: observation.external_listing_id,
      external_url: observation.external_url,
      result: reconciledResult,
    });
    return setJobState(job.id, "SUBMITTED_PENDING_REVIEW", {
      external_listing_id: observation.external_listing_id,
      external_url: observation.external_url,
      result: reconciledResult,
    });
  }
  const exactAbsence = observation?.state === "NOT_FOUND"
    && observation?.exact_search_used === true
    && observation?.exact_title_search === true
    && observation?.account_search_complete === true
    && observation?.empty_state_verified === true
    && observation?.result_count === 0;
  if (!exactAbsence) {
    // A partial seller view is not evidence of absence. Do not convert it into
    // permission for a second Publish.
    result.remote_reconciliation.status = "AMBIGUOUS";
    result.remote_reconciliation.confirmation = "REMOTE_RECONCILIATION_AMBIGUOUS";
    result.remote_reconciliation.safe_same_child_retry = false;
  }
  return setJobState(job.id, "FAILED", {
    error_code: exactAbsence ? "REMOTE_RECONCILIATION_NOT_FOUND" : "SUBMISSION_OUTCOME_UNKNOWN",
    error_detail: exactAbsence
      ? "A complete exact-title search in the authenticated Facebook seller listings showed the explicit empty state. A guarded same-child retry may be considered; no second Publish was attempted."
      : "Publish may have succeeded; authenticated read-only reconciliation did not uniquely establish the remote listing. No second Publish was attempted.",
    result,
  });
}

async function reconcileFacebookDescriptionRepair(job, tabId, snapshot, saveResult) {
  const payload = snapshot || {};
  const expectedId = String(job.external_listing_id || payload.repair_expected_remote_id || "");
  const expectedUrl = String(job.external_url || payload.repair_expected_remote_url || "");
  const expectedDescription = String(payload.description || "");
  if (!expectedId || !expectedUrl || !expectedDescription || payload.repair_scope !== "facebook_description_only") {
    return { status: "FAILED", error_code: "FACEBOOK_DESCRIPTION_REPAIR_IDENTITY_AMBIGUOUS", repair_state: "FACEBOOK_DESCRIPTION_REPAIR_FAILED" };
  }
  let observed;
  try {
    const tab = await chrome.tabs.get(tabId);
    const current = new URL(String(tab.url || tab.pendingUrl || ""));
    const expected = new URL(expectedUrl);
    if (current.origin !== expected.origin || current.pathname.replace(/\/$/, "") !== expected.pathname.replace(/\/$/, "")) {
      await chrome.tabs.update(tabId, { url: expectedUrl, active: false });
      await waitForTabComplete(tabId, 60000, { allowInteractiveFallback: true });
    } else {
      await waitForTabComplete(tabId, 15000, { allowInteractiveFallback: true });
    }
    observed = await facebookReconciliationOnTab(tabId, {
      title: payload.repair_expected_title || payload.title,
      price: payload.repair_expected_price ?? payload.price,
      description: expectedDescription,
    }, true);
  } catch (error) {
    return {
      status: "AWAITING_OPERATOR_REVIEW", error_code: "FACEBOOK_DESCRIPTION_REPAIR_OUTCOME_UNKNOWN",
      repair_state: "FACEBOOK_DESCRIPTION_REPAIR_OUTCOME_UNKNOWN",
      error_detail: String(error?.message || error).slice(0, 180),
      submission_performed: true, save_result: saveResult || null,
      external_listing_id: expectedId, external_url: expectedUrl,
    };
  }
  const sameIdentity = observed?.state === "FOUND"
    && Number(saveResult?.save_activation_count || 0) === 1
    && String(observed.external_listing_id || "") === expectedId
    && String(observed.external_url || "").replace(/\/$/, "") === expectedUrl.replace(/\/$/, "")
    && observed.exact_title_match === true
    && (observed.exact_price_match === true || observed.price_match === true)
    && observed.description_verification?.matches === true;
  if (sameIdentity) {
    return {
      status: "COMPLETED", repair_state: "FACEBOOK_DESCRIPTION_REPAIR_SUCCESS",
      submission_performed: true, external_listing_id: expectedId, external_url: expectedUrl,
      description_only: true, save_activation_count: Number(saveResult?.save_activation_count || 0),
      title_unchanged: true, price_unchanged: true,
      remote_description_verification: observed.description_verification,
      post_save_reconciliation: observed, save_activation: saveResult || null,
    };
  }
  if (observed?.state === "FOUND"
    && String(observed.external_listing_id || "") === expectedId
    && String(observed.external_url || "").replace(/\/$/, "") === expectedUrl.replace(/\/$/, "")
    && observed.description_verification?.matches === false) {
    return {
      status: "FAILED", error_code: "FACEBOOK_DESCRIPTION_REPAIR_FAILED",
      repair_state: "FACEBOOK_DESCRIPTION_REPAIR_FAILED", submission_performed: true,
      external_listing_id: expectedId, external_url: expectedUrl,
      remote_description_verification: observed.description_verification,
      post_save_reconciliation: observed, save_activation: saveResult || null,
    };
  }
  return {
    status: "AWAITING_OPERATOR_REVIEW", error_code: "FACEBOOK_DESCRIPTION_REPAIR_OUTCOME_UNKNOWN",
    repair_state: "FACEBOOK_DESCRIPTION_REPAIR_OUTCOME_UNKNOWN", submission_performed: true,
    external_listing_id: expectedId, external_url: expectedUrl,
    post_save_reconciliation: observed || null, save_activation: saveResult || null,
  };
}

// This reports readiness only; its tab event argument is a point-in-time
// snapshot and must never be reused as navigation truth after an await.
function waitForTabComplete(tabId, timeoutMs = 60000, options = {}) {
  const allowInteractiveFallback = Boolean(options.allowInteractiveFallback);
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      callback(value);
    };
    const timer = setTimeout(() => {
      // Facebook's application can keep the tab in a perpetual `loading`
      // state while its usable Marketplace form is already interactive. The
      // content script has its own login/form checks, so accept an existing
      // Facebook tab as a safe readiness fallback instead of reporting a
      // timeout after the operator can visibly use the form.
      if (allowInteractiveFallback) {
        chrome.tabs.get(tabId).then((tab) => {
          const url = String(tab?.url || "").toLowerCase();
          if (tab && /^https:\/\/(www\.)?facebook\.com\//.test(url)) finish(resolve, true);
          else finish(reject, new Error("Marketplace page did not finish loading before timeout."));
        }).catch((error) => finish(reject, error));
        return;
      }
      finish(reject, new Error("Marketplace page did not finish loading before timeout."));
    }, timeoutMs);
    const listener = (updatedId, changeInfo, tab) => {
      if (updatedId === tabId && (changeInfo.status === "complete" || tab?.status === "complete")) {
        finish(resolve, true);
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((tab) => {
      if (tab?.status === "complete") {
        finish(resolve, true);
      }
    }).catch((error) => {
      finish(reject, error);
    });
  });
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunk));
  }
  return btoa(binary);
}

async function downloadCanonicalImages(payload, marketplace, progressContext = null, policy = {}) {
  const maxCount = marketplace === "poshmark" ? 16 : marketplace === "mercari" ? 10 : 10;
  const urls = Array.isArray(payload.image_urls) ? payload.image_urls.slice(0, maxCount) : [];
  const images = [];
  let totalBytes = 0;
  const aggregateStartedAt = Date.now();
  const aggregateTimeoutMs = Number.isFinite(policy.aggregateTimeoutMs) ? Math.max(1, policy.aggregateTimeoutMs) : 120000;
  const perImageTimeoutMs = Number.isFinite(policy.perImageTimeoutMs) ? Math.max(1, policy.perImageTimeoutMs) : 25000;
  if (progressContext?.jobId && marketplace === "facebook") {
    await requireFacebookFormProgress(progressContext.jobId, progressContext.tabId, "IMAGE_DOWNLOAD_STARTED", "started", { count: urls.length });
  }
  for (let index = 0; index < urls.length; index += 1) {
    if (Date.now() - aggregateStartedAt >= aggregateTimeoutMs) {
      const error = new Error("Facebook image acquisition exceeded its 120-second aggregate limit.");
      error.code = "FACEBOOK_IMAGE_DOWNLOAD_TIMEOUT";
      error.safe_pre_submit_retry = true;
      throw error;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Math.min(perImageTimeoutMs, aggregateTimeoutMs - (Date.now() - aggregateStartedAt)));
    try {
      const response = await fetch(String(urls[index]), { credentials: "omit", cache: "no-store", signal: controller.signal });
      if (!response.ok) {
        const error = new Error(`Could not download canonical photo ${index + 1} (HTTP ${response.status}).`);
        error.code = "FACEBOOK_IMAGE_DOWNLOAD_FAILED";
        error.safe_pre_submit_retry = true;
        throw error;
      }
      const type = response.headers.get("content-type") || "";
      if (!type.toLowerCase().startsWith("image/")) {
        const error = new Error(`Canonical photo ${index + 1} did not return image content.`);
        error.code = "FACEBOOK_IMAGE_DOWNLOAD_FAILED";
        error.safe_pre_submit_retry = true;
        throw error;
      }
      const blob = await response.blob();
      totalBytes += blob.size;
      if (blob.size <= 0 || blob.size > 8 * 1024 * 1024 || totalBytes > 24 * 1024 * 1024) {
        const error = new Error("Canonical photos exceed the safe extension upload size limit.");
        error.code = "FACEBOOK_IMAGE_DOWNLOAD_FAILED";
        error.safe_pre_submit_retry = true;
        throw error;
      }
      const bytes = new Uint8Array(await blob.arrayBuffer());
      const extension = type.includes("png") ? "png" : type.includes("webp") ? "webp" : "jpg";
      images.push({ data_url: `data:${type};base64,${bytesToBase64(bytes)}`, file_name: `posterpro-${index + 1}.${extension}` });
      if (progressContext?.jobId && marketplace === "facebook") {
        await requireFacebookFormProgress(progressContext.jobId, progressContext.tabId, "IMAGE_DOWNLOAD_STARTED", "checkpoint", { index: index + 1, count: urls.length, duration_ms: Date.now() - aggregateStartedAt });
      }
    } catch (cause) {
      if (cause?.name === "AbortError") {
        const error = new Error(`Facebook image ${index + 1} exceeded its bounded download timeout.`);
        error.code = "FACEBOOK_IMAGE_DOWNLOAD_TIMEOUT";
        error.safe_pre_submit_retry = true;
        throw error;
      }
      throw cause;
    } finally {
      clearTimeout(timer);
    }
  }
  if (Date.now() - aggregateStartedAt > aggregateTimeoutMs) {
    const error = new Error("Facebook image acquisition exceeded its 120-second aggregate limit.");
    error.code = "FACEBOOK_IMAGE_DOWNLOAD_TIMEOUT";
    error.safe_pre_submit_retry = true;
    throw error;
  }
  if (progressContext?.jobId && marketplace === "facebook") {
    await requireFacebookFormProgress(progressContext.jobId, progressContext.tabId, "IMAGE_DOWNLOAD_STARTED", "completed", { count: urls.length, duration_ms: Date.now() - aggregateStartedAt });
    await requireFacebookFormProgress(progressContext.jobId, progressContext.tabId, "IMAGE_DOWNLOAD_COMPLETE", "checkpoint", { count: urls.length, duration_ms: Date.now() - aggregateStartedAt });
  }
  return images;
}

function requireMarketplaceFormReady(result, marketplace) {
  if (result?.ok) return result;
  const destination = String(marketplace || "marketplace").toLowerCase();
  const error = new Error(result?.error || result?.error_code || `${destination} adapter could not fill the listing form.`);
  error.code = result?.error_code || (result?.page_state === "FORM_CHANGED" ? "MARKETPLACE_FORM_CHANGED" : "MARKETPLACE_ADAPTER_REJECTED_FORM");
  error.result = result || { ok: false, marketplace: destination, page_state: "NO_ADAPTER_RESULT" };
  throw error;
}

async function sendIdentityWithRetry(tabId, trace) {
  const waits = [0, 100, 250, 500];
  let lastError = null;
  for (let attempt = 0; attempt < waits.length; attempt += 1) {
    if (waits[attempt]) await new Promise((resolve) => setTimeout(resolve, waits[attempt]));
    try {
      const identity = await chrome.tabs.sendMessage(tabId, { action: "posterpro_content_script_identity" });
      if (identity && typeof identity === "object") {
        trace.identity_attempts = attempt + 1;
        return identity;
      }
      lastError = new Error("Content script received no identity response.");
    } catch (error) { lastError = error; }
    trace.identity_attempts = attempt + 1;
  }
  const noResponse = !lastError || /receiving end does not exist|could not establish connection/i.test(String(lastError.message || lastError));
  const error = new Error(String(lastError?.message || "Content script did not answer the identity handshake.").slice(0, 600));
  error.code = noResponse ? "CONTENT_IDENTITY_NO_RESPONSE" : "CONTENT_IDENTITY_MESSAGE_FAILED";
  error.executionDiagnostic = trace;
  throw error;
}

function parseCurrentTabUrl(currentTab, stageLabel) {
  const rawUrl = currentTab?.url || currentTab?.pendingUrl || null;
  if (!rawUrl) {
    const error = new Error(`Automation tab URL is unavailable (${stageLabel}); tab status=${String(currentTab?.status || "unknown")}, url_present=${Boolean(currentTab?.url)}, pending_url_present=${Boolean(currentTab?.pendingUrl)}.`);
    error.code = "AUTOMATION_TAB_URL_UNAVAILABLE";
    throw error;
  }
  try {
    return new URL(String(rawUrl));
  } catch (cause) {
    const error = new Error(`Automation tab URL is malformed (${stageLabel}).`);
    error.code = "AUTOMATION_TAB_URL_INVALID";
    error.name = cause?.name || "TypeError";
    throw error;
  }
}

function validateMarketplaceTabUrl(parsed, requested) {
  if (parsed.protocol !== "https:" || !parsed.pathname.startsWith("/")) {
    const error = new Error("Diagnostic target URL must be an HTTPS page.");
    error.code = "AUTOMATION_TAB_URL_INVALID";
    throw error;
  }
  const expectedHost = requested.hostname.replace(/^www\./i, "").toLowerCase();
  const actualHost = parsed.hostname.replace(/^www\./i, "").toLowerCase();
  if (!(actualHost === expectedHost || actualHost.endsWith(`.${expectedHost}`))) {
    const error = new Error("Marketplace tab redirected outside its requested marketplace host.");
    error.code = "AUTOMATION_TAB_URL_INVALID";
    throw error;
  }
  return parsed;
}

async function prepareDiagnosticContent(tab, trace, requestedUrl) {
  const tabId = Number(tab?.id ?? trace.tab_id);
  trace.tab_id = Number.isInteger(tabId) && tabId > 0 ? tabId : null;
  trace.requested_url = safeExecutionUrl(requestedUrl);
  const requested = await executionStage(trace, "VALIDATE_REQUESTED_URL", "AUTOMATION_TAB_URL_INVALID", async () => {
    let parsed;
    try { parsed = new URL(String(requestedUrl || "")); }
    catch (cause) {
      const error = new Error("Requested diagnostic URL is malformed.");
      error.code = "AUTOMATION_TAB_URL_INVALID";
      error.name = cause?.name || "TypeError";
      throw error;
    }
    if (parsed.protocol !== "https:" || !parsed.hostname) {
      const error = new Error("Diagnostic target URL must be an HTTPS page.");
      error.code = "AUTOMATION_TAB_URL_INVALID";
      throw error;
    }
    return parsed;
  }, { tab_id: trace.tab_id, requested_url: trace.requested_url });
  let currentTab = await executionStage(trace, "READ_CURRENT_TAB", "AUTOMATION_TAB_NOT_FOUND", () => chrome.tabs.get(tabId), { tab_id: trace.tab_id });
  let currentParsed = await executionStage(trace, "VALIDATE_TARGET_URL", "AUTOMATION_TAB_URL_INVALID", async () => validateMarketplaceTabUrl(parseCurrentTabUrl(currentTab, "after initial navigation"), requested), {
    tab_id: trace.tab_id,
    tab_status: currentTab?.status || null,
    url_present: Boolean(currentTab?.url),
    pending_url_present: Boolean(currentTab?.pendingUrl),
    page_url: safeExecutionUrl(currentTab?.url || currentTab?.pendingUrl),
  });
  trace.page_url = safeExecutionUrl(currentParsed.href);
  trace.document_ready_state = currentTab?.status || null;
  await chrome.storage.local.set({ posterproLastExecutionDiagnostic: trace }).catch(() => {});

  const backgroundVersion = chrome.runtime.getManifest?.().version || EXTENSION_VERSION;
  const injectCurrentBundle = async () => {
    await executionStage(trace, "INJECT_ADAPTER", "SCRIPT_INJECTION_FAILED", () => chrome.scripting.executeScript({ target: { tabId }, files: ["marketplace-adapters.js"] }), { tab_id: tabId, page_url: trace.page_url });
    await executionStage(trace, "INJECT_CONTENT", "SCRIPT_INJECTION_FAILED", () => chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] }), { tab_id: tabId, page_url: trace.page_url });
    return executionStage(trace, "IDENTITY_REQUEST", "CONTENT_IDENTITY_MESSAGE_FAILED", () => sendIdentityWithRetry(tabId, trace), { tab_id: tabId, page_url: trace.page_url });
  };
  // Marketplace tabs can retain an isolated-world listener from the previous
  // extension build. Inject, verify the page's own identity, and if it is
  // stale reload exactly once before reinjecting the current bundle.
  let identity = null;
  let initialIdentityError = null;
  try { identity = await injectCurrentBundle(); }
  catch (error) {
    if (!["CONTENT_IDENTITY_NO_RESPONSE", "CONTENT_IDENTITY_MESSAGE_FAILED"].includes(error?.code)) throw error;
    initialIdentityError = error;
  }
  const currentIdentityMatches = (candidate) => candidate?.content_script_version === backgroundVersion
    && candidate?.extension_version === backgroundVersion
    && Boolean(candidate?.content_script_build_id);
  if (!currentIdentityMatches(identity)) {
    trace.stale_content_script = {
      background_version: backgroundVersion,
      page_content_script_version: identity?.content_script_version || null,
      page_extension_version: identity?.extension_version || null,
      page_content_script_build_id: identity?.content_script_build_id || null,
      identity_error_code: initialIdentityError?.code || null,
      identity_error_detail: String(initialIdentityError?.message || "").slice(0, 300) || null,
    };
    await executionStage(trace, "STALE_CONTENT_SCRIPT_RELOAD", "TAB_RELOAD_FAILED", () => chrome.tabs.reload(tabId), { tab_id: tabId, page_url: trace.page_url });
    await executionStage(trace, "WAIT_FOR_CURRENT_CONTENT", "TAB_LOAD_TIMEOUT", () => waitForTabComplete(tabId, 60000), { tab_id: tabId });
    currentTab = await executionStage(trace, "READ_POST_RELOAD_TAB", "AUTOMATION_TAB_NOT_FOUND", () => chrome.tabs.get(tabId), { tab_id: tabId });
    currentParsed = await executionStage(trace, "VALIDATE_POST_RELOAD_URL", "AUTOMATION_TAB_URL_INVALID", async () => validateMarketplaceTabUrl(parseCurrentTabUrl(currentTab, "after stale-script reload"), requested), {
      tab_id: tabId,
      tab_status: currentTab?.status || null,
      page_url: safeExecutionUrl(currentTab?.url || currentTab?.pendingUrl),
    });
    trace.page_url = safeExecutionUrl(currentParsed.href);
    await injectCurrentBundle();
    identity = await executionStage(trace, "IDENTITY_RECHECK", "CONTENT_IDENTITY_MESSAGE_FAILED", () => sendIdentityWithRetry(tabId, trace), { tab_id: tabId, page_url: trace.page_url });
  }
  if (!identity) {
    const error = new Error("Content script did not return an identity object.");
    error.code = "CONTENT_IDENTITY_NO_RESPONSE";
    error.executionDiagnostic = trace;
    throw error;
  }
  trace.identity = {
    extension_version: identity.extension_version || null,
    content_script_version: identity.content_script_version || null,
    content_script_build_id: identity.content_script_build_id || null,
    content_script_injected_at: identity.content_script_injected_at || null,
    page_loaded_at: identity.page_loaded_at || null,
  };
  if (!currentIdentityMatches(identity)) {
    const error = new Error(`Runtime version mismatch: background ${backgroundVersion}, page script ${identity.content_script_version || "missing"}, extension ${identity.extension_version || "missing"}.`);
    error.code = "CONTENT_VERSION_MISMATCH";
    error.executionDiagnostic = trace;
    throw error;
  }
  trace.identity_verified_at = new Date().toISOString();
  trace.last_successful_stage = "IDENTITY_VERIFIED";
  trace.stage = "IDENTITY_VERIFIED";
  trace.events.push({ stage: "IDENTITY_VERIFIED", status: "succeeded", at: trace.identity_verified_at });
  await chrome.storage.local.set({ posterproLastExecutionDiagnostic: trace }).catch(() => {});
  return { ok: true, ...identity, background_version: backgroundVersion };
}

async function runReadOnlyFacebookCreateContextProbe(job, tab, execution, requestedUrl, hygieneBefore) {
  const jobId = job.id;
  let probe = null;
  let cleanupError = null;
  const previousAutomationTabId = Number(tab.previous_automation_tab_id || 0) || null;
  try {
    const identity = await prepareDiagnosticContent(tab, execution, requestedUrl);
    if (identity.extension_version !== EXTENSION_VERSION || identity.content_script_version !== EXTENSION_VERSION
      || identity.content_script_build_id !== FACEBOOK_BUILD_ID) {
      const error = new Error("The read-only context probe did not receive the expected Facebook content build.");
      error.code = "STALE_CONTENT_SCRIPT_RELOAD_REQUIRED";
      throw error;
    }
    const response = await executionStage(execution, "READ_ONLY_CONTEXT_PROBE", "FACEBOOK_CONTEXT_PROBE_FAILED", () =>
      chrome.tabs.sendMessage(tab.id, { action: "posterpro_probe_facebook_create_context_read_only" }),
    { tab_id: tab.id, page_url: execution.page_url });
    if (!response || response.read_only !== true || response.extension_version !== EXTENSION_VERSION
      || response.content_script_version !== EXTENSION_VERSION || response.content_script_build_id !== FACEBOOK_BUILD_ID) {
      const error = new Error("The read-only Facebook context probe returned invalid or stale evidence.");
      error.code = "FACEBOOK_CONTEXT_PROBE_RESPONSE_INVALID";
      throw error;
    }
    const hygieneWithProbe = await facebookTabHygiene();
    const readiness = response.form_anchors || {};
    probe = {
      probe_id: Number(job.payload?.probe_id || jobId),
      tab_id: tab.id,
      page_loaded_at: response.page_loaded_at || null,
      performance_time_origin: response.performance_time_origin || null,
      content_script_injected_at: identity.content_script_injected_at || null,
      navigation_type: response.navigation_type || null,
      content_script_build: response.content_script_build_id,
      workflow_stage: response.workflow_stage || null,
      form_anchors: readiness,
      photo_preview_count: response.photo_preview_count,
      title_length: response.title_length,
      price_value: response.price_value,
      description_length: response.description_length,
      category_value: response.category_value,
      visible_dialogs: (response.visible_dialogs || []).slice(0, 4),
      visible_overlays: (response.visible_overlays || []).slice(0, 8),
      blocking_overlay_count: response.blocking_overlay_count,
      expanded_controls: (response.expanded_controls || []).slice(0, 8),
      draft_restore_evidence: response.draft_restore_evidence === true,
      initial_form_clean: response.initial_form_clean === true,
      initial_form_reasons: (response.initial_form_reasons || []).slice(0, 8),
      form_anchor_count: response.form_anchor_count,
      time_to_details_ready_ms: response.time_to_details_ready_ms,
      tab_hygiene_before: hygieneBefore,
      tab_hygiene_with_probe: hygieneWithProbe,
    };
    execution.stage = "READ_ONLY_CONTEXT_PROBE_CAPTURED";
    execution.last_successful_stage = execution.stage;
    execution.events.push({ stage: execution.stage, status: "succeeded", at: new Date().toISOString(), tab_id: tab.id });
    await chrome.storage.local.set({ posterproLastExecutionDiagnostic: execution });
  } finally {
    try {
      await closeAutomationTab(tab.id);
      if (previousAutomationTabId) {
        const previous = await chrome.tabs.get(previousAutomationTabId).catch(() => null);
        let previousIsFacebook = false;
        try { previousIsFacebook = /(^|\.)facebook\.com$/i.test(new URL(String(previous?.url || "")).hostname); } catch (_) { /* stale prior tab */ }
        if (previous?.id && previousIsFacebook) await chrome.storage.local.set({ posterproAutomationTabId: previousAutomationTabId });
      }
      if (probe) probe.tab_hygiene_after_cleanup = await facebookTabHygiene();
    } catch (error) {
      cleanupError = String(error?.message || error).slice(0, 160);
    }
  }
  if (!probe) throw Object.assign(new Error("Read-only Facebook context probe failed before evidence was captured."), { code: "FACEBOOK_CONTEXT_PROBE_FAILED" });
  if (cleanupError) probe.cleanup_error = cleanupError;
  const compactEvidence = encodeFacebookContextProbe(probe);
  const result = {
    ok: true, capability_ready: true, marketplace: "facebook", action: "DIAGNOSTIC",
    page_state: "FORM_AVAILABLE", login_state: "LOGGED_IN", page_url: requestedUrl,
    background_version: EXTENSION_VERSION, extension_version: probe.content_script_build,
    content_script_version: probe.content_script_build, content_script_build_id: probe.content_script_build,
    page_loaded_at: probe.page_loaded_at, content_script_injected_at: probe.content_script_injected_at,
    form_readiness: { form_ready_wait_started: true, form_ready_wait_ms: probe.time_to_details_ready_ms, form_ready_anchors: {
      title: probe.form_anchors?.title === true, price: probe.form_anchors?.price === true, description: probe.form_anchors?.description === true,
      photos: probe.form_anchors?.photos === true, visible_form: probe.form_anchors?.visible_form === true,
      core_anchor_count: Number(probe.form_anchors?.core_anchor_count || probe.form_anchor_count || 0),
    } },
    field_results: [], missing_required_fields: [], submission_performed: false,
    error_detail: compactEvidence, execution_diagnostic: execution,
  };
  await setJobState(jobId, "FORM_DETECTED", { result });
  await setJobState(jobId, "TESTING_FIELDS", { result });
  const completed = await setJobState(jobId, "COMPLETED", { result, error_detail: compactEvidence });
  await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...job, ...completed, tab_id: tab.id, result } });
  return result;
}

async function runClaimedJob(job) {
  const jobId = job.id;
  const diagnosticJob = String(job.action || "").toUpperCase() === "DIAGNOSTIC" || Boolean(job.payload?.diagnostic);
  const createMarketplace = String(job.marketplace || "").toLowerCase();
  const authorizedBrowserCreate = String(job.action || "").toUpperCase() === "CREATE" && ["facebook", "mercari"].includes(createMarketplace);
  const execution = diagnosticJob || authorizedBrowserCreate ? makeExecutionDiagnostic() : null;
  await chrome.storage.local.set({ posterproActiveJob: job });
  await setJobState(jobId, "NAVIGATING");
  const snapshot = job.payload?.marketplace_payload || {};
  const startUrl = String(snapshot.start_url || "");
  if (!/^https:\/\//i.test(startUrl)) {
    const error = new Error("Marketplace create URL is missing or not HTTPS.");
    if (execution) {
      execution.requested_url = safeExecutionUrl(startUrl);
      execution.stage = "VALIDATE_TARGET_URL";
      error.code = "AUTOMATION_TAB_URL_INVALID";
      error.executionDiagnostic = execution;
    }
    throw error;
  }
  if (String(job.action || "").toUpperCase() === "END") {
    const host = new URL(startUrl).hostname.toLowerCase();
    const hostMatch = {
      facebook: ["facebook.com"], mercari: ["mercari.com"], poshmark: ["poshmark.com"],
      vinted: ["vinted.com"], etsy: ["etsy.com"], offerup: ["offerup.com"],
      depop: ["depop.com"], whatnot: ["whatnot.com"],
    }[String(job.marketplace || "").toLowerCase()] || [];
    if (!hostMatch.some((domain) => host === domain || host.endsWith(`.${domain}`))) {
      throw new Error("Stored external listing URL does not match the requested marketplace.");
    }
    const tab = await openAutomationTab(startUrl);
    await waitForTabComplete(tab.id, 60000, { allowInteractiveFallback: job.marketplace === "facebook" });
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["marketplace-adapters.js", "content.js"] });
    const inspection = await chrome.tabs.sendMessage(tab.id, {
      action: "posterpro_inspect_exact_end",
      marketplace: job.marketplace,
      payload: snapshot,
      external_listing_id: job.external_listing_id || snapshot.external_listing_id || null,
      expected_external_url: job.external_url || snapshot.external_url || startUrl,
    });
    if (!inspection?.identity_verified) {
      await setJobState(jobId, "BLOCKED_EXTERNAL", { error_code: inspection?.error_code || "EXTERNAL_IDENTITY_NOT_VERIFIED", error_detail: "The browser could not verify that the open page is the exact stored marketplace listing.", result: inspection });
      await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...job, tab_id: tab.id, status: "BLOCKED_EXTERNAL", result: inspection } });
      return inspection;
    }
    const review = { ...inspection, stage: "AWAITING_OPERATOR_REVIEW" };
    await chrome.storage.local.set({ posterproActiveJob: { ...job, tab_id: tab.id, status: "AWAITING_OPERATOR_REVIEW", fill_result: review } });
    await setJobState(jobId, "AWAITING_OPERATOR_REVIEW", { result: review });
    return review;
  }
  const readOnlyCreateContextProbe = createMarketplace === "facebook" && String(job.action || "").toUpperCase() === "DIAGNOSTIC"
    && job.payload?.read_only_create_context_probe === true;
  const hygieneBeforeProbe = readOnlyCreateContextProbe ? await facebookTabHygiene() : null;
  const facebookFreshCreate = createMarketplace === "facebook" && String(job.action || "").toUpperCase() === "CREATE";
  const needsFreshFacebookTab = facebookFreshCreate || readOnlyCreateContextProbe;
  const tab = execution
    ? await executionStage(execution, needsFreshFacebookTab ? "AUTOMATION_TAB_CREATE_FRESH" : "AUTOMATION_TAB_RESOLUTION", "AUTOMATION_TAB_NOT_FOUND", () => openAutomationTab(startUrl, execution, { fresh: needsFreshFacebookTab }), { requested_url: safeExecutionUrl(startUrl) })
    : await openAutomationTab(startUrl);
  if (execution) {
    execution.tab_id = tab.id;
    execution.page_url = null;
    await chrome.storage.local.set({ posterproLastExecutionDiagnostic: execution }).catch(() => {});
  }
  await (execution
    ? executionStage(execution, "INITIAL_WAIT_COMPLETE", "TAB_LOAD_TIMEOUT", () => waitForTabComplete(tab.id, 60000, { allowInteractiveFallback: job.marketplace === "facebook" }), { tab_id: tab.id, requested_url: execution.requested_url })
    : waitForTabComplete(tab.id, 60000, { allowInteractiveFallback: job.marketplace === "facebook" }));
  if (String(job.action || "").toUpperCase() === "DIAGNOSTIC" || job.payload?.diagnostic) {
    if (readOnlyCreateContextProbe) return runReadOnlyFacebookCreateContextProbe(job, tab, execution, startUrl, hygieneBeforeProbe);
    const contentIdentity = await prepareDiagnosticContent(tab, execution, startUrl);
    const diagnostic = await executionStage(execution, "DIAGNOSTIC_MESSAGE", "DIAGNOSTIC_MESSAGE_FAILED", () => chrome.tabs.sendMessage(tab.id, {
      action: "posterpro_diagnose_listing",
      marketplace: job.marketplace,
      payload: snapshot,
      images: [],
    }), { tab_id: tab.id, page_url: execution.page_url });
    if (!diagnostic || typeof diagnostic !== "object" || typeof diagnostic.login_state !== "string" || !Array.isArray(diagnostic.field_results)) {
      const error = new Error("Marketplace content script returned an invalid diagnostic response.");
      error.code = "DIAGNOSTIC_RESPONSE_INVALID";
      error.executionDiagnostic = execution;
      throw error;
    }
    execution.stage = "DIAGNOSTIC_RESPONSE_VALIDATED";
    execution.last_successful_stage = "DIAGNOSTIC_RESPONSE_VALIDATED";
    execution.events.push({ stage: "DIAGNOSTIC_RESPONSE_VALIDATED", status: "succeeded", at: new Date().toISOString() });
    await chrome.storage.local.set({ posterproLastExecutionDiagnostic: execution }).catch(() => {});
    const responseVersion = diagnostic?.content_script_version;
    const responseExtensionVersion = diagnostic?.extension_version;
    if (responseVersion !== contentIdentity.content_script_version
      || responseExtensionVersion !== contentIdentity.extension_version
      || responseVersion !== contentIdentity.background_version) {
      const stale = {
        ok: false,
        error_code: "STALE_CONTENT_SCRIPT_RELOAD_REQUIRED",
        background_version: contentIdentity.background_version,
        extension_version: responseExtensionVersion || null,
        content_script_version: responseVersion || null,
        content_script_build_id: diagnostic?.content_script_build_id || contentIdentity.content_script_build_id,
        content_script_injected_at: diagnostic?.content_script_injected_at || contentIdentity.content_script_injected_at,
        page_loaded_at: diagnostic?.page_loaded_at || contentIdentity.page_loaded_at,
        execution_diagnostic: execution,
        submission_performed: false,
      };
      await setJobState(jobId, "FAILED", { error_code: stale.error_code, error_detail: "The diagnostic response did not come from the content-script build verified immediately before execution. No result was accepted.", result: stale });
      await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...job, tab_id: tab.id, status: "FAILED", result: stale } });
      await closeAutomationTab(tab.id);
      return stale;
    }
    diagnostic.background_version = contentIdentity.background_version;
    diagnostic.execution_diagnostic = execution;
    const terminalStatus = diagnostic?.login_state === "LOGIN_REQUIRED" ? "LOGIN_REQUIRED" : diagnostic?.page_state === "FORM_AVAILABLE" ? "COMPLETED" : "FAILED";
    await chrome.storage.local.set({ posterproLastJob: { ...job, tab_id: tab.id, status: terminalStatus, diagnostic_result: diagnostic } });
    if (diagnostic?.login_state === "LOGIN_REQUIRED") {
      await setJobState(jobId, "LOGIN_REQUIRED", { error_code: "MARKETPLACE_LOGIN_REQUIRED", error_detail: "Sign in to the marketplace in this browser, then start the form test again.", result: diagnostic });
      await chrome.storage.local.set({ posterproActiveJob: null });
      await closeAutomationTab(tab.id);
      return diagnostic;
    }
    if (["CAPTCHA_REQUIRED", "CHECKPOINT_REQUIRED", "ACCOUNT_RESTRICTION", "UNKNOWN"].includes(diagnostic?.login_state)) {
      await setJobState(jobId, "BLOCKED_EXTERNAL", { error_code: diagnostic.login_state, error_detail: "The marketplace requires attention before PosterPro can inspect its listing form.", result: diagnostic });
      await chrome.storage.local.set({ posterproActiveJob: null });
      await closeAutomationTab(tab.id);
      return diagnostic;
    }
    await setJobState(jobId, "FORM_DETECTED", { result: diagnostic });
    await setJobState(jobId, "TESTING_FIELDS", { result: diagnostic });
    await setJobState(jobId, terminalStatus, {
      error_code: diagnostic?.capability_ready ? null : (diagnostic?.error_code || "DIAGNOSTIC_FIELDS_FAILED"),
      error_detail: diagnostic?.capability_ready ? null : (diagnostic?.missing_required_fields || []).join(", "),
      result: diagnostic,
    });
    await chrome.storage.local.set({ posterproActiveJob: null });
    await closeAutomationTab(tab.id);
    return diagnostic;
  }
  const contentIdentity = authorizedBrowserCreate
    ? await prepareDiagnosticContent(tab, execution, startUrl)
    : null;
  if (createMarketplace === "facebook" && authorizedBrowserCreate
    && contentIdentity?.content_script_build_id !== CDP_CONTROL_POLICIES["facebook:FACEBOOK_SHIPPING_UPDATE"].content_build) {
    const error = new Error("Facebook diagnostic content build does not match the authorized CDP delivery experiment; no listing form action was sent.");
    error.code = "CONTENT_BUILD_MISMATCH";
    error.executionDiagnostic = execution;
    throw error;
  }
  const facebookDescriptionRepair = String(job.action || "").toUpperCase() === "UPDATE"
    && job.marketplace === "facebook"
    && snapshot.repair_scope === "facebook_description_only"
    && Boolean(job.external_listing_id && job.external_url);
  const browserInputNonce = createMarketplace === "facebook" && (authorizedBrowserCreate || facebookDescriptionRepair) ? crypto.randomUUID() : null;
  if (String(job.action || "").toUpperCase() === "UPDATE") {
    await setJobState(jobId, "FORM_FILLING");
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["marketplace-adapters.js", "content.js"] });
    if (facebookDescriptionRepair) {
      const identity = await sendIdentityWithRetry(tab.id, execution);
      if (identity?.extension_version !== EXTENSION_VERSION
        || identity?.content_script_version !== EXTENSION_VERSION
        || identity?.content_script_build_id !== FACEBOOK_BUILD_ID) {
        await setJobState(jobId, "FAILED", { error_code: "FACEBOOK_DESCRIPTION_REPAIR_CONTENT_IDENTITY_MISMATCH", result: { identity } });
        await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...job, status: "FAILED", error_code: "FACEBOOK_DESCRIPTION_REPAIR_CONTENT_IDENTITY_MISMATCH", result: { identity } } });
        return { ok: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_CONTENT_IDENTITY_MISMATCH", identity, submission_performed: false };
      }
    }
    const updateActiveJob = {
      ...job, tab_id: tab.id, status: "FORM_FILLING",
      browser_input_nonce: facebookDescriptionRepair ? browserInputNonce : null,
      browser_input_stage: facebookDescriptionRepair ? "FACEBOOK_DESCRIPTION_REPAIR_SAVE" : null,
      browser_input_used: false,
      browser_input_content_build_id: facebookDescriptionRepair ? FACEBOOK_BUILD_ID : null,
    };
    await chrome.storage.local.set({ posterproActiveJob: updateActiveJob });
    let result;
    try {
      result = await chrome.tabs.sendMessage(tab.id, {
        action: "posterpro_update_exact_listing",
        marketplace: job.marketplace,
        payload: snapshot,
        description_repair_only: facebookDescriptionRepair,
        browser_input_nonce: facebookDescriptionRepair ? browserInputNonce : null,
        external_listing_id: job.external_listing_id || snapshot.external_listing_id || null,
        expected_external_url: job.external_url || snapshot.external_url || startUrl,
      });
    } catch (error) {
      if (!facebookDescriptionRepair) throw error;
      const latest = (await chrome.storage.local.get(["posterproActiveJob"])).posterproActiveJob || {};
      const reconciled = await reconcileFacebookDescriptionRepair(job, tab.id, snapshot, {
        error_code: "POST_SAVE_MESSAGE_CHANNEL_LOST",
        error: String(error?.message || error).slice(0, 180),
        save_activation_count: latest.browser_input_used === true ? 1 : 0,
        submission_performed: latest.browser_input_used === true,
      });
      await setJobState(jobId, reconciled.status, {
        external_listing_id: job.external_listing_id,
        external_url: job.external_url,
        error_code: reconciled.error_code || null,
        error_detail: reconciled.error_detail || null,
        result: reconciled,
      });
      await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...job, ...reconciled } });
      return reconciled;
    }
    if (!result?.identity_verified) {
      await setJobState(jobId, "BLOCKED_EXTERNAL", { error_code: result?.error_code || "EXTERNAL_IDENTITY_NOT_VERIFIED", error_detail: "The browser refused to edit because exact listing identity could not be verified.", result });
      await chrome.storage.local.set({ posterProActiveJob: null, posterProLastJob: { ...job, tab_id: tab.id, status: "BLOCKED_EXTERNAL", result } });
      return result;
    }
    if (!result?.ok && !(facebookDescriptionRepair && result?.submission_performed === true)) {
      await setJobState(jobId, "FAILED", { error_code: result?.error_code || "UPDATE_FORM_NOT_READY", error_detail: result?.error_code || "The exact listing was identified, but its edit form could not be prepared.", result });
      await chrome.storage.local.set({ posterProActiveJob: null, posterProLastJob: { ...job, tab_id: tab.id, status: "FAILED", result } });
      return result;
    }
    if (facebookDescriptionRepair) {
      const reconciled = await reconcileFacebookDescriptionRepair(job, tab.id, snapshot, result);
      await setJobState(jobId, reconciled.status, {
        external_listing_id: job.external_listing_id,
        external_url: job.external_url,
        error_code: reconciled.error_code || null,
        error_detail: reconciled.error_detail || null,
        result: reconciled,
      });
      await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...job, ...reconciled } });
      return reconciled;
    }
    await chrome.storage.local.set({ posterProActiveJob: { ...job, tab_id: tab.id, status: "AWAITING_OPERATOR_REVIEW", fill_result: result } });
    await setJobState(jobId, "AWAITING_OPERATOR_REVIEW", { result });
    return result;
  }
  await setJobState(jobId, "FORM_FILLING");
  if (browserInputNonce) await chrome.storage.local.set({ posterproActiveJob: {
    ...job, tab_id: tab.id, status: "FORM_FILLING", browser_input_nonce: browserInputNonce,
    browser_input_stage: "FACEBOOK_SHIPPING_UPDATE", browser_input_used: false,
    browser_input_content_build_id: contentIdentity?.content_script_build_id || null,
  } });
  if (createMarketplace === "facebook") {
    await requireFacebookFormProgress(jobId, tab.id, "FORM_FILLING_STARTED", "started", { listing_id: job.listing_id || null });
    await requireFacebookFormProgress(jobId, tab.id, "CLEAN_FORM_CHECK_STARTED", "started");
    const baseline = await raceWithTimeout(chrome.tabs.sendMessage(tab.id, {
      action: "posterpro_inspect_facebook_create_baseline", marketplace: "facebook",
    }), 20000, "FACEBOOK_FORM_READY_TIMEOUT", "Facebook clean-form baseline inspection did not return in time.");
    if (!baseline?.ok || !baseline?.clean) {
      await chrome.tabs.remove(tab.id).catch(() => {});
      await rememberAutomationTab(tab.id, true);
      if (tab.previous_automation_tab_id) await chrome.storage.local.set({ posterproAutomationTabId: tab.previous_automation_tab_id });
      else await chrome.storage.local.remove("posterproAutomationTabId");
      const error = new Error("Facebook CREATE did not start from a verified clean Details form.");
      error.code = baseline?.error_code || "FACEBOOK_DIRTY_CREATE_FORM";
      error.result = baseline || { ok: false, error_code: error.code };
      error.safe_pre_submit_retry = true;
      throw error;
    }
    await requireFacebookFormProgress(jobId, tab.id, "CLEAN_FORM_VERIFIED", "completed", {
      count: baseline.initial_photo_preview_count || 0,
    });
    if (tab.previous_automation_tab_id && tab.previous_automation_tab_id !== tab.id) {
      await chrome.tabs.remove(tab.previous_automation_tab_id).catch(() => {});
      await rememberAutomationTab(tab.previous_automation_tab_id, true);
    }
  }
  const images = await downloadCanonicalImages(snapshot, job.marketplace,
    createMarketplace === "facebook" ? { jobId, tabId: tab.id } : null);
  if (!contentIdentity) await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["marketplace-adapters.js", "content.js"] });
  else {
    const currentIdentity = await sendIdentityWithRetry(tab.id, execution);
    if (currentIdentity.content_script_version !== contentIdentity.content_script_version
      || currentIdentity.content_script_build_id !== contentIdentity.content_script_build_id
      || currentIdentity.extension_version !== contentIdentity.extension_version) {
      const error = new Error("The marketplace tab content script changed after identity verification; no listing form action was sent.");
      error.code = "CONTENT_VERSION_MISMATCH";
      error.executionDiagnostic = execution;
      throw error;
    }
  }
  const rawResult = createMarketplace === "facebook"
    ? await sendFacebookFormFillWithWatchdog(tab.id, jobId, { ...snapshot, browser_input_nonce: browserInputNonce }, images)
    : await chrome.tabs.sendMessage(tab.id, {
      action: "posterpro_fill_listing", marketplace: job.marketplace, payload: snapshot, images,
      browser_input_nonce: browserInputNonce,
    });
  const result = contentIdentity ? {
    ...rawResult,
    background_version: contentIdentity.background_version,
    extension_version: contentIdentity.extension_version,
    content_script_version: contentIdentity.content_script_version,
    content_script_build_id: contentIdentity.content_script_build_id,
    content_script_injected_at: contentIdentity.content_script_injected_at,
    page_loaded_at: contentIdentity.page_loaded_at,
  } : rawResult;
  // Keep actual field/page evidence attached to failures. Without this, a
  // real operator-authorized CREATE is persisted as a generic execution error.
  requireMarketplaceFormReady(result, createMarketplace);
  if (String(responseAction(job) || "") === "CREATE" && ["mercari", "facebook"].includes(createMarketplace)) {
    if (result.capability_ready !== true || (result.missing_required_fields || []).length) {
      const error = new Error(`${createMarketplace} form did not pass full required-field verification; no submit action was taken.`);
      error.code = "REQUIRED_FIELDS_UNRESOLVED";
      error.result = result;
      throw error;
    }
    const submitActiveJob = {
      ...job, tab_id: tab.id, status: "SUBMITTING", browser_input_nonce: browserInputNonce,
      browser_input_stage: createMarketplace === "facebook" ? "FACEBOOK_SHIPPING_UPDATE" : null,
      browser_input_used: false, browser_input_content_build_id: contentIdentity?.content_script_build_id || null,
    };
    await chrome.storage.local.set({ posterproActiveJob: submitActiveJob });
    await setJobState(jobId, "SUBMITTING", { result: { ...result, submission_performed: false, submit_guard: "all_required_fields_verified" } });
    let rawSubmitted;
    try {
      rawSubmitted = await chrome.tabs.sendMessage(tab.id, {
        action: "posterpro_submit_operator_authorized_listing", marketplace: job.marketplace,
        payload: snapshot, browser_input_nonce: browserInputNonce,
        prior_form_readiness: createMarketplace === "facebook" ? result.facebook_form_readiness || null : null,
      });
    } catch (error) {
      if (createMarketplace !== "facebook") throw error;
      const reconciled = await reconcileLostFacebookSubmit(job, tab.id, snapshot, error);
      await chrome.storage.local.set({
        posterproActiveJob: null,
        posterproLastJob: { ...job, tab_id: tab.id, status: reconciled?.status || "FAILED", result: reconciled?.result || null, error_code: reconciled?.error_code || null },
      });
      return reconciled;
    }
    const submitted = contentIdentity ? {
      ...rawSubmitted,
      background_version: contentIdentity.background_version,
      extension_version: contentIdentity.extension_version,
      content_script_version: contentIdentity.content_script_version,
      content_script_build_id: contentIdentity.content_script_build_id,
      content_script_injected_at: contentIdentity.content_script_injected_at,
      page_loaded_at: contentIdentity.page_loaded_at,
    } : rawSubmitted;
    const trustedFacebookIdentity = createMarketplace === "facebook"
      ? validateFacebookSubmitIdentity(submitted, snapshot)
      : null;
    if (createMarketplace === "facebook" && submitted?.submission_performed === true) {
      const acceptedResult = {
        ...submitted,
        external_listing_id: trustedFacebookIdentity?.external_listing_id || null,
        external_url: trustedFacebookIdentity?.external_url || null,
        identity_capture_state: trustedFacebookIdentity ? "TITLE_PRICE_BOUND" : "PENDING_RECONCILIATION",
        submission_state: "SUBMITTED_PENDING_REVIEW",
        submission_performed: true,
      };
      await chrome.storage.local.set({ posterproActiveJob: { ...job, tab_id: tab.id, status: "SUBMITTED", submit_result: acceptedResult } });
      await setJobState(jobId, "SUBMITTED", {
        external_listing_id: trustedFacebookIdentity?.external_listing_id || null,
        external_url: trustedFacebookIdentity?.external_url || null,
        result: acceptedResult,
      });
      const pending = await setJobState(jobId, "SUBMITTED_PENDING_REVIEW", {
        external_listing_id: trustedFacebookIdentity?.external_listing_id || null,
        external_url: trustedFacebookIdentity?.external_url || null,
        result: acceptedResult,
      });
      await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: pending });
      return pending;
    }
    if (submitted?.submission_performed) {
      await chrome.storage.local.set({ posterproActiveJob: { ...job, tab_id: tab.id, status: "SUBMITTED", submit_result: submitted } });
      await setJobState(jobId, "SUBMITTED", { external_listing_id: submitted.external_listing_id || null, external_url: submitted.external_url || null, result: submitted });
    }
    if (createMarketplace === "facebook" && submitted?.submission_performed
      && !(submitted.external_listing_id && submitted.external_url)) {
      const reconciled = await reconcileLostFacebookSubmit(job, tab.id, snapshot, "submit response contained no remote identity");
      await chrome.storage.local.set({
        posterproActiveJob: null,
        posterproLastJob: { ...job, tab_id: tab.id, status: reconciled?.status || "FAILED", result: reconciled?.result || submitted, error_code: reconciled?.error_code || null },
      });
      return reconciled;
    }
    if (!submitted?.ok || (createMarketplace === "facebook"
      ? !(submitted.external_listing_id && submitted.external_url)
      : !(submitted.external_listing_id || submitted.external_url))) {
      const error = new Error(`${createMarketplace} submit outcome is not confirmed; reconcile the marketplace before any retry.`);
      error.code = submitted?.submission_performed === false
        ? (submitted?.error_code || "MARKETPLACE_FINAL_SUBMIT_NOT_REACHED")
        : "SUBMISSION_OUTCOME_UNKNOWN";
      const failureState = "FAILED";
      await setJobState(jobId, failureState, { error_code: error.code, error_detail: String(submitted?.confirmation || error.message), result: submitted });
      await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...job, tab_id: tab.id, status: failureState, result: submitted, error: error.message } });
      return submitted;
    }
    if (createMarketplace === "facebook") {
      let remoteDescriptionVerification = null;
      try {
        const identityUrl = String(submitted.external_url || "");
        if (!/^https:\/\/(?:www\.)?facebook\.com\/marketplace\/item\/[^/?#]+\/?(?:[?#].*)?$/i.test(identityUrl)) {
          throw new Error("Captured Facebook identity URL is not an exact Marketplace item URL.");
        }
        await chrome.tabs.update(tab.id, { url: identityUrl, active: false });
        await waitForTabComplete(tab.id, 60000, { allowInteractiveFallback: true });
        const remote = await facebookReconciliationOnTab(tab.id, snapshot, false);
        remoteDescriptionVerification = remote?.description_verification || null;
      } catch (error) {
        remoteDescriptionVerification = {
          status: "UNAVAILABLE", matches: false,
          expected_length: String(snapshot.description || "").replace(/\s+/g, " ").trim().length,
          observed_length: 0, expected_hash: null, observed_hash: null,
          error_code: "FACEBOOK_REMOTE_DESCRIPTION_VERIFY_FAILED",
          error: String(error?.message || error).slice(0, 180),
        };
      }
      submitted.remote_description_verification = remoteDescriptionVerification;
      if (remoteDescriptionVerification?.matches !== true) {
        const held = await setJobState(jobId, "AWAITING_OPERATOR_REVIEW", {
          external_listing_id: submitted.external_listing_id,
          external_url: submitted.external_url,
          error_code: "FACEBOOK_DESCRIPTION_REPAIR_REQUIRED",
          error_detail: "Remote listing identity is verified, but its authenticated Facebook page did not prove the canonical description is present. Do not CREATE again; repair this exact remote listing.",
          result: { ...submitted, submission_performed: true, remote_exists_verified: true },
        });
        await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...job, tab_id: tab.id, status: "AWAITING_OPERATOR_REVIEW", result: { ...submitted, remote_exists_verified: true }, error_code: "FACEBOOK_DESCRIPTION_REPAIR_REQUIRED" } });
        return { ...held, result: { ...submitted, remote_exists_verified: true } };
      }
    }
    const completed = await setJobState(jobId, "COMPLETED", { external_listing_id: submitted.external_listing_id || null, external_url: submitted.external_url || null, result: submitted });
    await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: completed });
    return submitted;
  }
    if (facebookDescriptionRepair) {
      let reconciled;
      try { reconciled = await reconcileFacebookDescriptionRepair(job, tab.id, snapshot, result); }
      catch (error) { reconciled = { status: "AWAITING_OPERATOR_REVIEW", error_code: "FACEBOOK_DESCRIPTION_REPAIR_OUTCOME_UNKNOWN", error_detail: String(error?.message || error).slice(0, 180) }; }
      await setJobState(jobId, reconciled.status, {
        external_listing_id: job.external_listing_id,
        external_url: job.external_url,
        error_code: reconciled.error_code || null,
        error_detail: reconciled.error_detail || null,
        result: { ...result, ...reconciled },
      });
      await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...job, ...reconciled, result: { ...result, ...reconciled } } });
      return { ...result, ...reconciled };
    }
    await chrome.storage.local.set({ posterproActiveJob: { ...job, tab_id: tab.id, status: "AWAITING_OPERATOR_REVIEW", fill_result: result } });
    await setJobState(jobId, "AWAITING_OPERATOR_REVIEW", { result });
  return result;
}

function validateFacebookSubmitIdentity(submitted, payload) {
  const acceptedEvidence = new Set([
    "DIRECT_ITEM_PAGE_EXACT_TITLE_PRICE",
    "NEW_POST_SUBMIT_ITEM_LINK_EXACT_TITLE_PRICE",
  ]);
  if (!acceptedEvidence.has(String(submitted?.identity_evidence || ""))) return null;
  const remoteId = String(submitted.external_listing_id || "").trim();
  const remoteUrl = String(submitted.external_url || "").trim();
  let parsed;
  try { parsed = new URL(remoteUrl); } catch (_) { return null; }
  const pathId = parsed.pathname.match(/^\/marketplace\/item\/([^/?#]+)\/?$/i)?.[1];
  if (parsed.protocol !== "https:" || !/(^|\.)facebook\.com$/i.test(parsed.hostname)
    || !remoteId || pathId !== remoteId) return null;
  const normalizeTitle = (value) => String(value || "").normalize("NFKC").replace(/\s+/g, " ").trim().toLocaleLowerCase();
  if (!normalizeTitle(submitted.observed_title)
    || normalizeTitle(submitted.observed_title) !== normalizeTitle(payload.title)) return null;
  const expectedPrice = Number(String(payload.price ?? "").replace(/[^0-9.]/g, ""));
  const observedPrice = Number(String(submitted.observed_price ?? "").replace(/[^0-9.]/g, ""));
  if (!Number.isFinite(expectedPrice) || !Number.isFinite(observedPrice)) return null;
  const exactPrice = Math.abs(expectedPrice - observedPrice) < 0.005;
  const roundedPrice = Math.abs(Math.round(expectedPrice) - observedPrice) < 0.005
    && Math.abs(expectedPrice - Math.round(expectedPrice)) <= 0.0100001;
  if (!exactPrice && !roundedPrice) return null;
  return { external_listing_id: remoteId, external_url: `https://www.facebook.com/marketplace/item/${encodeURIComponent(remoteId)}/` };
}

function responseAction(job) {
  return String(job?.action || "CREATE").toUpperCase();
}

function isExpiredPreSubmitFacebookCreate(activeJob, remoteJob, now = Date.now()) {
  if (!activeJob?.id || Number(remoteJob?.id) !== Number(activeJob.id)) return false;
  if (String(activeJob.marketplace || "").toLowerCase() !== "facebook"
    || responseAction(activeJob) !== "CREATE"
    || String(activeJob.status || "").toUpperCase() !== "FORM_FILLING") return false;
  if (String(remoteJob?.marketplace || "").toLowerCase() !== "facebook"
    || String(remoteJob?.action || "").toUpperCase() !== "CREATE"
    || String(remoteJob?.status || "").toUpperCase() !== "FORM_FILLING") return false;
  const leaseExpiry = Date.parse(remoteJob.lease_expires_at || "");
  if (!Number.isFinite(leaseExpiry) || leaseExpiry > now) return false;
  const result = remoteJob.result && typeof remoteJob.result === "object" ? remoteJob.result : {};
  return !remoteJob.external_listing_id && !remoteJob.external_url
    && result.submission_performed !== true
    && remoteJob.error_code !== "SUBMISSION_OUTCOME_UNKNOWN";
}

async function renewRunningMarketplaceJobLease() {
  if (activeJobLeaseRenewal) return;
  activeJobLeaseRenewal = true;
  try {
    const { token, activeJob } = await getAgentSettings();
    if (!token || !activeJob?.id
      || !["CLAIMED", "NAVIGATING", "FORM_FILLING", "SUBMITTING", "SUBMITTED"].includes(String(activeJob.status || "").toUpperCase())) return;
    await deviceRequest("/browser-extension/heartbeat", { method: "POST", body: JSON.stringify({ browser: "Chrome", extension_version: EXTENSION_VERSION }) });
    await deviceRequest(`/browser-extension/jobs/${encodeURIComponent(activeJob.id)}/lease`, { method: "POST", body: JSON.stringify({}) });
  } catch (error) {
    await chrome.storage.local.set({ posterproAgentError: String(error?.message || error), posterproAgentErrorAt: new Date().toISOString() }).catch(() => {});
  } finally {
    activeJobLeaseRenewal = false;
  }
}

async function pollMarketplaceQueue() {
  if (queuePollActive) {
    // A long-running content-script transaction must not starve its own lease
    // renewal. The alarm still refreshes the paired-device heartbeat and lease
    // while the single queue consumer remains active.
    await renewRunningMarketplaceJobLease();
    return;
  }
  queuePollActive = true;
  try {
    let { token, activeJob } = await getAgentSettings();
    if (!token) return;
    const heartbeat = await deviceRequest("/browser-extension/heartbeat", { method: "POST", body: JSON.stringify({ browser: "Chrome", extension_version: EXTENSION_VERSION }) });
    if (heartbeat?.device) await chrome.storage.local.set({ posterproDevice: heartbeat.device });
    if (activeJob?.id) {
      if (["CLAIMED", "NAVIGATING", "FORM_FILLING", "SUBMITTING", "SUBMITTED"].includes(String(activeJob.status || "").toUpperCase())) {
        try {
          await deviceRequest(`/browser-extension/jobs/${encodeURIComponent(activeJob.id)}/lease`, { method: "POST", body: JSON.stringify({}) });
        } catch (error) {
          const message = String(error?.message || error);
          if (message.includes("Claimed assisted job not found")) {
            await chrome.storage.local.set({ posterproActiveJob: null });
            activeJob = null;
          } else if (message.includes("Job lease has expired")) {
            const remoteJob = await deviceRequest(`/browser-extension/jobs/${encodeURIComponent(activeJob.id)}/status`);
            if (!isExpiredPreSubmitFacebookCreate(activeJob, remoteJob)) throw error;
            // The remote state machine enters SUBMITTING before any final
            // Publish activation. Only an expired, identity-free FORM_FILLING
            // CREATE can be released back to the ordinary same-job claim path.
            await chrome.storage.local.set({ posterproActiveJob: null });
            activeJob = null;
          } else throw error;
        }
      } else if (String(activeJob.status || "").toUpperCase() === "AWAITING_OPERATOR_REVIEW") {
        let remote = null;
        try {
          remote = await deviceRequest(`/browser-extension/jobs/${encodeURIComponent(activeJob.id)}/status`);
        } catch (error) {
          if (!String(error?.message || error).includes("Claimed assisted job not found")) throw error;
        }
        if (!remote || ["COMPLETED", "FAILED", "CANCELLED", "QUEUED", "RETRYABLE"].includes(String(remote.status || "").toUpperCase())) {
          await chrome.storage.local.set({ posterproActiveJob: null });
          activeJob = null;
        }
      }
    if (activeJob?.id) return;
    }
    // Resolve ambiguous Facebook CREATE outcomes using the authenticated seller
    // session before any ordinary queue work. This path is read-only and runs
    // even while CREATE automation is locally paused.
    const reconciliation = await deviceRequest("/browser-extension/facebook-reconciliation/claim", { method: "POST", body: JSON.stringify({}) });
    if (reconciliation?.task) {
      await runFacebookReconciliationTask(reconciliation.task);
      return;
    }
    const localState = await chrome.storage.local.get(["posterproAutomationPaused"]);
    if (localState.posterproAutomationPaused) return;
    const response = await deviceRequest("/browser-extension/jobs/claim", { method: "POST", body: JSON.stringify({}) });
    if (!response?.job) return;
    await chrome.storage.local.set({ posterproLastJob: response.job });
    try {
      const result = await runClaimedJob(response.job);
      const localResult = await chrome.storage.local.get(["posterproActiveJob", "posterproLastJob"]);
      if (String(localResult.posterproActiveJob?.status || "").toUpperCase() === "AWAITING_OPERATOR_REVIEW") {
        await chrome.storage.local.set({ posterproLastJob: { ...response.job, ...localResult.posterproActiveJob, status: "AWAITING_OPERATOR_REVIEW", fill_result: result } });
      }
    } catch (error) {
      const executionDiagnostic = error?.executionDiagnostic || (String(response.job.action || "").toUpperCase() === "DIAGNOSTIC" || response.job.payload?.diagnostic ? makeExecutionDiagnostic() : null);
      if (executionDiagnostic && !executionDiagnostic.error_code) {
        executionDiagnostic.error_code = error?.code || "EXTENSION_EXECUTION_FAILED";
        executionDiagnostic.error_name = String(error?.name || "Error").slice(0, 80);
        executionDiagnostic.error_message = String(error?.message || error).slice(0, 600);
        executionDiagnostic.failed_at = new Date().toISOString();
      }
      const failureCode = error?.code || "EXTENSION_EXECUTION_FAILED";
      if (String(response.job.marketplace || "").toLowerCase() === "facebook"
        && String(response.job.action || "CREATE").toUpperCase() === "CREATE"
        && error?.safe_pre_submit_retry === true) {
        if (error?.code === "FACEBOOK_FORM_CANCEL_UNCONFIRMED") {
          // Keep the durable job in FORM_FILLING and retain the local lease:
          // the page may still be mutating, so another job must not reuse it.
          await chrome.storage.local.set({ posterproAgentError: failureCode, posterproAgentErrorAt: new Date().toISOString() });
          return;
        }
        const progress = activeFacebookFormProgress.get(Number(response.job.id)) || {};
        const lastProgressAt = progress.lastProgressAt ? new Date(progress.lastProgressAt).toISOString() : null;
        const retryable = Number(response.job.attempt_count || 1) < 2;
        await setJobState(response.job.id, retryable ? "RETRYABLE" : "FAILED", {
          error_code: retryable ? failureCode : "FACEBOOK_FORM_STALL_RETRY_EXHAUSTED",
          error_detail: String(error?.message || error).slice(0, 1800),
          result: {
            ...(error?.result && typeof error.result === "object" ? error.result : {}),
            submission_performed: false,
            safe_pre_submit_retry: retryable,
            last_progress_stage: error.last_progress_stage || progress.stage || null,
            last_progress_at: error.last_progress_at || lastProgressAt,
            progress_persist_error: progress.lastProgressPersistError || null,
          },
        }).catch(() => {});
        await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...response.job, status: retryable ? "RETRYABLE" : "FAILED", error_code: retryable ? failureCode : "FACEBOOK_FORM_STALL_RETRY_EXHAUSTED" } });
        activeFacebookFormProgress.delete(Number(response.job.id));
        return;
      }
      const failureDetails = {
        error_code: failureCode,
        error_detail: String(error?.message || error).slice(0, 1800),
        ...(error?.result && typeof error.result === "object" ? { result: { ...error.result, failure_stage: error.result.stage || "FORM_FILLING", submission_performed: error.result.submission_performed === true } } : {}),
        ...(executionDiagnostic ? { result: { ...(error?.result && typeof error.result === "object" ? error.result : {}), execution_diagnostic: executionDiagnostic, background_version: executionDiagnostic.background_version, extension_version: executionDiagnostic.background_version, submission_performed: false } } : {}),
      };
      await setJobState(response.job.id, "FAILED", failureDetails).catch(() => {});
      if (String(response.job.action || "").toUpperCase() === "DIAGNOSTIC" || response.job.payload?.diagnostic) {
        const stored = await chrome.storage.local.get(["posterproAutomationTabId"]);
        if (stored.posterproAutomationTabId) await closeAutomationTab(stored.posterproAutomationTabId);
      }
      await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: { ...response.job, status: "FAILED", error_code: failureCode, error: String(error?.message || error), execution_diagnostic: executionDiagnostic } });
    }
  } catch (error) {
    await chrome.storage.local.set({ posterproAgentError: String(error?.message || error), posterproAgentErrorAt: new Date().toISOString() });
  } finally {
    queuePollActive = false;
  }
}

async function completeActiveJob({ externalListingId, externalUrl }) {
  const { activeJob } = await getAgentSettings();
  if (!activeJob?.id) throw new Error("There is no active assisted job awaiting operator review.");
  const isEnd = String(activeJob.action || "").toUpperCase() === "END";
  const resolvedId = externalListingId || activeJob.external_listing_id || activeJob.payload?.external_listing_id;
  const resolvedUrl = externalUrl || activeJob.external_url || activeJob.payload?.external_url;
  if (!resolvedId && !resolvedUrl) throw new Error("A confirmed marketplace listing ID or URL is required.");
  const id = activeJob.id;
  await setJobState(id, "SUBMITTING");
  await setJobState(id, "SUBMITTED", { external_listing_id: resolvedId || null, external_url: resolvedUrl || null });
  const result = await setJobState(id, "COMPLETED", {
    external_listing_id: resolvedId || null,
    external_url: resolvedUrl || null,
    result: isEnd ? { operator_confirmed_end: true } : { operator_confirmed_submission: true },
  });
  await chrome.storage.local.set({ posterproActiveJob: null, posterproLastJob: result });
  await pollMarketplaceQueue();
  return result;
}

async function pairDevice(pairingCode, deviceName) {
  const { api } = await getAgentSettings();
  const response = await fetch(`${api}/browser-extension/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pairing_code: pairingCode, device_name: deviceName || "PosterPro browser", browser: "Chrome", extension_version: EXTENSION_VERSION }),
  });
  const text = await response.text();
  let result = null;
  try { result = text ? JSON.parse(text) : null; } catch { result = { detail: text }; }
  if (!response.ok) throw new Error(result?.detail || `Pairing failed (${response.status})`);
  await chrome.storage.local.set({ posterproDeviceToken: result.device_token, posterproDevice: result.device, posterproActiveJob: null, posterproAgentError: null });
  // Pairing is the durable authorization step.  Queue polling is a separate
  // transport concern; a transient heartbeat/queue error after the server has
  // issued a token must not make the page report that pairing itself failed.
  void pollMarketplaceQueue();
  return result.device;
}

async function loadSettings() {
  const stored = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  return {
    posterproBaseUrl: String(stored.posterproBaseUrl || DEFAULT_SETTINGS.posterproBaseUrl).trim().replace(/\/+$/, ""),
    defaultMarketplace: String(stored.defaultMarketplace || DEFAULT_SETTINGS.defaultMarketplace).trim().toLowerCase(),
  };
}

function inferMarketplace(url) {
  const normalized = String(url || "").toLowerCase();
  const hint = MARKETPLACE_HOST_HINTS.find((item) => normalized.includes(item.match));
  return hint ? hint.marketplace : "mercari";
}

function safeJson(value) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return null;
  }
}

async function captureActiveSession() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id || !tab.url) {
    throw new Error("No active marketplace tab was found.");
  }
  const [pageSnapshot] = await Promise.all([
    chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        title: document.title,
        url: window.location.href,
        hostname: window.location.hostname,
      }),
    }),
  ]);

  const snapshot = {
    captured_at: new Date().toISOString(),
    marketplace: inferMarketplace(tab.url),
    session_material_exported: false,
    tab: {
      id: tab.id,
      title: tab.title || null,
      url: `${new URL(tab.url).origin}${new URL(tab.url).pathname}`,
    },
    page: safeJson(pageSnapshot?.[0]?.result) || {},
  };
  if (snapshot.page && typeof snapshot.page === "object") {
    snapshot.page.url = snapshot.tab.url;
  }
  await chrome.storage.local.set({ posterproLastSessionSnapshot: snapshot });
  return snapshot;
}

async function sendSessionToPosterPro(payload) {
  const settings = await loadSettings();
  const baseUrl = apiRoot(settings.posterproBaseUrl);
  if (!baseUrl) {
    throw new Error("PosterPro base URL is not configured in extension options.");
  }
  const response = await fetch(`${baseUrl}/browser-extension/sessions/import`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!response.ok) {
    const detail = data?.detail || data?.message || `PosterPro import failed (${response.status})`;
    throw new Error(String(detail));
  }
  await chrome.storage.local.set({
    posterproLastImportResponse: data,
    posterproLastImportAt: new Date().toISOString(),
  });
  return data;
}

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  await chrome.storage.sync.set({
    posterproBaseUrl: stored.posterproBaseUrl || DEFAULT_SETTINGS.posterproBaseUrl,
    defaultMarketplace: stored.defaultMarketplace || DEFAULT_SETTINGS.defaultMarketplace,
  });
  chrome.alarms.create("posterpro-marketplace-poll", { periodInMinutes: 0.5 });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("posterpro-marketplace-poll", { periodInMinutes: 0.5 });
  pollMarketplaceQueue();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "posterpro-marketplace-poll") pollMarketplaceQueue();
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== "object") {
    return false;
  }

  const action = String(message.action || "").toLowerCase();
  if (action === "posterpro_facebook_form_progress") {
    (async () => {
      const jobId = Number(message.internal_job_id);
      const senderTabId = Number(sender?.tab?.id);
      const { posterproActiveJob } = await chrome.storage.local.get(["posterproActiveJob"]);
      if (!Number.isInteger(jobId) || !posterproActiveJob || Number(posterproActiveJob.id) !== jobId
        || String(posterproActiveJob.marketplace || "").toLowerCase() !== "facebook"
        || String(posterproActiveJob.action || "CREATE").toUpperCase() !== "CREATE"
        || String(posterproActiveJob.status || "").toUpperCase() !== "FORM_FILLING"
        || Number(posterproActiveJob.tab_id) !== senderTabId
        || !/(^|\.)facebook\.com$/i.test(new URL(String(sender?.url || "")).hostname)) {
        return { ok: false, error_code: "FACEBOOK_FORM_PROGRESS_IDENTITY_MISMATCH" };
      }
      return setFacebookFormProgress(jobId, senderTabId, String(message.stage || ""), String(message.phase || "checkpoint"), message.detail || {});
    })().then(sendResponse).catch((error) => sendResponse({ ok: false, error_code: "FACEBOOK_FORM_PROGRESS_FAILED", error: String(error?.message || error).slice(0, 160) }));
    return true;
  }
  if (action === "probe_active_facebook_description_dom") {
    probeActiveFacebookDescriptionDom()
      .then(sendResponse)
      .catch((error) => sendResponse({ ok: false, error_code: "FACEBOOK_DESCRIPTION_PROBE_FAILED", error: String(error?.message || error).slice(0, 200) }));
    return true;
  }
  if (action === "posterpro_begin_facebook_description_repair_save") {
    beginFacebookDescriptionRepairSave(message, sender)
      .then(sendResponse)
      .catch((error) => sendResponse({ ok: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_SAVE_AUTH_FAILED", error: String(error?.message || error).slice(0, 180) }));
    return true;
  }
  if (action === "posterpro_marketplace_control_activation") {
    handleMarketplaceControlRequest(message, sender)
      .then(sendResponse)
      .catch((error) => sendResponse({ ok: false, error_code: "FACEBOOK_CDP_INPUT_FAILED", error: String(error?.message || error).slice(0, 240) }));
    return true;
  }
  if (action === "posterpro_release_marketplace_focus_lease") {
    releaseMarketplaceFocusLease(message, sender)
      .then(sendResponse)
      .catch((error) => sendResponse({ ok: false, error_code: "MARKETPLACE_FOCUS_LEASE_RELEASE_FAILED", error: String(error?.message || error).slice(0, 180) }));
    return true;
  }
  if (action === "posterpro_read_marketplace_focus") {
    const tabId = Number(sender?.tab?.id);
    if (!Number.isInteger(tabId) || tabId <= 0) { sendResponse({ ok: false, error_code: "MARKETPLACE_TAB_UNAVAILABLE" }); return false; }
    readMarketplaceTabContext(tabId)
      .then((context) => sendResponse({ ok: true, context }))
      .catch((error) => sendResponse({ ok: false, error_code: "MARKETPLACE_FOCUS_READ_FAILED", error: String(error?.message || error).slice(0, 180) }));
    return true;
  }
  if (action === "capture_session") {
    captureActiveSession()
      .then((snapshot) => sendResponse({ ok: true, snapshot }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (action === "send_to_posterpro") {
    sendSessionToPosterPro(message.payload || {})
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (action === "get_state") {
    Promise.all([
      chrome.storage.sync.get(DEFAULT_SETTINGS),
      chrome.storage.local.get(["posterproLastSessionSnapshot", "posterproLastImportResponse", "posterproLastImportAt", "posterproDevice", "posterproDeviceToken", "posterproActiveJob", "posterproLastJob", "posterproLastExecutionDiagnostic", "posterproAgentError", "posterproAgentErrorAt", "posterproAutomationPaused"]),
    ]).then(([settings, local]) => {
      sendResponse({ ok: true, settings, local });
    });
    return true;
  }

  if (action === "pair_device") {
    pairDevice(String(message.pairingCode || "").trim(), String(message.deviceName || "PosterPro browser"))
      .then((device) => sendResponse({ ok: true, device }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (action === "get_connection_status") {
    chrome.storage.local.get(["posterproDevice"]).then(({ posterproDevice }) => {
      const device = posterproDevice && typeof posterproDevice === "object" ? posterproDevice : null;
      sendResponse({
        ok: true,
        device: device ? {
          id: Number.isInteger(device.id) ? device.id : null,
          browser: String(device.browser || "").slice(0, 32),
          extension_version: String(device.extension_version || EXTENSION_VERSION).slice(0, 32),
          last_seen_at: typeof device.last_seen_at === "string" ? device.last_seen_at.slice(0, 64) : null,
        } : null,
      });
    }).catch(() => sendResponse({ ok: true, device: null }));
    return true;
  }

  if (action === "check_for_update") {
    // Automatic updates are available for Web Store/enterprise installs. An
    // unpacked developer install cannot be replaced by a webpage; Chrome
    // reports no_update and the PosterPro UI presents the safe reload fallback.
    if (typeof chrome.runtime.requestUpdateCheck !== "function") {
      sendResponse({ ok: false, status: "unsupported", version: EXTENSION_VERSION });
      return false;
    }
    chrome.runtime.requestUpdateCheck((status, details) => {
      sendResponse({ ok: true, status: String(status || "unknown"), version: details?.version || EXTENSION_VERSION });
    });
    return true;
  }

  if (action === "poll_queue") {
    pollMarketplaceQueue()
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (action === "set_automation_paused") {
    chrome.storage.local.set({ posterproAutomationPaused: Boolean(message.paused) })
      .then(() => sendResponse({ ok: true, paused: Boolean(message.paused) }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (action === "complete_active_job") {
    completeActiveJob({ externalListingId: String(message.externalListingId || "").trim(), externalUrl: String(message.externalUrl || "").trim() })
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  return false;
});
