globalThis.__POSTERPRO_CONTENT_SCRIPT_IDENTITY = {
  content_script_version: "0.3.70",
  content_script_build_id: "posterpro-0.3.70-facebook-background-queue-focus-safety",
  content_script_injected_at: new Date().toISOString(),
};
const PP_FACEBOOK_UPDATE_COMMIT_DIAGNOSTIC_BUILD = globalThis.__POSTERPRO_CONTENT_SCRIPT_IDENTITY.content_script_build_id === "posterpro-0.3.48-facebook-form-readiness-diagnostic";
let ppActiveMarketplaceFocusLeaseId = null;
let ppFacebookActiveFormRun = null;
const ppFacebookNextNodeIds = new WeakMap();
let ppFacebookNextNodeSequence = 0;
const ppFacebookDescriptionNodeIds = new WeakMap();
let ppFacebookDescriptionNodeSequence = 0;

function ppContentScriptIdentity() {
  let extensionVersion = null;
  try { extensionVersion = chrome.runtime?.getManifest?.().version || null; } catch (_) { /* runtime metadata unavailable */ }
  let pageLoadedAt = null;
  try { pageLoadedAt = Number.isFinite(performance.timeOrigin) ? new Date(performance.timeOrigin).toISOString() : new Date().toISOString(); } catch (_) { pageLoadedAt = new Date().toISOString(); }
  return { ...globalThis.__POSTERPRO_CONTENT_SCRIPT_IDENTITY, extension_version: extensionVersion, page_loaded_at: pageLoadedAt };
}

async function ppReleaseMarketplaceFocusLease(leaseId) {
  if (!leaseId) return { ok: true, skipped: true };
  try {
    return await chrome.runtime.sendMessage({
      action: "posterpro_release_marketplace_focus_lease",
      focus_lease_id: leaseId,
    });
  } catch (error) {
    return { ok: false, error_code: "MARKETPLACE_FOCUS_LEASE_RELEASE_FAILED", error: ppSafeEvidenceText(String(error?.message || error), 180) };
  }
}

function ppFindField(selectors, labelPatterns) {
  for (const selector of selectors || []) {
    const node = document.querySelector(selector);
    if (node && !node.disabled && node.getAttribute("aria-hidden") !== "true") return node;
  }
  for (const label of document.querySelectorAll("label")) {
    const text = String(label.innerText || label.getAttribute("aria-label") || "").trim().toLowerCase();
    if ((labelPatterns || []).some((pattern) => text.includes(pattern))) {
      // Facebook's current Marketplace form uses the label element itself as
      // the interactive combobox (role=combobox), with no nested input or
      // label.control reference. Treat that semantic label as the field node
      // so the option picker can open it and inspect the resulting menu.
      if (label.getAttribute("role") === "combobox" && !label.disabled) return label;
      const control = label.control || label.querySelector("input,textarea,select,[role=combobox],button");
      if (control && !control.disabled) return control;
    }
  }
  // Facebook's current form often renders semantic controls as plain buttons
  // whose visible label is present only in innerText (not aria-label/name).
  // Include that bounded label evidence so a visibly usable Category/Condition
  // control is not reported as FIELD_NOT_FOUND.
  for (const node of document.querySelectorAll("input,textarea,select,[contenteditable='true'],[role='combobox'],button")) {
    const text = `${node.getAttribute("aria-label") || ""} ${node.getAttribute("placeholder") || ""} ${node.getAttribute("name") || ""} ${node.innerText || node.textContent || ""}`.toLowerCase();
    if ((labelPatterns || []).some((pattern) => text.includes(pattern)) && !node.disabled) return node;
  }
  // Facebook sometimes puts the semantic label on a wrapper while the
  // clickable control itself is an unlabeled div/button. Inspect only the
  // nearest visible control wrapper; never scrape the whole page or use
  // customer data as selector evidence.
  for (const node of document.querySelectorAll("[role='button'],button,[role='combobox']")) {
    if (node.disabled || node.offsetParent === null) continue;
    const wrapperText = `${node.innerText || node.textContent || ""} ${node.parentElement?.innerText || ""}`
      .replace(/\s+/g, " ").slice(0, 240).toLowerCase();
    if ((labelPatterns || []).some((pattern) => wrapperText.includes(pattern))) return node;
  }
  // Facebook's current first-step form can render unlabeled text controls.
  // Use the same bounded order fallback as the headless bridge: title is the
  // first visible text input, price is the numeric/second text input, and the
  // description is the first textarea/contenteditable control.
  const visibleTextInputs = Array.from(document.querySelectorAll('input[type="text"], input:not([type]), input[inputmode="numeric"], input[type="number"]'))
    .filter((node) => node.offsetParent !== null && !node.disabled);
  const visibleDescriptions = Array.from(document.querySelectorAll('textarea, [contenteditable="true"]'))
    .filter((node) => node.offsetParent !== null && !node.disabled);
  const patterns = (labelPatterns || []).map((pattern) => String(pattern).toLowerCase());
  if (patterns.includes("title") && visibleTextInputs[0]) return visibleTextInputs[0];
  if (patterns.includes("price")) {
    const numeric = visibleTextInputs.find((node) => node.type === "number" || node.getAttribute("inputmode") === "numeric");
    if (numeric) return numeric;
    if (visibleTextInputs[1]) return visibleTextInputs[1];
  }
  if ((patterns.includes("description") || patterns.includes("describe")) && visibleDescriptions[0]) return visibleDescriptions[0];
  return null;
}

function ppSetValue(node, value) {
  if (!node || value == null || value === "") return false;
  if (node.tagName === "SELECT") {
    const wanted = String(value).trim().toLowerCase();
    const option = Array.from(node.options || []).find((item) => String(item.textContent || item.label || "").trim().toLowerCase() === wanted);
    if (!option) return false;
    node.value = option.value;
  } else if (node.isContentEditable) {
    node.focus(); node.textContent = String(value);
  } else if ("value" in node) {
    node.focus();
    const prototype = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (setter) setter.call(node, String(value)); else node.value = String(value);
  } else {
    return false;
  }
  node.dispatchEvent(new Event("input", { bubbles: true }));
  node.dispatchEvent(new Event("change", { bubbles: true }));
  node.blur?.();
  return true;
}

function ppNormalizeFacebookDescription(value) {
  return String(value ?? "").replace(/\r\n?/g, "\n").replace(/\s+/g, " ").trim();
}

function ppFacebookDescriptionHash(value) {
  // A compact diagnostic fingerprint only. Exact normalized text equality is
  // always the acceptance predicate; this non-cryptographic hash is never an
  // authorization or identity token.
  const text = ppNormalizeFacebookDescription(value);
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `fnv1a32:${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function ppFacebookPriceEquivalent(expectedValue, observedValue) {
  const expected = Number(String(expectedValue ?? "").replace(/[^\d.]/g, ""));
  const observed = Number(String(observedValue ?? "").replace(/[^\d.]/g, ""));
  if (!Number.isFinite(expected) || !Number.isFinite(observed)) return false;
  if (Math.round(expected * 100) === Math.round(observed * 100)) return true;
  // Facebook's live Marketplace Price input rounded this listing's 69.99 to
  // 70.00. Accept only the observed whole-dollar normalization when it is at
  // most one cent from the requested amount; do not permit broad repricing.
  return Number.isInteger(observed) && Math.abs(expected - observed) <= 0.0100001;
}

function ppFacebookDescriptionDomProbe() {
  const isVisible = (node) => Boolean(node && node.isConnected !== false && ppVisible(node));
  const text = (node, max = 240) => ppSafeEvidenceText(node?.innerText || node?.textContent || "", max);
  const attrs = (node) => {
    const out = {};
    for (const attr of Array.from(node?.attributes || [])) {
      if (/^data-[\w-]+$/i.test(attr.name) && !/(token|secret|auth|session|cookie)/i.test(attr.name)) out[attr.name] = String(attr.value || "").slice(0, 100);
      if (Object.keys(out).length >= 12) break;
    }
    return out;
  };
  const labelText = (node) => {
    const ids = String(node?.getAttribute?.("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    const labelledBy = ids.map((id) => document.getElementById?.(id)).filter(Boolean).map((item) => text(item, 100)).join(" ");
    const labels = node?.labels ? Array.from(node.labels).map((item) => text(item, 100)).join(" ") : "";
    const wrapped = text(node?.closest?.("label"), 100);
    return ppSafeEvidenceText([node?.getAttribute?.("aria-label"), labelledBy, labels, wrapped].filter(Boolean).join(" | "), 180);
  };
  const controls = Array.from(document.querySelectorAll('textarea,input,[role="textbox"],[contenteditable="true"],[contenteditable="plaintext-only"]'));
  const labels = Array.from(document.querySelectorAll("label,[role=heading],h1,h2,h3,[aria-label]"))
    .filter(isVisible).filter((node) => /^description\b/i.test(text(node, 100)) || /^description\b/i.test(String(node.getAttribute?.("aria-label") || "")))
    .slice(0, 8);
  const describe = (node) => {
    const rect = node?.getBoundingClientRect?.();
    const ancestors = [];
    let parent = node?.parentElement;
    for (let depth = 0; parent && depth < 4; depth += 1, parent = parent.parentElement) {
      ancestors.push({ tag: String(parent.tagName || "").toLowerCase(), role: parent.getAttribute?.("role") || null,
        label: ppSafeEvidenceText(parent.getAttribute?.("aria-label") || "", 100), text: text(parent, 180) });
    }
    const rawValue = node?.isContentEditable ? (node.innerText || node.textContent || "") : (node?.value ?? node?.textContent ?? "");
    const relationship = labels.map((label) => {
      const wrapper = label.contains?.(node) || node?.contains?.(label);
      const sameLabel = label.control === node;
      let sharedAncestor = false;
      let ancestor = node?.parentElement;
      for (let depth = 0; ancestor && depth < 5; depth += 1, ancestor = ancestor.parentElement) if (ancestor.contains?.(label)) { sharedAncestor = true; break; }
      return { label_text: text(label, 100), nested_or_wraps_control: Boolean(wrapper), label_control_match: Boolean(sameLabel), shared_bounded_ancestor: sharedAncestor };
    });
    return { tag_name: String(node?.tagName || "").toLowerCase(), role: node?.getAttribute?.("role") || null,
      aria_label: ppSafeEvidenceText(node?.getAttribute?.("aria-label") || "", 120) || null,
      aria_labelledby: ppSafeEvidenceText(node?.getAttribute?.("aria-labelledby") || "", 120) || null,
      placeholder: ppSafeEvidenceText(node?.getAttribute?.("placeholder") || "", 120) || null,
      name: ppSafeEvidenceText(node?.getAttribute?.("name") || "", 100) || null,
      contenteditable: node?.getAttribute?.("contenteditable") || (node?.isContentEditable ? "true" : null),
      type: node?.getAttribute?.("type") || node?.type || null,
      visible: isVisible(node), hidden: !isVisible(node), disabled: Boolean(node?.disabled || node?.getAttribute?.("aria-disabled") === "true"),
      read_only: Boolean(node?.readOnly || node?.getAttribute?.("aria-readonly") === "true"), connected: Boolean(node?.isConnected),
      value: ppSafeEvidenceText(rawValue, 240), value_length: String(rawValue || "").length,
      text_content_length: String(node?.textContent || "").length, inner_text_length: String(node?.innerText || "").length,
      nearest_label_text: labelText(node), ancestor_structure: ancestors, description_label_relationship: relationship,
      data_attributes: attrs(node), rect: rect ? { x: Number(rect.x), y: Number(rect.y), width: Number(rect.width), height: Number(rect.height) } : null,
      is_textarea: String(node?.tagName || "").toLowerCase() === "textarea", is_input: String(node?.tagName || "").toLowerCase() === "input",
      is_role_textbox: node?.getAttribute?.("role") === "textbox", is_contenteditable: Boolean(node?.isContentEditable || node?.getAttribute?.("contenteditable") === "true" || node?.getAttribute?.("contenteditable") === "plaintext-only") };
  };
  return { captured_at: new Date().toISOString(), page: { origin: location.origin, path: location.pathname },
    description_labels: labels.map((node) => ({ tag_name: String(node.tagName || "").toLowerCase(), role: node.getAttribute?.("role") || null,
      text: text(node, 100), aria_label: ppSafeEvidenceText(node.getAttribute?.("aria-label") || "", 100) || null,
      ancestors: describe(node).ancestor_structure })),
    candidates: controls.filter((node) => isVisible(node) || node.required || node.getAttribute?.("aria-required") === "true").slice(0, 40).map(describe) };
}

function ppFacebookDescriptionControl() {
  if (location.hostname !== "www.facebook.com" || !/^\/marketplace\/(create|item)\//i.test(location.pathname)) return null;
  const editableSelector = 'textarea,input,[role="textbox"],[contenteditable="true"],[contenteditable="plaintext-only"]';
  const controls = Array.from(document.querySelectorAll(editableSelector))
    .filter((node) => node?.isConnected !== false && !node.disabled && node.getAttribute?.("aria-disabled") !== "true" && !node.readOnly && ppVisible(node));
  const forbidden = /title|location|category|search|shipping|price|condition|availability|quantity|postal|zip/i;
  const labelsFor = (node) => {
    const ids = String(node.getAttribute?.("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    const labelledBy = ids.map((id) => document.getElementById?.(id)).filter(Boolean);
    const explicit = [node.getAttribute?.("aria-label"), node.getAttribute?.("placeholder"), node.getAttribute?.("name")];
    const native = [...Array.from(node.labels || []), node.closest?.("label"), ...labelledBy];
    return [...new Set([...explicit, ...native.map((label) => label?.innerText || label?.textContent || "")]
      .filter(Boolean).map((value) => String(value).replace(/\s+/g, " ").trim()))];
  };
  const exactDescription = (value) => /\bdescription\b/i.test(value) && !forbidden.test(value.replace(/\bdescription\b/ig, ""));
  const candidates = controls.filter((node) => labelsFor(node).some(exactDescription));
  // A label may wrap a non-editable shell around the actual nested editor.
  // Prefer the deepest editable descendant only when the wrapping label is
  // explicitly Description; never use the first arbitrary textarea.
  const nested = [];
  for (const label of Array.from(document.querySelectorAll("label")).filter(ppVisible)) {
    const labelText = String(label.innerText || label.textContent || "").replace(/\s+/g, " ").trim();
    if (!exactDescription(labelText)) continue;
    const descendants = Array.from(label.querySelectorAll?.(editableSelector) || [])
      .filter((node) => controls.includes(node));
    if (label.control && controls.includes(label.control)) descendants.push(label.control);
    nested.push(...descendants);
  }
  const resolved = [...new Set([...candidates, ...nested])];
  return resolved.length === 1 ? resolved[0] : null;
}

function ppFacebookLocationControlResolution() {
  const adapter = globalThis.POSTERPRO_MARKETPLACE_ADAPTERS?.facebook;
  if (!adapter || location.hostname !== "www.facebook.com") return { node: null, candidates: [], ambiguous: false };
  const selector = 'input,[role="combobox"],[role="textbox"],textarea,[contenteditable="true"]';
  const controls = Array.from(document.querySelectorAll(selector)).filter((node) => node?.isConnected !== false
    && !node.disabled && node.getAttribute?.("aria-disabled") !== "true" && !node.readOnly && ppVisible(node));
  const forbidden = /description|title|category|search|shipping|price|condition|availability|quantity/i;
  const signalsFor = (node) => {
    const ids = String(node.getAttribute?.("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    const labelledBy = ids.map((id) => document.getElementById?.(id)).filter(Boolean);
    const labels = [...Array.from(node.labels || []), node.closest?.("label"), ...labelledBy]
      .map((label) => String(label?.innerText || label?.textContent || "").replace(/\s+/g, " ").trim()).filter(Boolean);
    return [...new Set([node.getAttribute?.("aria-label"), node.getAttribute?.("placeholder"), node.getAttribute?.("name"), node.getAttribute?.("data-testid"), ...labels]
      .filter(Boolean).map((value) => String(value).replace(/\s+/g, " ").trim()))];
  };
  const exactLocation = (signal) => /\b(location|city|town|postal code|zip code|seller area|marketplace area)\b/i.test(signal)
    && !forbidden.test(signal);
  const candidates = controls.filter((node) => signalsFor(node).some(exactLocation));
  const unique = [...new Set(candidates)];
  const descriptionNode = ppFacebookDescriptionControl();
  const safe = unique.filter((node) => node !== descriptionNode);
  return { node: safe.length === 1 ? safe[0] : null, candidates: safe, ambiguous: safe.length > 1,
    evidence: safe.slice(0, 6).map((node) => ({ tag: String(node.tagName || "").toLowerCase(), role: node.getAttribute?.("role") || null,
      labels: signalsFor(node).slice(0, 6), connected: node.isConnected === true, visible: ppVisible(node) })) };
}

let ppFacebookDetailsDescriptionVerification = null;
let ppFacebookDetailsFieldVerification = { title: null, price: null };
let ppFacebookCurrentPayload = null;

function ppFacebookDetailsControl(field) {
  if (!['title', 'price'].includes(field) || location.hostname !== 'www.facebook.com'
    || !/^\/marketplace\/create\//i.test(location.pathname)) return null;
  const selector = 'input,textarea,[role="textbox"],[contenteditable="true"],[contenteditable="plaintext-only"]';
  const excluded = field === 'title'
    ? /price|minimum|offer|location|category|search|shipping|condition|availability|quantity|postal|zip|description/i
    : /minimum|offer|location|category|search|shipping|condition|availability|quantity|postal|zip|title|description/i;
  const exact = field === 'title' ? /\btitle\b/i : /\bprice\b/i;
  const controls = Array.from(document.querySelectorAll(selector)).filter((node) => node?.isConnected !== false
    && !node.disabled && node.getAttribute?.('aria-disabled') !== 'true' && !node.readOnly && ppVisible(node));
  const signals = (node) => {
    const ids = String(node.getAttribute?.('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const labelledBy = ids.map((id) => document.getElementById?.(id)).filter(Boolean);
    const labels = [...Array.from(node.labels || []), node.closest?.('label'), ...labelledBy];
    return [...new Set([node.getAttribute?.('aria-label'), node.getAttribute?.('placeholder'), node.getAttribute?.('name'),
      ...labels.map((label) => label?.innerText || label?.textContent || '')]
      .filter(Boolean).map((value) => String(value).replace(/\s+/g, ' ').trim()))];
  };
  const candidates = controls.filter((node) => {
    const evidence = signals(node);
    const positive = evidence.some((signal) => exact.test(signal) && !excluded.test(signal));
    const contradictory = evidence.some((signal) => excluded.test(signal));
    return positive && !contradictory;
  });
  return candidates.length === 1 ? candidates[0] : null;
}

async function ppFacebookSetAndVerifyDetailsField(field, value) {
  const expected = String(value ?? '').replace(/\s+/g, ' ').trim();
  const node = ppFacebookDetailsControl(field);
  if (!expected || !node) return { ok: false, error_code: node ? 'FACEBOOK_DETAILS_FIELD_VALUE_EMPTY' : `FACEBOOK_${field.toUpperCase()}_CONTROL_NOT_FOUND`, evidence: { field, found: Boolean(node), matches: false } };
  const assigned = ppSetValue(node, value);
  await ppFacebookWaitForRenderFrames();
  await ppWait(120);
  const current = ppFacebookDetailsControl(field);
  const observed = String(current?.value ?? current?.innerText ?? current?.textContent ?? '').replace(/\s+/g, ' ').trim();
  const matches = field === 'price'
    ? ppFacebookPriceEquivalent(expected, observed)
    : observed.toLocaleLowerCase() === expected.toLocaleLowerCase();
  const expectedComparable = field === 'price' ? Number(expected.replace(/[^\d.]/g, '')) : expected;
  const observedComparable = field === 'price' ? Number(observed.replace(/[^\d.]/g, '')) : observed;
  const evidence = { field, verified: Boolean(assigned && matches && current && current.isConnected !== false), matches,
    expected: field === 'title' ? ppSafeEvidenceText(expected, 200) : expectedComparable,
    observed: field === 'title' ? ppSafeEvidenceText(observed, 200) : (Number.isFinite(observedComparable) ? observedComparable : null),
    ...(field === 'price' && matches && Number(expectedComparable) !== Number(observedComparable) ? { normalization: 'FACEBOOK_WHOLE_DOLLAR_WITHIN_ONE_CENT' } : {}),
    control_tag: current ? String(current.tagName || '').toLowerCase() : null,
    control_connected: Boolean(current && current.isConnected !== false), verified_at: new Date().toISOString() };
  if (evidence.verified) ppFacebookDetailsFieldVerification[field] = evidence;
  return { ok: evidence.verified, error_code: evidence.verified ? null : `FACEBOOK_${field.toUpperCase()}_NOT_PERSISTED`, evidence };
}

function ppFacebookDescriptionCrossFieldGuard(expectedDescription, observedDescription, payload = ppFacebookCurrentPayload || {}) {
  const normalized = ppNormalizeFacebookDescription(observedDescription).toLocaleLowerCase();
  const normalize = (value) => ppNormalizeFacebookDescription(value).toLocaleLowerCase();
  const expected = normalize(expectedDescription);
  const location = normalize(payload.location);
  const title = normalize(payload.title);
  const price = normalize(payload.price);
  const equalsLocation = Boolean(location && normalized === location);
  const equalsTitle = Boolean(title && normalized === title);
  const equalsPrice = Boolean(price && normalized === price);
  if (!normalized) return { ok: false, error_code: "FACEBOOK_DESCRIPTION_NOT_PERSISTED", equals_location: false, equals_title: false, equals_price: false };
  if (equalsLocation) return { ok: false, error_code: "FACEBOOK_DESCRIPTION_LOCATION_CROSSFIELD_CORRUPTION", equals_location: true, equals_title: equalsTitle, equals_price: equalsPrice };
  if (equalsTitle || equalsPrice) return { ok: false, error_code: "FACEBOOK_DESCRIPTION_CROSSFIELD_CORRUPTION", equals_location: false, equals_title: equalsTitle, equals_price: equalsPrice };
  return { ok: true, error_code: null, equals_location: equalsLocation, equals_title: equalsTitle, equals_price: equalsPrice };
}

function ppFacebookDescriptionEvidence(expectedValue, stage, payload = ppFacebookCurrentPayload || {}) {
  const expected = ppNormalizeFacebookDescription(expectedValue);
  const control = ppFacebookDescriptionControl();
  let observed = "";
  let evidenceSource = "none";
  if (control) {
    observed = control.isContentEditable ? String(control.innerText || control.textContent || "") : String(control.value ?? control.textContent ?? "");
    evidenceSource = "live_description_control";
  } else if (expected) {
    // On later wizard/review stages Facebook may unmount the editor. Only
    // accept a bounded visible form/review container that contains the entire
    // normalized description; never use arbitrary page-wide text as proof.
    const scopes = Array.from(document.querySelectorAll("main,[role='main'],[role='dialog'],form,[data-pagelet*='Marketplace']"))
      .filter(ppVisible);
    const matching = scopes.find((scope) => ppNormalizeFacebookDescription(scope.innerText || scope.textContent || "").includes(expected));
    if (matching) {
      observed = expected;
      evidenceSource = "visible_marketplace_review_copy";
    } else if (ppFacebookDetailsDescriptionVerification?.matches === true && !/^DESCRIPTION_AFTER_FILL$|^DESCRIPTION_AFTER_DETAILS_FILL$|^DESCRIPTION_DETAILS_STAGE$/i.test(String(stage))) {
      observed = expected;
      evidenceSource = "durable_details_stage_verification";
    }
  }
  const normalizedObserved = ppNormalizeFacebookDescription(observed);
  const crossField = ppFacebookDescriptionCrossFieldGuard(expected, normalizedObserved, payload);
  const matches = Boolean(expected && normalizedObserved === expected && crossField.ok);
  return {
    stage,
    expected_length: expected.length,
    observed_length: normalizedObserved.length,
    expected_hash: ppFacebookDescriptionHash(expected),
    observed_hash: normalizedObserved ? ppFacebookDescriptionHash(normalizedObserved) : null,
    matches,
    error_code: crossField.error_code || (matches ? null : "FACEBOOK_DESCRIPTION_NOT_PERSISTED"),
    cross_field: { equals_location: crossField.equals_location, equals_title: crossField.equals_title, equals_price: crossField.equals_price, safe: crossField.ok },
    observed: Boolean(normalizedObserved),
    evidence_source: evidenceSource,
    description_verified_at_details_stage: Boolean(ppFacebookDetailsDescriptionVerification?.matches === true),
    details_stage_verification: ppFacebookDetailsDescriptionVerification,
    control: control ? {
      tag: String(control.tagName || "").toLowerCase(),
      role: control.getAttribute?.("role") || null,
      connected: control.isConnected !== false,
      visible: ppVisible(control),
      invalid: control.getAttribute?.("aria-invalid") === "true",
      fingerprint: ppFacebookDescriptionControlFingerprint(control),
    } : null,
  };
}

function ppFacebookDescriptionControlFingerprint(node = ppFacebookDescriptionControl()) {
  if (!node) return null;
  if (!ppFacebookDescriptionNodeIds.has(node)) ppFacebookDescriptionNodeIds.set(node, ++ppFacebookDescriptionNodeSequence);
  return `description-control-${ppFacebookDescriptionNodeIds.get(node)}`;
}

function ppFacebookDescriptionMutationCheckpoint(operation, expectedValue, payload = ppFacebookCurrentPayload || {}) {
  const evidence = ppFacebookDescriptionEvidence(expectedValue, operation, payload);
  return {
    operation,
    captured_at: new Date().toISOString(),
    workflow_stage: ppFacebookWorkflowStage(ppFacebookStageSnapshot(0)),
    normalized_length: evidence.observed_length,
    hash: evidence.observed_hash,
    expected_length: evidence.expected_length,
    expected_hash: evidence.expected_hash,
    matches: evidence.matches,
    control_fingerprint: evidence.control?.fingerprint || null,
    connected: evidence.control?.connected ?? false,
    visible: evidence.control?.visible ?? false,
    semantic_label: evidence.control ? "Description" : null,
    evidence_source: evidence.evidence_source,
    error_code: evidence.error_code,
  };
}

function ppFacebookValidateMutationOwnership(targetField, node) {
  if (!node || node.isConnected === false) return { ok: false, error_code: "FACEBOOK_FIELD_CONTROL_UNAVAILABLE" };
  const description = ppFacebookDescriptionControl();
  if (targetField === "description") return node === description
    ? { ok: true, semantic_identity: "Description" }
    : { ok: false, error_code: "FACEBOOK_FIELD_OWNERSHIP_MISMATCH", target_field: targetField, resolved_as: "NOT_DESCRIPTION" };
  if (node === description) {
    return { ok: false, error_code: "FACEBOOK_FIELD_OWNERSHIP_MISMATCH", target_field: targetField, resolved_as: "Description" };
  }
  if (String(node.tagName || "").toUpperCase() === "TEXTAREA") {
    const labels = ppFacebookControlSemanticLabels(node);
    if (!labels.some((label) => ppFacebookExactFieldLabel(label, targetField))) return {
      ok: false, error_code: "FACEBOOK_FIELD_OWNERSHIP_MISMATCH", target_field: targetField, resolved_as: "Unverified textarea",
    };
  }
  if (targetField === "location") {
    const location = ppFacebookLocationControlResolution();
    if (location.ambiguous || location.node !== node) return { ok: false, error_code: "FACEBOOK_LOCATION_CONTROL_NOT_SEMANTICALLY_RESOLVED" };
  }
  return { ok: true, semantic_identity: targetField };
}

function ppFacebookControlSemanticLabels(node) {
  const ids = String(node?.getAttribute?.("aria-labelledby") || "").split(/\s+/).filter(Boolean);
  const labelledBy = ids.map((id) => document.getElementById?.(id)).filter(Boolean);
  return [...new Set([
    node?.getAttribute?.("aria-label"), node?.getAttribute?.("placeholder"), node?.getAttribute?.("name"),
    ...Array.from(node?.labels || []).map((label) => label?.innerText || label?.textContent || ""),
    node?.closest?.("label")?.innerText || node?.closest?.("label")?.textContent || "",
    ...labelledBy.map((label) => label?.innerText || label?.textContent || ""),
  ].filter(Boolean).map((value) => String(value).replace(/\s+/g, " ").trim()))];
}

function ppFacebookExactFieldLabel(label, field) {
  const normalized = String(label || "").replace(/\s+/g, " ").trim().toLowerCase();
  const aliases = { brand: ["brand"], size: ["size"], color: ["color"], material: ["material"],
    location: ["location", "city", "town", "postal code", "zip code", "seller area", "marketplace area"],
    quantity: ["quantity"], weight: ["weight", "package weight"] }[field] || [field];
  const forbidden = /description|title|price|category|condition|shipping|availability|minimum|offer|search/i;
  return !forbidden.test(normalized) && aliases.some((alias) => new RegExp(`^${alias.replace(/[.*+?^${}()|[\\]\\]/g, "\\$&")}(?:\\s*[:*])?$`, "i").test(normalized));
}

function ppFacebookOptionalTextControl(field) {
  const controls = Array.from(document.querySelectorAll('input,textarea,[role="textbox"],[contenteditable="true"],[contenteditable="plaintext-only"]'))
    .filter((node) => node?.isConnected !== false && !node.disabled && node.getAttribute?.("aria-disabled") !== "true"
      && !node.readOnly && ppVisible(node));
  const candidates = controls.filter((node) => ppFacebookControlSemanticLabels(node).some((label) => ppFacebookExactFieldLabel(label, field)));
  return candidates.length === 1 ? candidates[0] : null;
}

function ppFacebookDescriptionSubmissionGuard(payload = {}, stage = "DESCRIPTION_FINAL_PRE_SUBMIT") {
  const evidence = ppFacebookDescriptionEvidence(payload.description, stage, payload);
  return {
    allowed: Boolean(evidence.matches),
    error_code: evidence.matches ? null : evidence.error_code || "FACEBOOK_DESCRIPTION_NOT_PERSISTED",
    evidence,
  };
}

async function ppFacebookSetAndVerifyDescription(adapter, value, stage = "AFTER_FILL") {
  const expected = ppNormalizeFacebookDescription(value);
  const sourceGuard = ppFacebookDescriptionCrossFieldGuard(expected, expected, ppFacebookCurrentPayload || {});
  if (!expected) return { ok: false, error_code: "FACEBOOK_DESCRIPTION_SOURCE_EMPTY", evidence: ppFacebookDescriptionEvidence(value, stage) };
  if (!sourceGuard.ok) return { ok: false, error_code: sourceGuard.error_code, evidence: ppFacebookDescriptionEvidence(value, stage, ppFacebookCurrentPayload || {}) };
  const node = ppFacebookDescriptionControl();
  if (!node || node.isConnected === false || node.disabled || !ppVisible(node)) {
    return { ok: false, error_code: "FACEBOOK_DESCRIPTION_CONTROL_NOT_FOUND", evidence: ppFacebookDescriptionEvidence(value, stage) };
  }
  const set = ppSetValue(node, value);
  // Let Facebook's controlled React input process the native input/change and
  // blur, then reacquire the live editor after potential rerender.
  await ppFacebookWaitForRenderFrames();
  await ppWait(120);
  const evidence = ppFacebookDescriptionEvidence(value, stage, ppFacebookCurrentPayload || {});
  if (evidence.matches && /DESCRIPTION_AFTER_FILL|DESCRIPTION_AFTER_DETAILS_FILL|DESCRIPTION_DETAILS_STAGE/i.test(stage)) {
    ppFacebookDetailsDescriptionVerification = {
      verified: true, matches: true, verified_at: new Date().toISOString(), expected_hash: evidence.expected_hash,
      observed_hash: evidence.observed_hash, expected_length: evidence.expected_length, observed_length: evidence.observed_length,
    };
    evidence.description_verified_at_details_stage = true;
    evidence.details_stage_verification = ppFacebookDetailsDescriptionVerification;
  }
  return {
    ok: Boolean(set && evidence.matches && evidence.evidence_source === "live_description_control" && evidence.control?.connected),
    error_code: set && evidence.matches && evidence.evidence_source === "live_description_control" && evidence.control?.connected ? null : evidence.error_code || "FACEBOOK_DESCRIPTION_NOT_PERSISTED",
    assignment_returned: Boolean(set),
    evidence,
  };
}

async function ppFacebookWaitForRenderFrames(timeoutMs = 1200) {
  const frameWait = new Promise((resolve) => {
    if (typeof requestAnimationFrame !== "function") return resolve();
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
  await ppWaitOrTimeout(frameWait, timeoutMs);
}

function ppFacebookMinimumPriceControls() {
  const selectors = 'input,textarea,select,[role="textbox"],[role="spinbutton"]';
  return Array.from(document.querySelectorAll(selectors)).filter((node) => {
    if (node.isConnected === false) return false;
    const labelledBy = String(node.getAttribute?.("aria-labelledby") || "").split(/\s+/).filter(Boolean)
      .map((id) => document.getElementById?.(id)?.innerText || document.getElementById?.(id)?.textContent || "");
    const label = [node.getAttribute?.("aria-label"), node.getAttribute?.("name"), node.getAttribute?.("placeholder"),
      ...(Array.from(node.labels || []).map((item) => item.innerText || item.textContent)),
      ...labelledBy, node.closest?.("label")?.innerText, node.parentElement?.innerText]
      .filter(Boolean).join(" ").replace(/\s+/g, " ").trim().toLowerCase();
    return /minimum price you(?:'|’)ll consider/.test(label);
  });
}

function ppFacebookMinimumPriceControlEvidence() {
  const controls = ppFacebookMinimumPriceControls();
  return controls.map((node) => ({
    visible: ppVisible(node), disabled: Boolean(node.disabled || node.getAttribute?.("aria-disabled") === "true"),
    required: Boolean(node.required || node.getAttribute?.("aria-required") === "true"),
    invalid: node.getAttribute?.("aria-invalid") === "true" || node.validity?.valid === false,
  }));
}

function ppFacebookOfferToggles() {
  const selector = 'input[type="checkbox"],[role="checkbox"],[role="switch"]';
  const labelPartsFor = (node) => {
    const labelledBy = String(node.getAttribute?.("aria-labelledby") || "").split(/\s+/).filter(Boolean)
      .map((id) => document.getElementById?.(id)?.innerText || document.getElementById?.(id)?.textContent || "");
    return [node.getAttribute?.("aria-label"), node.getAttribute?.("title"), node.innerText, node.textContent,
      ...Array.from(node.labels || []).map((item) => item.innerText || item.textContent),
      ...labelledBy, node.closest?.("label")?.innerText, node.parentElement?.innerText]
      .filter(Boolean).map((value) => String(value).replace(/\s+/g, " ").trim()).filter(Boolean);
  };
  const isOfferLabel = (part) => /^(?:accept\s+offers?|allow\s+offers?|enable\s+offers?|make\s+offers?|offers?|negotiable(?:\s+price)?|allow\s+buyers?\s+to\s+make\s+offers?)\s*[.!:]?$/i.test(part)
    || /\blet\s+buyers\s+negotiate\b/i.test(part)
    || /\bbuyers?\s+(?:can|may)\s+make\s+offers?\b/i.test(part);
  return Array.from(document.querySelectorAll(selector)).filter((node) => {
    const visibleLabel = Array.from(node.labels || []).some(ppVisible) || ppVisible(node.closest?.("label"));
    if (!ppVisible(node) && !visibleLabel) return false;
    return labelPartsFor(node).some(isOfferLabel);
  }).map((node) => ({
    node, label: ppSafeEvidenceText(labelPartsFor(node).find(isOfferLabel) || "Offers", 120),
    checked: node.checked === true || node.getAttribute?.("aria-checked") === "true"
      ? true : node.checked === false || node.getAttribute?.("aria-checked") === "false" ? false : null,
  }));
}

async function ppFacebookDisableOffers(payload = {}) {
  // Fixed-price listings default to no offers. Only an explicit destination
  // setting may opt in; never infer negotiation from a minimum-price field.
  if (payload.facebook_offers_enabled === true) return { resolved: true, offers_enabled: true, explicit_opt_in: true, control_present: false, action: "EXPLICIT_OFFERS_OPT_IN" };
  const matches = ppFacebookOfferToggles();
  if (matches.length > 1) return { resolved: false, offers_enabled: null, control_present: true, error_code: "FACEBOOK_OFFER_CONTROL_AMBIGUOUS", match_count: matches.length };
  if (!matches.length) return { resolved: true, offers_enabled: false, control_present: false, action: "OFFERS_NOT_EXPOSED_DEFAULT_OFF" };
  let { node, label, checked } = matches[0];
  if (checked === null) return { resolved: false, offers_enabled: null, control_present: true, label, error_code: "FACEBOOK_OFFER_STATE_UNOBSERVABLE" };
  if (!checked) return { resolved: true, offers_enabled: false, control_present: true, label, action: "ALREADY_OFF" };
  if (node.disabled || node.getAttribute?.("aria-disabled") === "true") return { resolved: false, offers_enabled: true, control_present: true, label, error_code: "FACEBOOK_OFFER_CONTROL_DISABLED_WHILE_ON" };
  const actionable = node.labels?.[0] || node.closest?.('label,[role="checkbox"],[role="switch"],[role="radio"]') || node;
  actionable.click?.();
  const deadline = Date.now() + 1400;
  let observed = checked;
  do {
    await ppWait(100);
    const live = ppFacebookOfferToggles();
    if (live.length !== 1) return { resolved: false, offers_enabled: null, control_present: live.length > 0, error_code: "FACEBOOK_OFFER_CONTROL_CHANGED_DURING_TOGGLE" };
    node = live[0].node; label = live[0].label; observed = live[0].checked;
    if (observed === false) break;
  } while (Date.now() < deadline);
  return observed === false
    ? { resolved: true, offers_enabled: false, control_present: true, label, action: "SWITCHED_OFF" }
    : { resolved: false, offers_enabled: observed === true ? true : null, control_present: true, label, error_code: "FACEBOOK_OFFERS_DID_NOT_TURN_OFF" };
}

function ppFacebookResolveMinimumPriceControl(payload = {}) {
  const matches = ppFacebookMinimumPriceControls();
  const evidence = ppFacebookMinimumPriceControlEvidence();
  if (payload.facebook_offers_enabled !== true) return {
    found: matches.length > 0, resolved: true, action: "SKIPPED_OFFERS_DISABLED",
    active: evidence.some((item) => item.visible && !item.disabled), controls: evidence,
  };
  const explicitFloor = Number(payload.minimum_offer_price);
  if (!Number.isFinite(explicitFloor) || explicitFloor <= 0) return {
    found: matches.length > 0, resolved: !evidence.some((item) => item.visible && !item.disabled && (item.required || item.invalid)),
    action: "NO_EXPLICIT_MINIMUM_OFFER", controls: evidence,
    error_code: evidence.some((item) => item.visible && !item.disabled && (item.required || item.invalid)) ? "FACEBOOK_MINIMUM_OFFER_VALUE_NOT_CONFIGURED" : null,
  };
  if (!matches.length) return { found: false, resolved: true, action: "NOT_PRESENT", controls: evidence };
  if (matches.length !== 1) return { found: true, resolved: false, error_code: "FACEBOOK_MINIMUM_PRICE_CONTROL_AMBIGUOUS", match_count: matches.length, controls: evidence };
  const node = matches[0];
  if (node.disabled || node.getAttribute?.("aria-disabled") === "true" || !ppVisible(node)) return { found: true, resolved: true, action: "INACTIVE_CONTROL", controls: evidence };
  const value = explicitFloor.toFixed(2);
  const filled = ppSetValue(node, value);
  const committed = String(node.value || "").replace(/[^\d.]/g, "") === value;
  const remainsInvalid = node.getAttribute?.("aria-invalid") === "true" || node.validity?.valid === false;
  return { found: true, resolved: Boolean(filled && committed && !remainsInvalid), action: "EXPLICIT_MINIMUM_OFFER_VALUE", value, controls: ppFacebookMinimumPriceControlEvidence(), error_code: filled && committed && !remainsInvalid ? null : "FACEBOOK_MINIMUM_PRICE_NOT_ACCEPTED" };
}

function ppFacebookIsInactiveMinimumPriceControl(node) {
  if (!ppFacebookMinimumPriceControls().includes(node)) return false;
  return !ppVisible(node) || Boolean(node.disabled || node.getAttribute?.("aria-disabled") === "true");
}

function ppFacebookIsInactiveMinimumPriceAlert(text, payload = {}) {
  if (payload.facebook_offers_enabled === true) return false;
  if (!/minimum price you(?:'|’)ll consider/i.test(String(text || ""))) return false;
  return !ppFacebookMinimumPriceControlEvidence().some((item) => item.visible && !item.disabled);
}

function ppSafeStructureText(value, limit = 80) {
  const text = String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  if (!text || /@|password|cookie|token|secret|bearer|\b\d{3}[- .]?\d{3}[- .]?\d{4}\b/i.test(text)) return null;
  return /^[\p{L}\p{N}_ .,:/()&+'’-]+$/u.test(text) ? text : null;
}

function ppControlStructure(node, field) {
  if (!node) return null;
  const type = String(node.getAttribute("type") || "").toLowerCase();
  if (["password", "email", "tel", "hidden"].includes(type)) return null;
  const tag = String(node.tagName || "").toLowerCase();
  const role = String(node.getAttribute("role") || (tag === "select" ? "combobox" : tag === "textarea" || tag === "input" ? "textbox" : tag === "button" ? "button" : "")).toLowerCase();
  const labelNode = node.labels?.[0] || node.closest?.("label") || (node.id ? document.querySelector(`label[for="${CSS.escape(node.id)}"]`) : null);
  const result = { tag, role };
  const fieldAliases = {
    category: ["category", "department", "item type"], condition: ["condition"], availability: ["availability", "quantity"],
    department: ["department"], subcategory: ["subcategory", "category"], shipping: ["shipping", "delivery", "parcel"],
    shipping_payer: ["shipping", "delivery", "payer"], shipping_method: ["shipping", "delivery", "method"], package: ["package", "parcel", "weight"],
  };
  const aliasHints = fieldAliases[field] || [field];
  const rawAttrs = [
    ["aria_label", node.getAttribute("aria-label")],
    ["name", node.getAttribute("name")],
    ["placeholder", node.getAttribute("placeholder")],
    ["nearby_label", labelNode?.innerText || labelNode?.textContent],
  ].map(([key, value]) => [key, ppSafeStructureText(value, key === "name" ? 64 : 80)]).filter(([, value]) => value);
  const labelHint = rawAttrs.map(([, value]) => value).join(" ").toLowerCase();
  const isMatchingControl = aliasHints.some((alias) => labelHint.includes(alias));
  if (isMatchingControl) for (const [key, value] of rawAttrs) result[key] = value;
  if (tag === "select" && isMatchingControl) {
    result.option_labels = Array.from(node.options || []).slice(0, 12).map((option) => ppSafeStructureText(option.label || option.textContent, 80)).filter(Boolean);
  }
  return result;
}

function ppSelectorDiagnostic(field, node, selectors, labels = []) {
  const root = node?.closest?.("form") || document.querySelector("form") || document;
  let controls = node ? [node] : Array.from(root.querySelectorAll("input:not([type=password]):not([type=email]):not([type=tel]):not([type=hidden]), textarea, select, [role=combobox], [role=button], button[aria-label]"))
    .filter((candidate) => candidate.offsetParent !== null && !candidate.disabled)
    .filter((candidate) => {
      const role = String(candidate.getAttribute("role") || "").toLowerCase();
      const tag = String(candidate.tagName || "").toLowerCase();
      const semantic = ["category", "condition", "availability", "department", "subcategory", "shipping", "shipping_payer", "shipping_method", "package"].includes(field);
      return semantic ? tag === "select" || ["combobox", "button", "option"].includes(role) || tag === "button" : ["input", "textarea"].includes(tag) || role === "textbox";
    }).slice(0, 6);
  if (!controls.length) controls = [null];
  const safeLabels = (labels || []).map((label) => ppSafeStructureText(label, 80)).filter(Boolean);
  const diagnostic = {
    field,
    page_path: location.pathname.split("/").map((part) => (/^\d+$/.test(part) || part.length > 48 ? ":id" : part)).join("/").slice(0, 300),
    selectors_tried: (selectors || []).slice(0, 8).map(String),
    controls: controls.map((control) => ppControlStructure(control, field)).filter(Boolean),
    expected_label_hints: safeLabels,
  };
  if (location.hostname.endsWith("mercari.com") && ["category", "condition", "shipping"].includes(field)) {
    const optionText = Array.from(document.querySelectorAll('[role="option"], [role="radio"], [role="menuitem"], [role="menuitemradio"], label, option, li, button'))
      .filter((candidate) => candidate.getAttribute("aria-hidden") !== "true" && candidate.offsetParent !== null && !candidate.disabled)
      .map((candidate) => ppSafeStructureText(candidate.innerText || candidate.textContent || candidate.value, 90))
      .filter(Boolean);
    const relevant = field === "condition"
      ? optionText.filter((value) => /new|used|good|fair|poor|like new|acceptable/i.test(value))
      : field === "shipping"
        ? optionText.filter((value) => /ship|delivery|pickup|prepaid|label|buyer|seller|package|parcel/i.test(value))
        : optionText;
    diagnostic.visible_option_labels = [...new Set(relevant)].slice(0, 16);
    if (field === "shipping") {
      diagnostic.delivery_options = Array.from(document.querySelectorAll('input[name="deliveryOptions"]')).slice(0, 12).map((input) => {
        const label = input.labels?.[0] || input.closest?.("label") || (input.id ? document.querySelector(`label[for="${CSS.escape(input.id)}"]`) : null);
        const wrapper = input.closest?.('[role="radio"], [role="button"], label, button') || input.parentElement;
        return {
          type: ppSafeStructureText(input.getAttribute("type"), 24),
          value: ppSafeStructureText(input.value, 80),
          checked: Boolean(input.checked),
          id: ppSafeStructureText(input.id, 64),
          aria_label: ppSafeStructureText(input.getAttribute("aria-label"), 64),
          label: ppSafeStructureText(label?.innerText || label?.textContent, 100),
          visible_card: ppSafeStructureText(wrapper?.innerText || wrapper?.textContent, 140),
        };
      });
    }
  }
  if (location.hostname.endsWith("facebook.com") && field === "location") {
    const candidates = Array.from(document.querySelectorAll('input, textarea, button, [role="button"], [role="combobox"], [role="textbox"], [role="dialog"]'))
      .filter(ppVisible)
      .filter((candidate) => {
        const evidence = `${candidate.getAttribute("aria-label") || ""} ${candidate.getAttribute("placeholder") || ""} ${candidate.getAttribute("name") || ""} ${candidate.getAttribute("data-testid") || ""} ${candidate.innerText || candidate.textContent || ""} ${candidate.parentElement?.innerText || ""}`.toLowerCase();
        return /location|zip|postal|city|town|address/.test(evidence);
      }).slice(0, 8);
    diagnostic.visible_location_controls = candidates.map((candidate) => ({
      tag: String(candidate.tagName || "").toLowerCase(),
      role: ppSafeStructureText(candidate.getAttribute("role"), 32),
      aria_label: ppSafeStructureText(candidate.getAttribute("aria-label"), 64),
      placeholder: ppSafeStructureText(candidate.getAttribute("placeholder"), 64),
      data_testid: ppSafeStructureText(candidate.getAttribute("data-testid"), 64),
      value_present: Boolean(String(candidate.value || "").trim()),
      visible: ppVisible(candidate),
      nearby_text_mentions_location: /location|zip|postal|city|town|address/i.test(String(candidate.innerText || candidate.textContent || candidate.parentElement?.innerText || "")),
    }));
  }
  return diagnostic;
}

const ppWait = (ms) => {
  const run = ppFacebookActiveFormRun;
  if (run?.cancelled) {
    const error = new Error("Facebook form fill was cancelled by its bounded pre-submit watchdog.");
    error.code = run.cancelCode || "FACEBOOK_FORM_FILL_CANCELLED";
    return Promise.reject(error);
  }
  if (!run) return new Promise((resolve) => setTimeout(resolve, ms));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    run.cancelSignal.then(() => {
      clearTimeout(timer);
      if (!run.cancelled) return resolve();
      const error = new Error("Facebook form fill was cancelled by its bounded pre-submit watchdog.");
      error.code = run.cancelCode || "FACEBOOK_FORM_FILL_CANCELLED";
      reject(error);
    });
  });
};

function ppWaitOrTimeout(promise, timeoutMs) {
  let timer;
  return Promise.race([promise, new Promise((resolve) => { timer = setTimeout(resolve, timeoutMs); })])
    .finally(() => clearTimeout(timer));
}

async function ppReportFacebookFormProgress(stage, phase = "checkpoint", detail = {}) {
  const run = ppFacebookActiveFormRun;
  if (!run) return { ok: false, skipped: true };
  if (run.cancelled) {
    const error = new Error("Facebook form fill was cancelled by its bounded pre-submit watchdog.");
    error.code = run.cancelCode || "FACEBOOK_FORM_FILL_CANCELLED";
    throw error;
  }
  const safeDetail = {};
  for (const key of ["field", "index", "count", "error_code", "duration_ms", "wait_ms", "required_fields_remaining"]) {
    const value = detail?.[key];
    if (["string", "number", "boolean"].includes(typeof value) || value == null) safeDetail[key] = typeof value === "string" ? value.slice(0, 120) : value;
  }
  try {
    const acknowledgment = await ppWaitOrTimeout(chrome.runtime.sendMessage({
        action: "posterpro_facebook_form_progress", internal_job_id: run.jobId,
        stage, phase, timestamp: new Date().toISOString(), page_url: ppSafePageUrl(), detail: safeDetail,
      }).then((response) => response, () => null).then((response) => {
        if (!response) throw Object.assign(new Error("Progress-message acknowledgement failed."), { code: "FACEBOOK_PROGRESS_MESSAGE_TIMEOUT" });
        return response;
      }),
      8000,
    );
    if (!acknowledgment || acknowledgment.ok !== true) throw Object.assign(new Error(acknowledgment?.error || "Progress checkpoint was not durably acknowledged."), { code: acknowledgment?.error_code || "FACEBOOK_PROGRESS_MESSAGE_TIMEOUT" });
  } catch (error) {
    run.lastProgressReportError = String(error?.message || error).slice(0, 120);
    throw error;
  }
  if (run.cancelled) {
    const error = new Error("Facebook form fill was cancelled by its bounded pre-submit watchdog.");
    error.code = run.cancelCode || "FACEBOOK_FORM_FILL_CANCELLED";
    throw error;
  }
  return { ok: true };
}

function ppVisible(node) {
  if (!node || node.disabled || node.getAttribute?.("aria-hidden") === "true") return false;
  const style = globalThis.getComputedStyle?.(node);
  return node.getClientRects?.().length > 0 && style?.visibility !== "hidden" && style?.display !== "none";
}

function ppMercariFieldControl(field, fallback) {
  if (field !== "category") return fallback;
  // Current Mercari renders the Category caption separately from an unlabeled
  // button. Resolve the nearest small field wrapper instead of guessing from
  // a page-wide button index or relying on an accessible name that is absent.
  const labels = Array.from(document.querySelectorAll("label, legend, span, p, div"))
    .filter((node) => ppVisible(node) && String(node.innerText || node.textContent || "").replace(/\s+/g, " ").trim().toLowerCase() === "category");
  for (const label of labels) {
    let current = label;
    for (let depth = 0; current && depth < 5; depth += 1, current = current.parentElement) {
      const controls = Array.from(current.querySelectorAll?.('button, [role="button"], [role="combobox"], input') || []).filter(ppVisible);
      const trigger = controls.find((node) => !/submit|publish|list item/i.test(`${node.innerText || ""} ${node.getAttribute("aria-label") || ""}`));
      if (trigger && current !== document.body) return trigger;
    }
  }
  return fallback;
}

function ppMercariOptionEvidence(option) {
  const input = option.matches?.('input[type="radio"], input[type="checkbox"]') ? option : option.querySelector?.('input[type="radio"], input[type="checkbox"]');
  const roleSelected = [option.getAttribute?.("aria-checked"), option.getAttribute?.("aria-selected"), option.getAttribute?.("data-state")]
    .some((value) => ["true", "checked", "selected"].includes(String(value || "").toLowerCase()));
  const classSelected = /selected|checked|active|chosen/i.test(String(option.className?.baseVal || option.className || ""));
  return { input, selected: Boolean(input?.checked || roleSelected || classSelected) };
}

function ppMercariElementStructure(node) {
  if (!node) return null;
  const text = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 160) || null;
  const describe = (element) => element ? {
    tag: String(element.tagName || "").toLowerCase() || null,
    role: element.getAttribute?.("role") || null,
    aria_selected: element.getAttribute?.("aria-selected") || null,
    aria_checked: element.getAttribute?.("aria-checked") || null,
    tabindex: element.getAttribute?.("tabindex") || null,
    data_testid: element.getAttribute?.("data-testid") || null,
    href: element.getAttribute?.("href") || null,
    text: text(element.innerText || element.textContent),
  } : null;
  let actionable = null;
  let current = node;
  for (let depth = 0; current && depth <= 5; depth += 1, current = current.parentElement) {
    if (depth > 0 && current.matches?.('[role="option"], [role="menuitem"], [role="menuitemradio"], [role="treeitem"], [role="button"], button, a, [tabindex="0"]')) {
      actionable = current;
      break;
    }
  }
  return { element: describe(node), actionable_ancestor: describe(actionable) };
}

function ppMercariLocalCardText(input) {
  const label = input?.labels?.[0] || input?.closest?.("label") || (input?.id ? document.querySelector(`label[for="${CSS.escape(input.id)}"]`) : null);
  if (label) return String(label.innerText || label.textContent || "").replace(/\s+/g, " ").trim().slice(0, 160);
  const hasShippingChoice = (text) => /prepaid label|mercari shipping|ship with mercari|shipping option/i.test(text);
  const siblings = (element) => [element?.previousElementSibling, element?.nextElementSibling].filter(Boolean);
  // Some Mercari builds keep deliveryOptions as a React state input adjacent
  // to, rather than nested inside, the visible card. Inspect only its local
  // sibling/card scope; never use whole-page text as selection evidence.
  let local = input;
  for (let depth = 0; local && depth <= 4; depth += 1, local = local.parentElement) {
    const localText = String(local.innerText || local.textContent || "").replace(/\s+/g, " ").trim();
    if (localText && localText.length <= 500 && hasShippingChoice(localText)) return localText.slice(0, 160);
    for (const sibling of siblings(local)) {
      const siblingText = String(sibling.innerText || sibling.textContent || "").replace(/\s+/g, " ").trim();
      if (siblingText && siblingText.length <= 240 && hasShippingChoice(siblingText)) return siblingText.slice(0, 160);
    }
    const parentSiblings = siblings(local?.parentElement);
    for (const sibling of parentSiblings) {
      const siblingText = String(sibling.innerText || sibling.textContent || "").replace(/\s+/g, " ").trim();
      if (siblingText && siblingText.length <= 240 && hasShippingChoice(siblingText)) return siblingText.slice(0, 160);
    }
  }
  const direct = input?.closest?.('[role="radio"], [role="button"], label, button') || input?.parentElement;
  const directText = String(direct?.innerText || direct?.textContent || "").replace(/\s+/g, " ").trim();
  if (directText) return directText.slice(0, 160);
  // Mercari sometimes places the radio in a visually labelled card without a
  // <label> or ARIA relationship. Walk a few local ancestors and use only
  // compact, text-bearing card content.
  let parent = input?.parentElement;
  for (let depth = 0; parent && depth < 6; depth += 1, parent = parent.parentElement) {
    const text = String(parent.innerText || parent.textContent || "").replace(/\s+/g, " ").trim();
    if (text && text.length <= 320) return text.slice(0, 160);
  }
  return null;
}

function ppMercariInputLabel(input) {
  return ppMercariLocalCardText(input) || String(input?.getAttribute?.("aria-label") || "").slice(0, 160);
}

function ppMercariBelongsToShippingField(input) {
  let current = input?.parentElement;
  for (let depth = 0; current && depth < 5; depth += 1, current = current.parentElement) {
    const tag = String(current.tagName || "").toLowerCase();
    const role = String(current.getAttribute?.("role") || "").toLowerCase();
    const marker = `${current.getAttribute?.("aria-label") || ""} ${current.getAttribute?.("data-testid") || ""} ${current.getAttribute?.("name") || ""} ${current.innerText || ""}`.toLowerCase();
    if ((tag === "fieldset" || role === "group" || /shipping|delivery/.test(marker)) && /shipping|delivery|mercari/i.test(marker)) return true;
  }
  return false;
}

function ppMercariShippingSelected(input) {
  const name = String(input?.name || input?.getAttribute?.("name") || "");
  const value = String(input?.value || input?.getAttribute?.("value") || "").trim().toUpperCase();
  const ariaLabel = String(input?.getAttribute?.("aria-label") || "").trim().toLowerCase();
  return Boolean(name === "deliveryOptions" && input?.checked === true && value === "MERCARI"
    && (ariaLabel === "mercarishipping" || ppMercariBelongsToShippingField(input)));
}

function ppMercariVisiblePickerCount() {
  return Array.from(document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"]')).filter(ppVisible).length;
}

function ppMercariCategoryControlText(fallback) {
  const freshFallback = ppFindField((globalThis.POSTERPRO_MARKETPLACE_ADAPTERS || {}).mercari?.category || [], ["category"]);
  const control = ppMercariFieldControl("category", freshFallback || fallback);
  return String(control?.innerText || control?.textContent || control?.value || "").replace(/\s+/g, " ").trim().slice(0, 180);
}

async function ppMercariWaitForCategorySelection({ option, beforeText, beforePickerCount, timeoutMs = 1400 }) {
  const started = Date.now();
  let summary = beforeText;
  let pickerCount = beforePickerCount;
  let evidence = ppMercariOptionEvidence(option);
  let categoryStateValue = "";
  do {
    summary = ppMercariCategoryControlText(summary);
    pickerCount = ppMercariVisiblePickerCount();
    evidence = ppMercariOptionEvidence(option);
    const stateInputs = Array.from(document.querySelectorAll('input[name*="category" i], input[name*="categoryId" i]'));
    categoryStateValue = String(stateInputs.find((input) => input.value || input.getAttribute("value"))?.value || "").trim();
    const summarySelected = Boolean(summary && !/^select category$/i.test(summary) && summary.toLowerCase() !== beforeText.toLowerCase());
    const pickerClosed = beforePickerCount > 0 && pickerCount === 0;
    if (summarySelected || pickerClosed || evidence.selected || categoryStateValue) {
      return { selected: true, summary, pickerCount, selectedState: evidence.selected, categoryStateValue, waitMs: Date.now() - started };
    }
    if (Date.now() - started >= timeoutMs) break;
    await ppWait(100);
  } while (true);
  return { selected: false, summary, pickerCount, selectedState: evidence.selected, categoryStateValue, waitMs: Date.now() - started };
}

async function ppMercariWaitForRender(beforeCount) {
  // React menus are often portaled outside the form. Wait for a real visible
  // DOM change, but retain a short bounded polling fallback for older builds.
  if (typeof MutationObserver === "function") {
    await new Promise((resolve) => {
      let done = false;
      const finish = () => { if (!done) { done = true; observer.disconnect(); resolve(); } };
      const observer = new MutationObserver(finish);
      observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-expanded", "aria-selected", "aria-checked", "data-state", "class"] });
      setTimeout(finish, 900);
    });
  }
  if (document.querySelectorAll(' [role="dialog"], [role="listbox"], [role="menu"], [role="option"], [role="radio"]').length === beforeCount) await ppWait(250);
}

function ppOptionAliases(marketplace, field, value) {
  const text = String(value || "").trim().toLowerCase().replace(/[_-]+/g, " ");
  if (field === "category") {
    if (/clothing|apparel|shirt|dress|jacket|fashion|shoe|footwear/.test(text)) return ["clothing", "clothing & accessories", "clothing and accessories", "apparel", "fashion"];
    if (/electronic|computer|phone|camera|audio/.test(text)) return ["electronics", "computers & tablets", "cell phones", "electronics & media"];
    if (/home|garden|furniture|kitchen/.test(text)) return ["home", "home & garden", "home and garden", "home goods", "furniture"];
    if (/toy|game|collectible|card/.test(text)) return ["toys & games", "toys and games", "toys & collectibles", "toys and collectibles", "collectibles", "trading cards"];
    if (/sport|outdoor/.test(text)) return ["sporting goods", "sports & outdoors", "sports and outdoors"];
  }
  if (field === "condition") {
    if (/new/.test(text)) return ["new", "brand new"];
    if (/like new|open box/.test(text)) return ["like new", "open box", "new other"];
    if (/good|used/.test(text)) return marketplace === "facebook" ? ["used - good", "good", "used, good", "used good"] : ["good", "used - good", "good condition"];
    return [text, "fair", "acceptable"];
  }
  if (field === "shipping" || field === "shipping_method") {
    if (text.includes("local")) return ["local pickup", "pick up", "meet in person", "local delivery"];
    if (text.includes("standard")) return ["standard", "ship", "shipping", "ship with mercari", "mercari prepaid label", "prepaid label", "i'll ship it myself", "i’ll ship it myself", "buyer pays"];
    if (marketplace === "mercari" && /ship|shipping|delivery/.test(text)) return ["shipping", "ship", "ship with mercari", "mercari prepaid label", "prepaid label", "i'll ship it myself", "i’ll ship it myself"];
  }
  if (field === "shipping_payer") {
    if (text.includes("buyer")) return ["buyer", "buyer pays", "customer pays"];
    if (text.includes("seller")) return ["seller", "seller pays", "i will pay"];
  }
  if (field === "package") {
    if (text.includes("small")) return ["small", "under 1 lb", "1 lb or less"];
    if (/\d/.test(text)) return [text, "1 lb or less", "small"];
  }
  return [text];
}

async function ppSelectOption(node, value, marketplace, field, context = {}) {
  if (!node || value == null || value === "") return { ok: false, error_code: "FIELD_OR_MAPPED_VALUE_MISSING" };
  if (node.tagName === "SELECT") {
    const aliases = ppOptionAliases(marketplace, field, value);
    const option = Array.from(node.options || []).find((item) => aliases.includes(String(item.textContent || item.label || "").trim().toLowerCase()));
    if (!option) return { ok: false, error_code: "DESTINATION_OPTION_NOT_FOUND" };
    node.value = option.value;
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, value: String(option.textContent || option.label || value).trim() };
  }
  if (marketplace === "facebook" && field === "category") {
    return ppSelectFacebookCategory(node, value, context.payload || {});
  }
  const mercari = marketplace === "mercari" && ["category", "condition", "shipping"].includes(field);
  if (mercari) node = ppMercariFieldControl(field, node);
  const beforeCount = document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"], [role="option"], [role="radio"]').length;
  const beforeText = String(node.innerText || node.textContent || node.value || "").replace(/\s+/g, " ").trim().slice(0, 180);
  const conditionInputs = mercari && field === "condition" ? Array.from(document.querySelectorAll('input[name="sellCondition"]')) : [];
  const aliases = ppOptionAliases(marketplace, field, value);
  const conditionInput = mercari && field === "condition"
    ? conditionInputs.find((input) => aliases.some((alias) => alias && ppMercariInputLabel(input).toLowerCase().includes(alias)))
      || (node.matches?.('input[name="sellCondition"]') && aliases.some((alias) => alias && ppMercariInputLabel(node).toLowerCase().includes(alias)) ? node : null)
    : null;
  const conditionBefore = conditionInput ? {
    checked: Boolean(conditionInput.checked), value: String(conditionInput.value || "").slice(0, 80),
    selected_state: conditionInput.getAttribute("aria-checked") || conditionInput.getAttribute("data-state"),
    choices: conditionInputs.slice(0, 12).map((input) => ({ value: String(input.value || "").slice(0, 80), checked: Boolean(input.checked), label: ppSafeStructureText(ppMercariInputLabel(input), 140) })),
  } : null;
  const deliveryInputs = mercari && field === "shipping" ? Array.from(document.querySelectorAll('input[name="deliveryOptions"]')) : [];
  const deliveryBefore = deliveryInputs.slice(0, 12).map((input) => ({ value: String(input.value || "").slice(0, 80), checked: Boolean(input.checked), label: ppSafeStructureText(ppMercariLocalCardText(input), 140) }));
  const selectedDelivery = deliveryInputs.find(ppMercariShippingSelected);
  if (mercari && field === "shipping" && selectedDelivery && /ship|shipping|delivery|standard/i.test(String(value))) {
    const cardText = ppMercariLocalCardText(selectedDelivery);
    return { ok: true, value: "MERCARI", diagnostic: {
      stage: "SELECTION_VERIFIED_ALREADY_SELECTED",
      trigger_clicked: "",
      selected_option: "MERCARI",
      post_selection_field_text: cardText,
      delivery_before: deliveryBefore,
      delivery_after: deliveryBefore,
      selected_input: {
        value: String(selectedDelivery.value || "").slice(0, 80),
        checked: Boolean(selectedDelivery.checked),
        aria_label: ppSafeStructureText(selectedDelivery.getAttribute?.("aria-label"), 80),
        card_text: cardText,
      },
    } };
  }
  // Inputs on Mercari can be hidden React state holders. Click their visible
  // associated label/card so React sees the same trusted interaction as a user.
  const semanticInput = mercari && ["condition", "shipping"].includes(field)
    ? (field === "condition" ? conditionInput : node.matches?.('input[name="deliveryOptions"]') ? node : deliveryInputs[0])
    : null;
  const semanticAssociatedLabel = semanticInput
    ? semanticInput.labels?.[0] || semanticInput.closest?.("label") || (semanticInput.id ? document.querySelector(`label[for="${CSS.escape(semanticInput.id)}"]`) : null)
    : null;
  const semanticClick = semanticAssociatedLabel
    || semanticInput?.closest?.('[role="radio"], [role="button"], label, button')
    || (semanticInput && ppVisible(semanticInput) ? semanticInput : null);
  // Mercari condition cards are already rendered as radio choices. Clicking
  // the first named radio as if it were a dropdown trigger selects New before
  // the requested option can be resolved; search the live cards first and
  // interact only with the exact matching card below.
  if (!(mercari && field === "condition")) (semanticClick || node).click?.();
  // Mercari's SPA often mounts the category/condition/shipping menu only
  // after the React state update has reached the next render. Poll for the
  // requested option instead of taking one early snapshot of the DOM.
  if (mercari) await ppMercariWaitForRender(beforeCount);
  else await ppWait(450);
  let optionSelectors = '[role="option"], [role="menuitem"], [role="menuitemradio"], [role="radio"], [role="treeitem"], [role="button"], [role="listbox"] > *, option, li, button';
  // Mercari exposes condition/delivery choices through form labels and radio
  // inputs rather than option/combobox roles. Include those controls only for
  // those fields; category matching remains restricted to explicit options so
  // an unrelated page label can never be mistaken for a category.
  if (marketplace === "mercari" && ["condition", "shipping"].includes(field)) optionSelectors += ', label, input[type="radio"], [data-testid], [tabindex="0"]';
  if (marketplace === "mercari" && field === "category") optionSelectors += ', [data-testid], [tabindex="0"], a';
  let options = [];
  const optionText = (option) => String(option.innerText || option.textContent || ((marketplace === "mercari" && (field === "condition" || field === "shipping")) ? ppMercariInputLabel(option) : "") || option.value || "").replace(/\s+/g, " ").trim().toLowerCase();
  const findOption = () => {
    const dialogs = mercari ? Array.from(document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"]')).filter((candidate) => ppVisible(candidate)) : [];
    const root = field === "category" && dialogs.length ? dialogs[dialogs.length - 1] : document;
    options = Array.from(root.querySelectorAll(optionSelectors))
      .filter((option) => ppVisible(option) || (option.offsetParent !== null && option.getAttribute("aria-hidden") !== "true" && !option.disabled));
    const exactMatches = options.filter((option) => aliases.includes(optionText(option)));
    const exact = exactMatches.length === 1 ? exactMatches[0] : null;
    const starts = options.filter((option) => aliases.some((alias) => alias && optionText(option).startsWith(alias)));
    const contains = exact || ((field === "category" || (marketplace === "mercari" && field === "shipping"))
      ? (starts.length === 1 ? starts[0] : null)
      : options.find((option) => aliases.some((alias) => alias && optionText(option).includes(alias))));
    return { contains, exactMatches, starts };
  };
  let { contains, exactMatches, starts } = findOption();
  let categoryPath = [];
  if (!contains && mercari && field === "category") {
    const visibleLabels = [...new Set(options.map(optionText).filter(Boolean))];
    const department = String(context.payload?.department || (context.diagnostic ? "Women" : "")).trim().toLowerCase();
    const departmentChoice = ["women", "men"].includes(department) && visibleLabels.some((label) => label === department)
      ? options.find((option) => optionText(option) === department)
      : null;
    // Mercari first asks for department, then mounts its leaf categories.
    // The synthetic garment diagnostic may choose the explicit department
    // and visible Tops leaf; real listings require their own exact category.
    if (departmentChoice) {
      const clickableDepartment = departmentChoice.closest?.('[role="option"], [role="menuitem"], [role="button"], button, a, li, [tabindex="0"]') || departmentChoice;
      clickableDepartment.click?.();
      categoryPath.push(String(context.payload?.department || "Women"));
      const countAfterDepartment = document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"], [role="option"], [role="radio"]').length;
      await ppMercariWaitForRender(countAfterDepartment);
      ({ contains, exactMatches, starts } = findOption());
      if (!contains && context.diagnostic && /clothing|apparel|garment/i.test(String(value))) {
        const desiredLeaf = String(context.payload?.subcategory || "Tops").trim().toLowerCase();
        const leaves = options.filter((option) => optionText(option) === desiredLeaf || optionText(option).startsWith(`${desiredLeaf} `));
        if (leaves.length === 1) { contains = leaves[0]; categoryPath.push(String(context.payload?.subcategory || "Tops")); }
      }
    }
  }
  if (!contains && marketplace === "mercari") {
    const deadline = Date.now() + 4500;
    while (!contains && Date.now() < deadline) {
      await ppWait(250);
      ({ contains, exactMatches, starts } = findOption());
    }
  }
  // For categories, never pick an arbitrary broader/adjacent leaf based on a
  // substring (e.g. "women's clothing" for an ambiguous "clothing" hint).
  // Accept an exact match or a unique option that starts with the requested
  // mapped label; otherwise leave the field unresolved for operator review.
  const menuCountAfterClick = document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"], [role="option"], [role="radio"]').length;
  const stage = !contains ? (field === "category" && menuCountAfterClick === beforeCount ? "TRIGGER_CLICK_NO_MUTATION" : "OPTIONS_NOT_FOUND") : null;
  if (!contains) return {
    ok: false,
    error_code: field === "category" && (starts.length > 1 || exactMatches.length > 1) ? "DESTINATION_OPTION_AMBIGUOUS" : "DESTINATION_OPTION_NOT_FOUND",
    diagnostic: {
      stage: stage || (beforeText === String(node.innerText || node.textContent || node.value || "").replace(/\s+/g, " ").trim().slice(0, 180) ? "TRIGGER_CLICK_NO_MUTATION_OR_OPTIONS_NOT_FOUND" : "MENU_NOT_FOUND"),
      label: field === "category" ? "Category" : field,
      field_container: ppSafeStructureText(node.parentElement?.innerText || node.parentElement?.textContent, 180),
      trigger_clicked: ppSafeStructureText(node.innerText || node.textContent || node.getAttribute("aria-label"), 120),
      dom_menu_count_before: beforeCount,
      dom_menu_count_after: menuCountAfterClick,
      dialog_or_menu_role: Array.from(document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"]')).filter((item) => ppVisible(item)).map((item) => item.getAttribute("role")).slice(0, 8),
      first_visible_options: [...new Set(options.map(optionText).filter(Boolean))].slice(0, 20),
      condition_before: conditionBefore,
      delivery_before: deliveryBefore,
    },
  };
  // Mercari may put option text in a span while the handler lives on a card
  // wrapper. Click the nearest semantic actionable ancestor.
  const associatedLabel = contains.tagName === "INPUT" && contains.id
    ? Array.from(document.querySelectorAll("label")).find((label) => label.htmlFor === contains.id)
    : null;
  const clickable = associatedLabel
    || contains.closest?.('[role="option"], [role="menuitem"], [role="menuitemradio"], [role="radio"], [role="treeitem"], [role="button"], button, a, li, label, [tabindex="0"]')
    || contains;
  let clickedElementStructure = ppMercariElementStructure(contains);
  const categoryPickerBeforeSelection = mercari && field === "category" ? ppMercariVisiblePickerCount() : 0;
  let categorySelection = null;
  let selectedOptionText = ppSafeStructureText(contains.innerText || contains.textContent || contains.value, 140);
  clickable.click?.();
  if (mercari && field === "category") {
    categorySelection = await ppMercariWaitForCategorySelection({ option: clickable, beforeText, beforePickerCount: categoryPickerBeforeSelection });
    const requestedCategoryPath = String(value || "").split(/\s*(?:>|\/|›)\s*/).map((part) => part.trim()).filter(Boolean);
    const requestedCategoryLeaf = requestedCategoryPath.at(-1)?.toLowerCase() || "";
    const selectedTextIsRequestedLeaf = selectedOptionText.toLowerCase() === requestedCategoryLeaf;
    if (categorySelection.selected && requestedCategoryLeaf && !selectedTextIsRequestedLeaf && requestedCategoryPath.length > 1) {
      // A parent row can close the first picker while storing only a broad
      // category. Re-open the field to inspect the next level before deciding
      // whether the canonical leaf is actually selectable.
      const currentControl = ppMercariFieldControl("category", node);
      currentControl?.click?.();
      await ppMercariWaitForRender(ppMercariVisiblePickerCount());
      categorySelection = { ...categorySelection, selected: false, pickerCount: ppMercariVisiblePickerCount() };
    }
    // The synthetic garment can reveal a third level after Women -> Tops.
    // Re-query the active picker and select only its unique aggregate tops
    // option; do not guess between specific blouse/shirt categories.
    if (!categorySelection.selected && context.diagnostic && /women/i.test(categoryPath[0] || "") && /tops/i.test(categoryPath[1] || "") && categorySelection.pickerCount > 0) {
      const liveDialogs = Array.from(document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"]')).filter(ppVisible);
      const liveRoot = liveDialogs[liveDialogs.length - 1] || document;
      const liveOptions = Array.from(liveRoot.querySelectorAll(optionSelectors)).filter((option) => ppVisible(option) || option.offsetParent !== null);
      const aggregateTops = liveOptions.filter((option) => /^(all )?tops\s*(?:&|and)\s*blouses\b/i.test(String(option.innerText || option.textContent || "").replace(/\s+/g, " ").trim()));
      if (aggregateTops.length === 1) {
        const nextOption = aggregateTops[0];
        const nextClickable = nextOption.closest?.('[role="option"], [role="menuitem"], [role="menuitemradio"], [role="treeitem"], [role="button"], button, a, li, label, [tabindex="0"]') || nextOption;
        clickedElementStructure = ppMercariElementStructure(nextOption);
        selectedOptionText = ppSafeStructureText(nextOption.innerText || nextOption.textContent || "All Tops & blouses", 140);
        categoryPath.push(selectedOptionText);
        const beforeNextPickerCount = ppMercariVisiblePickerCount();
        nextClickable.click?.();
        categorySelection = await ppMercariWaitForCategorySelection({ option: nextClickable, beforeText, beforePickerCount: beforeNextPickerCount });
      }
    }
    // CategoryRow values can be navigation nodes, not the chosen category.
    // Walk only exact labels from the listing's canonical path and re-query
    // the live portal after each React transition. A broad parent match alone
    // never satisfies a more-specific requested leaf.
    if (!categorySelection.selected && categorySelection.pickerCount > 0) {
      const requestedPath = requestedCategoryPath;
      const ignoredPathParts = new Set(["root", "toys & hobbies", "toys and hobbies", "toys & collectibles", "toys and collectibles"]);
      const pathToVisit = requestedPath.filter((part) => !ignoredPathParts.has(part.toLowerCase()));
      const desiredLeaf = pathToVisit[pathToVisit.length - 1]?.toLowerCase() || "";
      categoryPath = [...categoryPath, selectedOptionText];
      let fallbackLeafSelected = false;
      const readLiveOptions = () => {
        const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"]')).filter(ppVisible);
        const root = dialogs[dialogs.length - 1] || document;
        const currentOptions = Array.from(root.querySelectorAll(optionSelectors)).filter((item) => ppVisible(item) || (item.offsetParent !== null && item.getAttribute("aria-hidden") !== "true" && !item.disabled));
        return { currentOptions, labels: [...new Set(currentOptions.map(optionText).filter(Boolean))] };
      };
      let observedLabels = [];
      const segmentAliases = (segment) => {
        const normalized = segment.toLowerCase();
        if (normalized === "games") return ["games", "games & puzzles", "games and puzzles"];
        return [normalized];
      };
      for (const segment of pathToVisit) {
        if (segmentAliases(segment).some((alias) => categoryPath.some((part) => segmentAliases(part).includes(alias)))) continue;
        let { currentOptions, labels } = readLiveOptions();
        observedLabels = labels;
        let selectedSegment = segment;
        let matches = currentOptions.filter((item) => segmentAliases(segment).includes(optionText(item)));
        // On the live game tree, "Games & Puzzles" opens an intermediate
        // list whose only aggregate branch is "All games & puzzles". Traverse
        // that actually-rendered branch before considering any fallback leaf.
        if (matches.length !== 1 && /plastic action games/i.test(desiredLeaf) && segment.toLowerCase() === desiredLeaf) {
          const allGames = currentOptions.filter((item) => optionText(item) === "all games & puzzles" || optionText(item) === "all games and puzzles");
          if (allGames.length === 1) {
            const branch = allGames[0];
            const branchClick = branch.closest?.('[role="option"], [role="menuitem"], [role="menuitemradio"], [role="treeitem"], [role="button"], button, a, li, label, [tabindex="0"]') || branch;
            const priorLabels = labels.join("|");
            branchClick.click?.();
            await ppMercariWaitForRender(ppMercariVisiblePickerCount());
            const deadline = Date.now() + 3500;
            let deeper = readLiveOptions();
            while (deeper.labels.join("|") === priorLabels && Date.now() < deadline) {
              await ppWait(200);
              deeper = readLiveOptions();
            }
            selectedOptionText = ppSafeStructureText(branch.innerText || branch.textContent, 140);
            categoryPath.push(selectedOptionText);
            clickedElementStructure = ppMercariElementStructure(branch);
            observedLabels = deeper.labels;
            ({ currentOptions } = deeper);
            matches = currentOptions.filter((item) => segmentAliases(segment).includes(optionText(item)));
          }
        }
        // Mercari's current live taxonomy labels its game-family parent
        // "Games & Puzzles". Under that exact rendered parent, "Other" is a
        // deterministic leaf fallback for canonical "Plastic Action Games"
        // when no more specific rendered action/reflex-game leaf exists.
        if (matches.length !== 1 && /plastic action games/i.test(desiredLeaf) && segment.toLowerCase() === desiredLeaf) {
          const leafAliases = ["action games", "other games", "other"];
          const leafMatches = currentOptions.filter((item) => leafAliases.includes(optionText(item)));
          if (leafMatches.length === 1) {
            matches = leafMatches;
            selectedSegment = String(leafMatches[0].innerText || leafMatches[0].textContent || "Other").replace(/\s+/g, " ").trim();
            fallbackLeafSelected = true;
          }
        }
        if (matches.length !== 1) break;
        const pathOption = matches[0];
        const pathClickable = pathOption.closest?.('[role="option"], [role="menuitem"], [role="menuitemradio"], [role="treeitem"], [role="button"], button, a, li, label, [tabindex="0"]') || pathOption;
        clickedElementStructure = ppMercariElementStructure(pathOption);
        selectedOptionText = ppSafeStructureText(pathOption.innerText || pathOption.textContent || selectedSegment, 140);
        categoryPath.push(selectedOptionText);
        const pickerBeforePathClick = ppMercariVisiblePickerCount();
        pathClickable.click?.();
        const pathSelection = await ppMercariWaitForCategorySelection({ option: pathClickable, beforeText, beforePickerCount: pickerBeforePathClick });
        const after = readLiveOptions();
        observedLabels = after.labels;
        categorySelection = { ...pathSelection, live_hierarchy_options: observedLabels.slice(0, 30) };
        if (segment.toLowerCase() === desiredLeaf || selectedSegment.toLowerCase() === desiredLeaf || fallbackLeafSelected) break;
        if (ppMercariVisiblePickerCount() === 0) {
          const currentControl = ppMercariFieldControl("category", node);
          currentControl?.click?.();
          await ppMercariWaitForRender(ppMercariVisiblePickerCount());
        }
      }
      categorySelection.requested_category_path = requestedPath;
      categorySelection.live_hierarchy_options = observedLabels.slice(0, 30);
      if ((categoryPath.some((part) => part.toLowerCase() === desiredLeaf) || fallbackLeafSelected) && desiredLeaf) {
        const liveSummary = ppMercariCategoryControlText("");
        const stateInput = Array.from(document.querySelectorAll('input[name*="category" i], input[name*="categoryId" i]')).find((item) => String(item.value || item.getAttribute("value") || "").trim());
        const pickerCount = ppMercariVisiblePickerCount();
        if ((liveSummary && !/^select category$/i.test(liveSummary)) || pickerCount === 0 || stateInput) {
          categorySelection = { ...categorySelection, selected: true, summary: liveSummary, pickerCount, selectedState: Boolean(stateInput), stage: fallbackLeafSelected ? "SELECTION_VERIFIED_MAPPED_FALLBACK_LEAF" : "SELECTION_VERIFIED_PATH_LEAF", mapped_leaf_fallback: fallbackLeafSelected ? "Other" : null };
        }
      }
    }
  } else {
    await ppWait(100);
  }
  // Mercari's controlled condition radios can be visually wrapped by a label
  // that does not own the React change handler. Try the matching radio itself
  // only if the first semantic card/label click left it unchecked. This is a
  // real DOM click (never a raw checked/value assignment), and the state is
  // re-read immediately afterward.
  let conditionInputClickFallback = false;
  if (mercari && field === "condition" && conditionInput && !conditionInput.checked) {
    conditionInput.click?.();
    conditionInputClickFallback = true;
    await ppWait(100);
  }
  const selectedEvidence = ppMercariOptionEvidence(contains);
  const selectedControl = contains.matches?.('input[type="radio"], input[type="checkbox"]')
    ? contains
    : contains.querySelector?.('input[type="radio"], input[type="checkbox"]') || associatedLabel?.querySelector?.('input[type="radio"], input[type="checkbox"]');
  const conditionMatch = conditionInputs.find((input) => input.checked && aliases.some((alias) => alias && ppMercariInputLabel(input).toLowerCase().includes(alias)));
  const shippingMatch = deliveryInputs.find((input) => input.checked && aliases.some((alias) => alias && ppMercariInputLabel(input).toLowerCase().includes(alias)));
  const explicitlySelected = Boolean(categorySelection?.selected) || selectedEvidence.selected || selectedControl?.checked === true || conditionInput?.checked === true || Boolean(conditionMatch || shippingMatch)
    || [contains.getAttribute?.("aria-checked"), contains.getAttribute?.("aria-selected"), contains.getAttribute?.("data-state")].some((value) => ["true", "checked", "selected"].includes(String(value || "").toLowerCase()));
  const triggerText = String(node.innerText || node.textContent || node.value || "").replace(/\s+/g, " ").trim().toLowerCase();
  const verifyAliases = field === "category" && categoryPath.length ? [...aliases, ...categoryPath.slice(-1).map((part) => part.toLowerCase())] : aliases;
  const triggerReflectsChoice = verifyAliases.some((alias) => alias && triggerText.includes(alias));
  const inputReflectsChoice = semanticInput && [semanticInput.value, semanticInput.getAttribute?.("value")].some((candidate) => aliases.some((alias) => alias && String(candidate || "").toLowerCase().includes(alias)));
  const afterText = String(node.innerText || node.textContent || node.value || "").replace(/\s+/g, " ").trim().slice(0, 180);
  const summaryReflectsChoice = afterText !== beforeText && verifyAliases.some((alias) => alias && afterText.toLowerCase().includes(alias));
  if (!explicitlySelected && !triggerReflectsChoice && !inputReflectsChoice && !summaryReflectsChoice) return {
    ok: false,
    error_code: "DESTINATION_SELECTION_NOT_VERIFIED",
    diagnostic: {
      stage: "SELECTION_NOT_PERSISTED",
      trigger_clicked: ppSafeStructureText((semanticClick || node).innerText || (semanticClick || node).textContent || (semanticClick || node).getAttribute("aria-label"), 120),
      condition_before: conditionBefore,
      condition_after: conditionInput ? { checked: Boolean(conditionInput.checked), value: String(conditionInput.value || "").slice(0, 80), selected_state: conditionInput.getAttribute("aria-checked") || conditionInput.getAttribute("data-state") } : null,
      condition_input_click_fallback: conditionInputClickFallback,
      condition_clicked_element: mercari && field === "condition" ? ppMercariElementStructure(contains) : null,
      delivery_before: deliveryBefore,
      delivery_after: deliveryInputs.map((input) => ({ value: String(input.value || "").slice(0, 80), checked: Boolean(input.checked) })),
      selected_option: selectedOptionText,
      post_selection_field_text: ppSafeStructureText(categorySelection?.summary || afterText, 180),
      ...(field === "category" ? {
        category_path: categoryPath,
        requested_category_path: categorySelection?.requested_category_path || [],
        live_hierarchy_options: categorySelection?.live_hierarchy_options || [],
        clicked_element_structure: clickedElementStructure,
        dialog_visible_before: categoryPickerBeforeSelection > 0,
        dialog_visible_after: (categorySelection?.pickerCount ?? ppMercariVisiblePickerCount()) > 0,
        visible_selected_breadcrumb: categorySelection?.summary || null,
        selected_state: categorySelection?.selectedState || false,
      } : {}),
    },
  };
  const verifiedValue = categorySelection?.summary && !/^select category$/i.test(categorySelection.summary)
    ? categorySelection.summary
    : ppSafeStructureText(contains.innerText || contains.textContent || value, 160);
  return { ok: true, value: verifiedValue, diagnostic: {
    stage: field === "category" && categorySelection?.selectedState ? "SELECTION_VERIFIED_STATE" : field === "category" && categorySelection && categorySelection.pickerCount === 0 ? "SELECTION_VERIFIED_PICKER_CLOSED" : field === "category" ? "SELECTION_VERIFIED_SUMMARY" : "SELECTION_VERIFIED",
    label: field === "category" ? "Category" : field,
    category_path: categoryPath,
    field_container: ppSafeStructureText(node.parentElement?.innerText || node.parentElement?.textContent, 180),
    trigger_clicked: ppSafeStructureText((semanticClick || node).innerText || (semanticClick || node).textContent || (semanticClick || node).getAttribute?.("aria-label"), 120),
    dom_menu_count_before: beforeCount,
    dom_menu_count_after: menuCountAfterClick,
    dialog_or_menu_role: Array.from(document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"]')).filter((item) => ppVisible(item)).map((item) => item.getAttribute("role")).slice(0, 8),
    first_visible_options: [...new Set(options.map(optionText).filter(Boolean))].slice(0, 20),
    selected_option: selectedOptionText || ppSafeStructureText(contains.innerText || contains.textContent || value, 140),
    post_selection_field_text: ppSafeStructureText(categorySelection?.summary || afterText, 180),
    ...(field === "category" ? {
      clicked_element_structure: clickedElementStructure,
      requested_category_path: categorySelection?.requested_category_path || [],
      live_hierarchy_options: categorySelection?.live_hierarchy_options || [],
      category_resolution_stage: categorySelection?.stage || null,
      mapped_leaf_fallback: categorySelection?.mapped_leaf_fallback || null,
      dialog_visible_before: categoryPickerBeforeSelection > 0,
      dialog_visible_after: (categorySelection?.pickerCount ?? ppMercariVisiblePickerCount()) > 0,
      visible_selected_breadcrumb: categorySelection?.summary || null,
      selected_state: categorySelection?.selectedState || false,
    } : {}),
    condition_before: conditionBefore,
    condition_after: conditionInput ? { checked: Boolean(conditionInput.checked), value: String(conditionInput.value || "").slice(0, 80), selected_state: conditionInput.getAttribute("aria-checked") || conditionInput.getAttribute("data-state"), choices: conditionInputs.slice(0, 12).map((input) => ({ value: String(input.value || "").slice(0, 80), checked: Boolean(input.checked), label: ppSafeStructureText(ppMercariInputLabel(input), 140) })) } : null,
    condition_input_click_fallback: conditionInputClickFallback,
    condition_clicked_element: mercari && field === "condition" ? ppMercariElementStructure(contains) : null,
    delivery_before: deliveryBefore,
    delivery_after: deliveryInputs.slice(0, 12).map((input) => ({ value: String(input.value || "").slice(0, 80), checked: Boolean(input.checked), label: ppSafeStructureText(ppMercariInputLabel(input), 140) })),
  } };
}

function ppFacebookElementStructure(node) {
  if (!node) return null;
  const describe = (element) => element ? {
    tag: String(element.tagName || "").toLowerCase() || null,
    role: ppSafeStructureText(element.getAttribute?.("role"), 32),
    data_testid: ppSafeStructureText(element.getAttribute?.("data-testid"), 64),
    aria_selected: ppSafeStructureText(element.getAttribute?.("aria-selected"), 16),
    aria_checked: ppSafeStructureText(element.getAttribute?.("aria-checked"), 16),
    tabindex: ppSafeStructureText(element.getAttribute?.("tabindex"), 8),
    text: ppSafeStructureText(element.innerText || element.textContent, 120),
  } : null;
  let actionable = null;
  let current = node;
  for (let depth = 0; current && depth <= 5; depth += 1, current = current.parentElement) {
    if (current.matches?.('button, [role="option"], [role="menuitem"], [role="treeitem"], [role="button"], a, li, [tabindex="0"]')) {
      actionable = current;
      break;
    }
  }
  return { element: describe(node), actionable_ancestor: describe(actionable) };
}

function ppFacebookVisiblePickerCount() {
  return Array.from(document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"]')).filter(ppVisible).length;
}

function ppFacebookCategoryCandidates(canonicalCategory, payload = {}) {
  const canonical = String(canonicalCategory || "").replace(/\s+/g, " ").trim();
  const leaf = canonical.split(">").pop().trim();
  const semantic = `${canonical} ${payload.title || ""} ${payload.product_type || ""}`.toLowerCase();
  // Facebook has its own vocabulary. These narrow family mappings are only
  // candidates: a candidate must still be present in the live Marketplace UI.
  if (/video game|gaming console|game console/.test(semantic)) return { family: "video_games", candidates: ["Video Games"] };
  if (/\bgame(?:s)?\b|\btoy(?:s)?\b|collectible|trading card/.test(semantic)) return { family: "toys_games", candidates: ["Toys & Games", "Toys and Games", "Collectibles"] };
  if (/furniture|sofa|couch|table|chair|dresser|bed frame/.test(semantic)) return { family: "furniture", candidates: ["Furniture"] };
  if (/appliance|air fryer|coffee maker|microwave|space heater|toaster|blender/.test(semantic)) return { family: "appliances", candidates: ["Appliances"] };
  if (/garden|lawn|patio|outdoor plant|landscap/.test(semantic)) return { family: "garden", candidates: ["Garden"] };
  if (/household|home decor|kitchen|bedding|bathroom|housewares/.test(semantic)) return { family: "household", candidates: ["Household"] };
  if (/clothing|apparel|garment|shirt|dress|jacket|fashion|shoe|footwear/.test(semantic)) return { family: "apparel", candidates: ["Clothing", "Clothing & Accessories", "Apparel"] };
  if (/electronic|computer|phone|camera|audio/.test(semantic)) return { family: "electronics", candidates: ["Electronics", "Computers & Tablets", "Electronics & Media"] };
  if (/sport|outdoor/.test(semantic)) return { family: "sporting_goods", candidates: ["Sporting Goods", "Sports & Outdoors"] };
  return { family: "canonical_exact", candidates: ppOptionAliases("facebook", "category", leaf) || [leaf] };
}

async function ppSelectFacebookCategory(trigger, value, payload = {}) {
  const mapping = ppFacebookCategoryCandidates(value, payload);
  const candidates = mapping.candidates;
  const aliases = [...new Set(candidates.flatMap((candidate) => ppOptionAliases("facebook", "category", candidate)))];
  const beforeText = String(trigger.innerText || trigger.textContent || trigger.value || "").replace(/\s+/g, " ").trim().slice(0, 180);
  const triggerStructure = ppFacebookElementStructure(trigger);
  const beforePickerCount = ppFacebookVisiblePickerCount();
  const beforeOptionCount = document.querySelectorAll('[role="option"], [role="menuitem"], [role="treeitem"]').length;
  trigger.click?.();
  const selectors = '[role="option"], [role="menuitem"], [role="menuitemradio"], [role="treeitem"], [role="button"], button, [role="listbox"] > *, [data-testid], [tabindex="0"], li';
  const optionText = (option) => String(option.innerText || option.textContent || "").replace(/\s+/g, " ").trim();
  const findTarget = () => {
    // Facebook commonly portals menu rows outside the form. Query the live
    // document on every pass; never retain a stale React option collection.
    const options = Array.from(document.querySelectorAll(selectors))
      .filter((option) => ppVisible(option) && !option.disabled)
      .map((option) => ({ option, text: optionText(option), normalized: optionText(option).toLowerCase() }))
      .filter(({ text }) => text && text.length <= 180);
    const exact = options.filter(({ normalized }) => aliases.includes(normalized));
    const starts = options.filter(({ normalized }) => aliases.some((alias) => alias && normalized.startsWith(alias)));
    const target = exact.length === 1 ? exact[0] : exact.length === 0 && starts.length === 1 ? starts[0] : null;
    return { target, options, exactCount: exact.length, startsCount: starts.length };
  };
  let found = findTarget();
  const started = Date.now();
  while (!found.target && Date.now() - started < 3500) {
    await ppWait(150);
    found = findTarget();
  }
  const menuCountAfterOpen = ppFacebookVisiblePickerCount();
  const firstVisibleOptions = [...new Set(found.options.map(({ text }) => ppSafeStructureText(text, 100)).filter(Boolean))].slice(0, 20);
  if (!found.target) return {
    ok: false,
    error_code: found.exactCount > 1 || found.startsCount > 1 ? "DESTINATION_OPTION_AMBIGUOUS" : "FACEBOOK_CATEGORY_MAPPING_UNRESOLVED",
    diagnostic: {
      stage: menuCountAfterOpen > beforePickerCount || found.options.length ? "MAPPING_UNRESOLVED" : beforeOptionCount === 0 ? "TRIGGER_CLICK_NO_MUTATION" : "OPTIONS_NOT_FOUND",
      source_canonical_category: ppSafeStructureText(value, 160),
      product_semantics: ppSafeStructureText(payload.title || payload.product_type, 160),
      facebook_category_family: mapping.family,
      facebook_candidate_labels: candidates.map((candidate) => ppSafeStructureText(candidate, 100)).filter(Boolean),
      field_container: ppSafeStructureText(trigger.parentElement?.innerText || trigger.parentElement?.textContent, 180),
      trigger_structure: triggerStructure,
      trigger_clicked: ppSafeStructureText(trigger.innerText || trigger.textContent || trigger.getAttribute?.("aria-label"), 120),
      picker_count_before: beforePickerCount,
      picker_count_after: menuCountAfterOpen,
      option_count_before: beforeOptionCount,
      option_count_after: found.options.length,
      dialog_menu_roles: Array.from(document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"]')).filter(ppVisible).map((item) => item.getAttribute("role")).slice(0, 8),
      first_visible_options: firstVisibleOptions,
    },
  };
  const chosen = found.target.option;
  const clickable = chosen.closest?.('[role="option"], [role="menuitem"], [role="menuitemradio"], [role="treeitem"], [role="button"], button, a, li, [tabindex="0"]') || chosen;
  const clickedStructure = ppFacebookElementStructure(chosen);
  clickable.click?.();
  const verifyStarted = Date.now();
  let afterText = beforeText;
  let pickerCount = menuCountAfterOpen;
  let selected = false;
  do {
    await ppWait(100);
    afterText = String(trigger.innerText || trigger.textContent || trigger.value || "").replace(/\s+/g, " ").trim().slice(0, 180);
    pickerCount = ppFacebookVisiblePickerCount();
    const selectedState = [chosen.getAttribute?.("aria-selected"), chosen.getAttribute?.("aria-checked"), chosen.getAttribute?.("data-state"), clickable.getAttribute?.("aria-selected"), clickable.getAttribute?.("aria-checked"), clickable.getAttribute?.("data-state")]
      .some((state) => ["true", "selected", "checked"].includes(String(state || "").toLowerCase()));
    const summaryChanged = afterText && afterText.toLowerCase() !== beforeText.toLowerCase() && aliases.some((alias) => afterText.toLowerCase().includes(alias));
    selected = Boolean(summaryChanged || selectedState || (menuCountAfterOpen > beforePickerCount && pickerCount === beforePickerCount));
    if (selected || Date.now() - verifyStarted >= 1800) break;
  } while (true);
  const diagnostic = {
    stage: selected ? "SELECTION_VERIFIED" : "SELECTION_NOT_PERSISTED",
    source_canonical_category: ppSafeStructureText(value, 160),
    product_semantics: ppSafeStructureText(payload.title || payload.product_type, 160),
    facebook_category_family: mapping.family,
    facebook_candidate_labels: candidates.map((candidate) => ppSafeStructureText(candidate, 100)).filter(Boolean),
    selected_hierarchy_path: selected ? [ppSafeStructureText(optionText(chosen), 120)].filter(Boolean) : [],
    field_container: ppSafeStructureText(trigger.parentElement?.innerText || trigger.parentElement?.textContent, 180),
    trigger_structure: triggerStructure,
    trigger_clicked: ppSafeStructureText(trigger.innerText || trigger.textContent || trigger.getAttribute?.("aria-label"), 120),
    picker_count_before: beforePickerCount,
    picker_count_after_open: menuCountAfterOpen,
    picker_count_after_selection: pickerCount,
    first_visible_options: firstVisibleOptions,
    selected_option: ppSafeStructureText(optionText(chosen), 120),
    clicked_element_structure: clickedStructure,
    post_selection_field_text: ppSafeStructureText(afterText, 180),
    selection_wait_ms: Date.now() - verifyStarted,
  };
  return selected
    ? { ok: true, value: ppSafeStructureText(afterText, 160) || ppSafeStructureText(optionText(chosen), 160), diagnostic }
    : { ok: false, error_code: "DESTINATION_SELECTION_NOT_VERIFIED", diagnostic };
}

function ppDetectLoginState(marketplace) {
  const url = `${location.hostname}${location.pathname}`.toLowerCase();
  const passwordVisible = Array.from(document.querySelectorAll('input[type="password"]')).some((node) => node.offsetParent !== null);
  const loginUrl = /login|signin|sign-in|checkpoint|challenge|captcha/.test(url);
  const bodyHint = String(document.body?.innerText || "").slice(0, 1800).toLowerCase();
  if (/captcha|security check|confirm you are human/.test(bodyHint) || /captcha/.test(url)) return "CAPTCHA_REQUIRED";
  if (/checkpoint/.test(url)) return "CHECKPOINT_REQUIRED";
  if (/challenge/.test(url) || /account restricted|temporarily blocked/.test(bodyHint)) return "ACCOUNT_RESTRICTION";
  if (passwordVisible || loginUrl || /log in to continue|sign in to continue|create an account to continue/.test(bodyHint)) return "LOGIN_REQUIRED";
  // A marketplace create form is positive, safe evidence of an authenticated
  // session. Do not report LOGGED_IN merely because a page loaded.
  // Mercari's authenticated create page is commonly served at /sell/ (and
  // then enhanced in-place by its SPA), not /sell/new. Treat that route as
  // positive form context so a valid logged-in Mercari session is not reduced
  // to UNKNOWN before field detection runs.
  if (/marketplace\/create|\/sell(?:\/|$)|sell\/new|listing\/create|items\/new/.test(url)) return "LOGGED_IN";
  if (Array.from(document.querySelectorAll('button,[role="button"],a')).some((node) => /log out|sign out|account menu/.test(String(node.innerText || node.getAttribute("aria-label") || "").toLowerCase()))) return "LOGGED_IN";
  return "UNKNOWN";
}

function ppSyntheticImage() {
  const canvas = document.createElement("canvas");
  canvas.width = 640; canvas.height = 420;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = "#eef2ff"; ctx.fillRect(0, 0, 640, 420);
  ctx.fillStyle = "#172554"; ctx.font = "bold 36px sans-serif"; ctx.textAlign = "center";
  ctx.fillText("POSTERPRO SAFE TEST", 320, 190);
  ctx.font = "24px sans-serif"; ctx.fillText("DO NOT SUBMIT", 320, 240);
  return canvas.toDataURL("image/jpeg", 0.82);
}

function ppDataUrlToFile(image, index) {
  const parts = String(image.data_url || "").match(/^data:([^;,]+);base64,(.+)$/s);
  if (!parts) return null;
  const binary = atob(parts[2]); const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new File([bytes], image.file_name || `posterpro-photo-${index + 1}.jpg`, { type: parts[1] });
}

function ppCanonicalCondition(marketplace, condition) {
  const value = String(condition || "").toLowerCase();
  if (marketplace === "facebook") {
    if (/new/.test(value)) return "New";
    if (/like new|open box/.test(value)) return "Used - like new";
    if (/good|used/.test(value)) return "Used - good";
    return "Used - fair";
  }
  if (/new/.test(value)) return "New";
  if (/like new/.test(value)) return "Like new";
  if (/good|used/.test(value)) return "Good";
  return condition || "Good";
}

function ppSafeEvidenceText(value, limit = 280) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[redacted email]")
    .replace(/\b\d{3}[- .]?\d{3}[- .]?\d{4}\b/g, "[redacted phone]")
    .replace(/\b[A-F0-9]{24,}\b/gi, "[redacted id]")
    .trim()
    .slice(0, limit);
}

function ppMercariFormAnchors(adapter) {
  const visibleField = (selectors, labels) => {
    const node = ppFindField(selectors, labels);
    return Boolean(node && ppVisible(node));
  };
  const photoInput = document.querySelector(adapter.photos);
  const forms = Array.from(document.querySelectorAll("form")).filter((form) => ppVisible(form));
  return {
    title: visibleField(adapter.title, ["title"]),
    price: visibleField(adapter.price, ["price"]),
    description: visibleField(adapter.description, ["description", "describe"]),
    photos: Boolean(photoInput && !photoInput.disabled && photoInput.getAttribute("aria-hidden") !== "true"),
    visible_form: forms.length > 0,
    core_anchor_count: [
      visibleField(adapter.title, ["title"]),
      visibleField(adapter.price, ["price"]),
      visibleField(adapter.description, ["description", "describe"]),
    ].filter(Boolean).length,
  };
}

async function ppWaitForMercariSellFormReady(adapter, { timeoutMs = 12000, pollMs = 200 } = {}) {
  const startedAt = Date.now();
  let wakeForMutation = null;
  let observer = null;
  try {
    if (typeof MutationObserver === "function" && document.documentElement) {
      observer = new MutationObserver(() => wakeForMutation?.());
      observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
    }
    let anchors;
    do {
      anchors = ppMercariFormAnchors(adapter);
      if (anchors.title && anchors.price && anchors.description && anchors.photos) {
        return { ready: true, wait_started: true, wait_ms: Date.now() - startedAt, anchors };
      }
      const remaining = timeoutMs - (Date.now() - startedAt);
      if (remaining <= 0) break;
      const mutation = new Promise((resolve) => { wakeForMutation = resolve; });
      await Promise.race([ppWait(Math.min(pollMs, remaining)), mutation]);
      wakeForMutation = null;
      // Bound rapid mutation bursts while still polling even if React does not
      // emit a useful child-list event for a value/visibility transition.
      if (observer) await ppWait(Math.min(50, Math.max(0, timeoutMs - (Date.now() - startedAt))));
    } while (Date.now() - startedAt <= timeoutMs);
    anchors = ppMercariFormAnchors(adapter);
    return { ready: false, wait_started: true, wait_ms: Date.now() - startedAt, anchors };
  } finally {
    wakeForMutation = null;
    observer?.disconnect();
  }
}

function ppMercariPageEvidence(adapter, anchors) {
  const bodyText = String(document.body?.innerText || "").replace(/\s+/g, " ").trim();
  const normalized = bodyText.toLowerCase();
  const headings = Array.from(document.querySelectorAll("h1,h2,h3,[role='heading']"))
    .filter((node) => ppVisible(node))
    .map((node) => ppSafeEvidenceText(node.innerText || node.textContent, 100))
    .filter(Boolean)
    .slice(0, 8);
  const inputs = Array.from(document.querySelectorAll("input"));
  const textareas = Array.from(document.querySelectorAll("textarea"));
  const buttons = Array.from(document.querySelectorAll("button,[role='button']"));
  const fileInputs = inputs.filter((node) => String(node.type || node.getAttribute("type") || "").toLowerCase() === "file");
  const loginState = ppDetectLoginState("mercari");
  const loadingIndicators = Boolean(
    document.querySelector('[aria-busy="true"],[role="progressbar"],[data-testid*="loading" i]')
    || /\b(loading|please wait|one moment)\b/i.test(bodyText),
  );
  const sellShellVisible = /list an item|sell an item|create a listing|item details|add photos/i.test(bodyText);
  const classification = loginState === "CAPTCHA_REQUIRED" ? "CAPTCHA_REQUIRED"
    : loginState === "CHECKPOINT_REQUIRED" ? "CHECKPOINT_REQUIRED"
      : loginState === "LOGIN_REQUIRED" ? "LOGIN_REQUIRED"
        : loginState === "ACCOUNT_RESTRICTION" ? "ACCOUNT_RESTRICTION"
          : loadingIndicators || sellShellVisible ? "FORM_HYDRATION_TIMEOUT" : "MARKETPLACE_FORM_CHANGED";
  return {
    classification,
    evidence: {
      current_url: ppSafePageUrl(),
      page_title: ppSafeEvidenceText(document.title || "", 160),
      document_ready_state: ["loading", "interactive", "complete"].includes(document.readyState) ? document.readyState : "unknown",
      body_text_summary: ppSafeEvidenceText(bodyText, 360),
      visible_headings: headings,
      loading_indicators: loadingIndicators,
      login_state: loginState,
      captcha_or_checkpoint_text: /captcha|security check|checkpoint|confirm you are human/i.test(bodyText),
      sell_shell_visible: sellShellVisible,
      counts: { inputs: inputs.length, textareas: textareas.length, buttons: buttons.length, file_inputs: fileInputs.length, visible_forms: anchors.visible_form ? 1 : 0 },
      form_anchors: anchors,
    },
  };
}

async function ppRunDiagnostic(message) {
  const marketplace = String(message.marketplace || "").toLowerCase();
  if (marketplace === "facebook") {
    const adapter = (globalThis.POSTERPRO_MARKETPLACE_ADAPTERS || {}).facebook;
    const readiness = await ppWaitForFacebookCreateFormReady(adapter, { timeoutMs: 15000, pollMs: 200 });
    if (!readiness.ready) {
      const page = ppMarketplacePageEvidence("facebook", readiness.anchors);
      return {
        ...ppContentScriptIdentity(), ok: false, capability_ready: false,
        stage: page.classification, marketplace, action: "DIAGNOSTIC",
        page_state: page.classification, login_state: page.evidence.login_state,
        page_url: page.evidence.current_url, form_detected: false,
        form_readiness: { form_ready_wait_started: true, form_ready_wait_ms: readiness.wait_ms, form_ready_anchors: readiness.anchors },
        page_evidence: page.evidence, field_results: [], missing_required_fields: [],
        submission_performed: false, error_code: page.classification,
        error_detail: `Facebook Marketplace form did not reach its required anchors within ${readiness.wait_ms} ms (${page.classification}).`,
      };
    }
    const result = await ppFillListing({ ...message, diagnostic: true });
    const locationResult = await ppVerifyFacebookLocationAtReview(message.payload?.location);
    result.location_requirement_satisfied = locationResult.diagnostic?.location_requirement_satisfied === true;
    result.location_value_verified = locationResult.diagnostic?.location_value_verified === true;
    result.location_verified_mode = locationResult.diagnostic?.verified_mode || null;
    const locationField = result.field_results?.find((field) => field.field === "location");
    if (locationField && locationResult.filled) {
      locationField.filled = true;
      locationField.detected = locationField.detected || locationResult.detected;
      locationField.verified_value = locationResult.verified_value;
      locationField.error_code = null;
      locationField.interaction_diagnostic = locationResult.diagnostic;
      result.missing_required_fields = (result.missing_required_fields || []).filter((field) => field !== "location");
      result.capability_ready = result.missing_required_fields.length === 0;
      result.error_code = result.missing_required_fields.length ? "REQUIRED_FIELDS_UNRESOLVED" : null;
      result.page_url = location.origin + location.pathname;
    } else if (locationField) {
      locationField.interaction_diagnostic = locationResult.diagnostic;
    }
    return { ...result, form_detected: true, form_readiness: { form_ready_wait_started: true, form_ready_wait_ms: readiness.wait_ms, form_ready_anchors: readiness.anchors } };
  }
  if (marketplace !== "mercari") return ppFillListing({ ...message, diagnostic: true });
  const adapter = (globalThis.POSTERPRO_MARKETPLACE_ADAPTERS || {})[marketplace];
  if (!adapter) return ppFillListing({ ...message, diagnostic: true });
  const readiness = await ppWaitForMercariSellFormReady(adapter, { timeoutMs: 12000, pollMs: 200 });
  if (!readiness.ready) {
    const page = ppMercariPageEvidence(adapter, readiness.anchors);
    return {
      ...ppContentScriptIdentity(),
      ok: false,
      capability_ready: false,
      stage: page.classification,
      marketplace,
      action: "DIAGNOSTIC",
      page_state: page.classification,
      login_state: page.evidence.login_state,
      page_url: page.evidence.current_url,
      form_detected: false,
      form_readiness: { form_ready_wait_started: readiness.wait_started, form_ready_wait_ms: readiness.wait_ms, form_ready_anchors: readiness.anchors },
      page_evidence: page.evidence,
      field_results: [],
      missing_required_fields: [],
      submission_performed: false,
      error_code: page.classification,
      error_detail: `Mercari sell form did not reach its required anchors within ${readiness.wait_ms} ms (${page.classification}).`,
    };
  }
  return {
    ...(await ppFillListing({ ...message, diagnostic: true })),
    form_detected: true,
    form_readiness: { form_ready_wait_started: readiness.wait_started, form_ready_wait_ms: readiness.wait_ms, form_ready_anchors: readiness.anchors },
  };
}

function ppFacebookVisibleLocationMatch(configuredLocation, visibleText) {
  const configured = String(configuredLocation || "").trim();
  const visible = String(visibleText || "").replace(/\s+/g, " ").trim();
  if (!configured || !visible) return false;
  const pieces = configured.match(/^(.+?),\s*([A-Za-z]{2}|[A-Za-z ]+?)\s+(\d{5})(?:-\d{4})?$/);
  if (!pieces) return false;
  const city = pieces[1].toLowerCase().replace(/[.,]/g, " ").replace(/\s+/g, " ").trim();
  const state = pieces[2].toLowerCase().trim();
  const zip = pieces[3];
  const folded = visible.toLowerCase().replace(/[.,]/g, " ").replace(/\s+/g, " ").trim();
  const stateAliases = state.length === 2 ? [state] : [state];
  if (state === "ny") stateAliases.push("new york");
  if (state === "ca") stateAliases.push("california");
  return folded.includes(city) && stateAliases.some((alias) => folded.includes(alias)) && folded.includes(zip);
}

function ppFacebookLocationSelectionMatches(configuredLocation) {
  const candidates = Array.from(document.querySelectorAll('[role="option"],[role="radio"],[role="combobox"],button,[role="button"],input[type="radio"],label'));
  return candidates.filter(ppVisible).some((node) => {
    const text = String(node.innerText || node.getAttribute?.("aria-label") || node.labels?.[0]?.innerText || node.parentElement?.innerText || "");
    const selected = node.checked === true
      || node.getAttribute?.("aria-selected") === "true"
      || node.getAttribute?.("aria-checked") === "true"
      || ["selected", "checked"].includes(String(node.getAttribute?.("data-state") || "").toLowerCase());
    return selected && ppFacebookVisibleLocationMatch(configuredLocation, text);
  });
}

function ppFacebookLocationOptionCandidates(configuredLocation) {
  const selectors = '[role="option"],[role="radio"],[role="menuitem"],input[type="radio"],label,button,[role="button"],li,[tabindex="0"]';
  return Array.from(document.querySelectorAll(selectors)).filter(ppVisible).filter((node) => {
    const text = String(node.innerText || node.textContent || node.getAttribute?.("aria-label") || node.labels?.[0]?.innerText || "");
    return ppFacebookLocationSuggestionMatches(configuredLocation, text);
  });
}

function ppFacebookLocationSuggestionMatches(configuredLocation, visibleText) {
  const configured = String(configuredLocation || "").trim();
  const visible = String(visibleText || "").replace(/\s+/g, " ").trim();
  const pieces = configured.match(/^(.+?),\s*([A-Za-z]{2}|[A-Za-z ]+?)\s+(\d{5})(?:-\d{4})?$/);
  if (!pieces || !visible) return false;
  const city = pieces[1].toLowerCase().replace(/[.,]/g, " ").replace(/\s+/g, " ").trim();
  const state = pieces[2].toLowerCase().trim();
  const folded = visible.toLowerCase().replace(/[.,]/g, " ").replace(/\s+/g, " ").trim();
  const stateAliases = state === "ny" ? ["ny", "new york"] : state === "ca" ? ["ca", "california"] : [state];
  const visibleZip = visible.match(/\b\d{5}(?:-\d{4})?\b/)?.[0]?.slice(0, 5) || null;
  return folded.includes(city) && stateAliases.some((alias) => folded.includes(alias))
    && (!visibleZip || visibleZip === pieces[3]);
}

function ppFacebookLocationControlMatches(configuredLocation, input) {
  if (!input) return false;
  const value = String(input.value || input.innerText || "").trim();
  const expanded = input.getAttribute?.("aria-expanded");
  return ppFacebookLocationSuggestionMatches(configuredLocation, value) && expanded !== "true";
}

function ppFacebookLocationCityMatches(configuredLocation, value) {
  const city = String(configuredLocation || "").split(",")[0].trim().toLowerCase();
  const observed = String(value || "").replace(/[.,]/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
  return Boolean(city && observed.includes(city));
}

function ppFacebookLocationReviewEvidence(configuredLocation, snapshot, visibleText) {
  const reviewContext = /review your listing|ready to publish|listing review/i.test(
    `${(snapshot?.headings || []).join(" ")} ${snapshot?.page_path || ""} ${visibleText || ""}`,
  );
  return reviewContext && ppFacebookVisibleLocationMatch(configuredLocation, visibleText);
}

function ppFacebookStageSnapshot(step) {
  const visible = (selector) => Array.from(document.querySelectorAll(selector)).filter(ppVisible);
  const labelOf = (node) => String(node?.innerText || node?.value || node?.getAttribute?.("aria-label") || node?.getAttribute?.("placeholder") || node?.getAttribute?.("title") || "").replace(/\s+/g, " ").trim();
  const headings = visible('h1,h2,h3,[role="heading"]').map(labelOf).filter(Boolean).slice(0, 8);
  // Visibility and enabled state are separate facts. Facebook's final Publish
  // button is commonly rendered visibly but disabled while React settles; the
  // generic ppVisible helper intentionally filters disabled controls, which
  // would hide this exact state from the transition machine.
  const rendered = (node) => {
    if (!node || node.getAttribute?.("aria-hidden") === "true" || !node.getClientRects?.().length) return false;
    const style = globalThis.getComputedStyle?.(node);
    return style?.visibility !== "hidden" && style?.display !== "none";
  };
  const buttonNodes = Array.from(document.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]')).filter(rendered);
  const safeButtonNodes = buttonNodes.filter((node) => {
    const text = labelOf(node);
    return text && !/\b[\p{Lu}][\p{L}'’-]+\s+[\p{Lu}][\p{L}'’-]+\b/u.test(text);
  });
  const buttonLabels = safeButtonNodes.map(labelOf).slice(0, 24);
  const semanticControls = visible('[role="dialog"],[role="listbox"],[role="combobox"],[role="option"],[role="radio"],[role="checkbox"],[role="switch"]')
    .slice(0, 24).map((node) => {
      const role = node.getAttribute?.("role") || null;
      const label = ["dialog", "listbox"].includes(role) ? String(node.getAttribute?.("aria-label") || node.getAttribute?.("title") || "") : labelOf(node);
      return { role, label: ppSafeEvidenceText(label, 100), expanded: node.getAttribute?.("aria-expanded") || null, selected: node.getAttribute?.("aria-selected") || null, checked: node.getAttribute?.("aria-checked") || null };
    });
  const dialogs = visible('[role="dialog"],[role="listbox"],[role="menu"]')
    .slice(0, 8).map((node) => ({
      role: node.getAttribute?.("role") || null,
      label: ppSafeEvidenceText(node.getAttribute?.("aria-label") || node.getAttribute?.("title") || "", 80),
      heading: ppSafeEvidenceText(Array.from(node.querySelectorAll?.('h1,h2,h3,[role="heading"]') || []).map(labelOf).join(" ") || "", 120),
      expanded: node.getAttribute?.("aria-expanded") || null,
      option_labels: Array.from(node.querySelectorAll?.('[role="option"],[role="menuitem"],[role="radio"],input[type="radio"],label') || [])
        .filter(ppVisible).map((option) => ppSafeEvidenceText(option.labels?.[0]?.innerText || labelOf(option), 100))
        .filter((text) => text && /\busps\b|\bups\b|fed\s*ex|\b\d+(?:\.\d+)?\s*(?:lb|lbs|pound|pounds)\b|weight|shipping/i.test(text)).slice(0, 16),
    }));
  const fieldLabels = visible('label,input,textarea,select,[role="textbox"]').map((node) => ({
    type: String(node.getAttribute?.("type") || node.tagName?.toLowerCase() || "control").slice(0, 24),
    label: ppSafeEvidenceText(labelOf(node) || node.getAttribute?.("name") || "", 80),
    placeholder: ppSafeEvidenceText(node.getAttribute?.("placeholder") || "", 80),
    aria_label: ppSafeEvidenceText(node.getAttribute?.("aria-label") || "", 80),
    role: ppSafeEvidenceText(node.getAttribute?.("role") || "", 32),
    disabled: Boolean(node.disabled || node.getAttribute?.("aria-disabled") === "true"),
    required: Boolean(node.required || node.getAttribute?.("aria-required") === "true"),
  })).filter((row) => row.label || row.placeholder).slice(0, 32);
  const selectedControls = visible('input[type="radio"],input[type="checkbox"],[role="radio"],[role="checkbox"],[role="switch"]')
    .filter((node) => node.checked === true || node.getAttribute?.("aria-checked") === "true").slice(0, 16)
    .map((node) => ({ role: node.getAttribute?.("role") || node.getAttribute?.("type") || "control", label: ppSafeEvidenceText(labelOf(node.labels?.[0] || node.parentElement) || node.getAttribute?.("name") || "", 80), checked: true }));
  const validationEvidence = {
    invalid_controls: visible('input[aria-invalid="true"],textarea[aria-invalid="true"],select[aria-invalid="true"],[role="combobox"][aria-invalid="true"],[role="textbox"][aria-invalid="true"]')
      .slice(0, 8).map((node) => ({
        label: ppSafeEvidenceText(labelOf(node.labels?.[0] || node.parentElement) || node.getAttribute?.("aria-label") || node.getAttribute?.("name") || node.getAttribute?.("placeholder") || "", 100),
        role: ppSafeEvidenceText(node.getAttribute?.("role") || node.tagName?.toLowerCase() || "control", 24),
        required: Boolean(node.required || node.getAttribute?.("aria-required") === "true"),
        disabled: Boolean(node.disabled || node.getAttribute?.("aria-disabled") === "true"),
      })),
    messages: visible('[role="alert"],[aria-live="assertive"]')
      .map(labelOf).filter(Boolean).map((text) => ppSafeEvidenceText(text, 140)).filter(Boolean).slice(0, 8),
  };
  const locationRelatedLabels = visible('button,[role="button"],[role="combobox"],[role="dialog"],[role="option"],label,input,textarea,[role="textbox"],[role="alert"]')
    .map(labelOf).filter((text) => text && /location|city|town|postal|zip|seller area|marketplace area|change|edit/i.test(text))
    .map((text) => ppSafeEvidenceText(text, 140)).filter(Boolean).slice(0, 20);
  const publishControls = buttonNodes.filter((node) => /^(publish|publish listing|list|list item|post|post listing|submit listing)$/i.test(labelOf(node)));
  const publishLabels = publishControls.map(labelOf);
  const allText = String(document.body?.innerText || "");
  return {
    step, page_path: location.pathname, headings, button_labels: buttonLabels, dialogs,
    buttons: safeButtonNodes.slice(0, 24).map((node) => ({ label: ppSafeEvidenceText(labelOf(node), 100), enabled: !node.disabled && node.getAttribute?.("aria-disabled") !== "true" })).filter((row) => row.label),
    semantic_controls: semanticControls, field_labels: fieldLabels, selected_controls: selectedControls,
    details_controls: { title: { mounted: Boolean(ppFacebookDetailsControl("title")) },
      price: { mounted: Boolean(ppFacebookDetailsControl("price")) },
      description: { mounted: Boolean(ppFacebookDescriptionControl()) } },
    validation_evidence: validationEvidence,
    location_related_labels: locationRelatedLabels,
    location_validation_error: /location.{0,60}(required|enter|choose|select|add|missing)|(?:required|enter|choose|select|add|missing).{0,60}location/i.test(allText),
    final_publish_control_present: publishLabels.length > 0,
    final_publish_boundary: publishControls.some((node) => !node.disabled && node.getAttribute?.("aria-disabled") !== "true"),
    final_publish_labels: publishLabels.slice(0, 4),
    user_presence_challenge: /captcha|security check|checkpoint|confirm you are human/i.test(allText),
  };
}

function ppFacebookSnapshotSignature(snapshot) {
  const { step, ...state } = snapshot || {};
  return JSON.stringify(state);
}

function ppFacebookWorkflowStage(snapshot = null) {
  const current = snapshot || ppFacebookStageSnapshot(0);
  const headings = (current.headings || []).join(" ");
  const controls = (current.field_labels || []).map((row) => row.label || "").join(" ");
  const semantics = `${headings} ${controls} ${(current.semantic_controls || []).map((row) => row.label || "").join(" ")}`.toLowerCase();
  if (current.final_publish_boundary) return "REVIEW_OR_PUBLISH";
  const hasLocation = /\b(location|city|town|postal|zip|seller area|marketplace area)\b/i.test(semantics);
  const hasShipping = /\bshipping\b|delivery method|package weight|shipping carrier|shipping option/i.test(semantics);
  // Facebook keeps the Details preview heading/title and may expose an
  // unrelated "Minimum price you'll consider" input on later wizard steps.
  // Only classify Details when its actual named controls are mounted.
  const detailRows = current.details_controls || {};
  const hasLiveDetailsControls = ["title", "price", "description"].some((field) => detailRows[field] === true
    || detailRows[field]?.mounted === true);
  const fallbackDetailsLabels = (current.field_labels || []).some((row) => {
    const text = `${row.aria_label || ""} ${row.placeholder || ""} ${row.label || ""}`.replace(/\s+/g, " ").trim();
    return /\b(title|description)\b/i.test(text)
      || (/\bprice\b/i.test(text) && !/minimum|offer/i.test(text));
  });
  if (/\bdetails\b/i.test(headings) && (hasLiveDetailsControls || (!current.details_controls && fallbackDetailsLabels))) return "DETAILS";
  if (hasLocation && hasShipping) return "LOCATION_SHIPPING";
  if (hasLocation) return "LOCATION";
  if (hasShipping) return "SHIPPING";
  if (hasLiveDetailsControls || fallbackDetailsLabels) return "DETAILS";
  return "UNKNOWN";
}

function ppFacebookCreatePreviewCount(input) {
  const scope = ppFacebookMediaRoot(input);
  if (!scope?.querySelectorAll) return 0;
  const removeControls = Array.from(scope.querySelectorAll('button,[role="button"],[aria-label]'))
    .filter(ppVisible)
    .filter((node) => /remove\s+(?:photo|image)/i.test(ppFacebookControlLabel(node)));
  const previewNodes = Array.from(scope.querySelectorAll("img,canvas,[role='img'],[data-testid*='photo' i],[data-testid*='media' i]"))
    .filter(ppVisible);
  return Math.max(removeControls.length, previewNodes.length);
}

function ppFacebookCreateBaselineEvidence() {
  const adapter = globalThis.POSTERPRO_MARKETPLACE_ADAPTERS?.facebook;
  const snapshot = ppFacebookStageSnapshot(0);
  snapshot.workflow_stage = ppFacebookWorkflowStage(snapshot);
  const titleNode = ppFacebookDetailsControl("title");
  const priceNode = ppFacebookDetailsControl("price");
  const descriptionNode = ppFacebookDescriptionControl();
  const photoInput = adapter?.photos ? document.querySelector(adapter.photos) : null;
  const titleValue = String(titleNode?.value ?? "").replace(/\s+/g, " ").trim();
  const priceValue = String(priceNode?.value ?? "").replace(/\s+/g, " ").trim();
  const descriptionLength = String(descriptionNode?.value ?? descriptionNode?.innerText ?? descriptionNode?.textContent ?? "").replace(/\s+/g, " ").trim().length;
  const previewCount = ppFacebookCreatePreviewCount(photoInput);
  const overlays = Array.from(document.querySelectorAll('[role="dialog"],[role="listbox"],[role="menu"]')).filter(ppVisible);
  const coreDetailsControls = [titleNode, priceNode, descriptionNode].filter(Boolean);
  const overlayEvidence = overlays.slice(0, 12).map((node) => {
    const role = String(node.getAttribute?.("role") || "").toLowerCase();
    const containsDetailsControls = coreDetailsControls.length === 3
      && coreDetailsControls.every((control) => node === control || node.contains?.(control));
    const text = String(node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
    const marketplaceFormShell = role === "dialog" && containsDetailsControls;
    return {
      tag: String(node.tagName || "div").toLowerCase(), role: role || null,
      aria_label: String(node.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim().slice(0, 100) || null,
      text_excerpt: text.slice(0, 120),
      contains_details_controls: containsDetailsControls,
      marketplace_form_shell: marketplaceFormShell,
    };
  });
  // Marketplace's CREATE wizard may itself be rendered as a visible role=dialog.
  // That enclosing form is not an open transient menu or restored draft. Only
  // nested dialogs, listboxes, and menus block the clean baseline.
  const blockingOverlays = overlayEvidence.filter((item) => !item.marketplace_form_shell);
  const expandedControls = Array.from(document.querySelectorAll('[aria-expanded="true"]')).filter(ppVisible);
  const selectedCategory = (snapshot.field_labels || []).map((row) => String(row.label || "").trim())
    .find((label) => /^category\s+\S/i.test(label)) || null;
  const reasons = [];
  if (snapshot.workflow_stage !== "DETAILS") reasons.push("INITIAL_STAGE_NOT_DETAILS");
  if (!titleNode || !priceNode || !descriptionNode || !photoInput) reasons.push("FORM_ANCHORS_MISSING");
  if (previewCount !== 0) reasons.push("PREEXISTING_PHOTO_PREVIEWS");
  if (titleValue) reasons.push("PREEXISTING_TITLE");
  if (priceValue) reasons.push("PREEXISTING_PRICE");
  if (descriptionLength) reasons.push("PREEXISTING_DESCRIPTION");
  if (selectedCategory) reasons.push("PREEXISTING_CATEGORY");
  if (blockingOverlays.length) reasons.push("OPEN_DIALOG_OR_MENU");
  if (expandedControls.length) reasons.push("EXPANDED_CONTROL");
  let pageLoadedAt = null;
  try { pageLoadedAt = Number.isFinite(globalThis.performance?.timeOrigin) ? new Date(globalThis.performance.timeOrigin).toISOString() : null; } catch (_) { pageLoadedAt = null; }
  return {
    ok: reasons.length === 0, clean: reasons.length === 0,
    page_loaded_at: pageLoadedAt, initial_wizard_stage: snapshot.workflow_stage,
    initial_photo_preview_count: previewCount,
    initial_title_value: titleValue.slice(0, 140), initial_price_value: priceValue.slice(0, 40),
    initial_description_length: descriptionLength, initial_category_value: selectedCategory,
    open_overlay_count: blockingOverlays.length,
    visible_overlay_count: overlays.length,
    visible_overlay_evidence: overlayEvidence,
    expanded_control_count: expandedControls.length,
    expanded_control_evidence: expandedControls.slice(0, 12).map((node) => ({
      tag: String(node.tagName || "div").toLowerCase(),
      role: node.getAttribute?.("role") || null,
      label: String(node.getAttribute?.("aria-label") || node.innerText || node.textContent || "").replace(/\s+/g, " ").trim().slice(0, 100),
    })),
    form_anchor_count: [titleNode, priceNode, descriptionNode, photoInput].filter(Boolean).length,
    reasons, captured_at: new Date().toISOString(),
  };
}

async function ppProbeFacebookCreateContextReadOnly() {
  if (location.origin !== "https://www.facebook.com" || location.pathname !== "/marketplace/create/item") {
    return { ...ppContentScriptIdentity(), ok: false, read_only: true, error_code: "FACEBOOK_CREATE_BASELINE_WRONG_PAGE", page_url: ppSafePageUrl() };
  }
  const startedAt = Date.now();
  const adapter = globalThis.POSTERPRO_MARKETPLACE_ADAPTERS?.facebook;
  const readiness = adapter
    ? await ppWaitForFacebookCreateFormReady(adapter, { timeoutMs: 15000, pollMs: 200 })
    : { ready: false, wait_ms: 0, anchors: {} };
  const evidence = readiness.ready ? ppFacebookCreateBaselineEvidence() : null;
  const snapshot = readiness.ready ? ppFacebookStageSnapshot(0) : null;
  if (snapshot) snapshot.workflow_stage = ppFacebookWorkflowStage(snapshot);
  const bodyText = String(document.body?.innerText || document.body?.textContent || "").replace(/\s+/g, " ");
  const restoreEvidence = /\b(restore|resume|continue) (?:an? )?(?:saved )?draft\b|\bdiscard draft\b/i.test(bodyText);
  let performanceTimeOrigin = null;
  let navigationType = null;
  try {
    performanceTimeOrigin = Number.isFinite(globalThis.performance?.timeOrigin)
      ? new Date(globalThis.performance.timeOrigin).toISOString() : null;
    navigationType = globalThis.performance?.getEntriesByType?.("navigation")?.[0]?.type || null;
  } catch (_) { /* timing evidence is optional */ }
  return {
    ...ppContentScriptIdentity(), ok: Boolean(readiness.ready), read_only: true,
    error_code: readiness.ready ? null : "FACEBOOK_FORM_READY_TIMEOUT",
    page_url: ppSafePageUrl(), performance_time_origin: performanceTimeOrigin,
    navigation_type: navigationType,
    workflow_stage: snapshot?.workflow_stage || "NOT_READY",
    form_anchors: readiness.anchors || {},
    photo_preview_count: evidence?.initial_photo_preview_count ?? null,
    title_length: evidence?.initial_title_value?.length ?? null,
    price_value: evidence?.initial_price_value ?? null,
    description_length: evidence?.initial_description_length ?? null,
    category_value: evidence?.initial_category_value ?? null,
    visible_dialogs: (evidence?.visible_overlay_evidence || []).filter((item) => item.role === "dialog"),
    visible_overlays: evidence?.visible_overlay_evidence || [],
    blocking_overlay_count: evidence?.open_overlay_count ?? null,
    expanded_controls: evidence?.expanded_control_evidence || [],
    draft_restore_evidence: restoreEvidence,
    initial_form_clean: evidence?.clean ?? false,
    initial_form_reasons: evidence?.reasons || [],
    form_anchor_count: evidence?.form_anchor_count ?? 0,
    time_to_details_ready_ms: readiness.ready ? Math.max(0, Date.now() - startedAt) : readiness.wait_ms,
  };
}

function ppFacebookLocationStageState(locationResult) {
  if (locationResult?.filled === true) return "VERIFIED";
  const stages = locationResult?.diagnostic?.stages || [];
  const mountedLocationStage = stages.some((sample) =>
    ["LOCATION", "LOCATION_SHIPPING", "SHIPPING", "REVIEW_OR_PUBLISH"].includes(sample.workflow_stage)
    || sample.final_publish_boundary === true || Boolean(sample.location_validation_error));
  return mountedLocationStage ? "UNRESOLVED_AT_EXPECTED_STAGE" : "NOT_MOUNTED_YET";
}

async function ppWaitForFacebookFinalPublishBoundary(initialSnapshot, step, timeoutMs = 8000) {
  const startedAt = Date.now();
  const deadline = startedAt + Math.max(500, Math.min(Number(timeoutMs) || 8000, 12000));
  let snapshot = initialSnapshot;
  const samples = [];
  const record = (current) => {
    const buttons = (current?.buttons || []).filter((button) => /^(publish|publish listing|list|list item|post|post listing|submit listing)$/i.test(String(button.label || "")));
    samples.push({
      captured_at: new Date().toISOString(),
      wait_ms: Date.now() - startedAt,
      final_publish_control_present: Boolean(current?.final_publish_control_present),
      final_publish_boundary: Boolean(current?.final_publish_boundary),
      final_publish_labels: (current?.final_publish_labels || []).slice(0, 4),
      publish_buttons: buttons.slice(0, 4),
      validation_evidence: current?.validation_evidence || { invalid_controls: [], messages: [] },
      headings: (current?.headings || []).slice(0, 8),
      page_path: current?.page_path || null,
      user_presence_challenge: Boolean(current?.user_presence_challenge),
    });
    // A bounded trace is enough to establish whether React enabled Publish without
    // retaining a redundant snapshot for every animation frame.
    if (samples.length > 16) samples.splice(1, samples.length - 16);
  };
  record(snapshot);
  while (Date.now() < deadline && !snapshot.final_publish_boundary && !snapshot.user_presence_challenge) {
    await ppWait(300);
    snapshot = ppFacebookStageSnapshot(step);
    record(snapshot);
  }
  const waitMs = Date.now() - startedAt;
  const validation = snapshot.validation_evidence || {};
  const hasValidation = (validation.invalid_controls || []).length > 0 || (validation.messages || []).length > 0;
  return {
    ready: Boolean(snapshot.final_publish_boundary),
    error_code: snapshot.user_presence_challenge ? "FACEBOOK_USER_PRESENCE_CHALLENGE"
      : snapshot.final_publish_boundary ? null
        : hasValidation ? "FACEBOOK_FINAL_PUBLISH_VALIDATION_BLOCKED" : "FACEBOOK_FINAL_PUBLISH_NOT_READY",
    stage: snapshot.user_presence_challenge ? "USER_PRESENCE_CHALLENGE" : "FINAL_PUBLISH_READINESS",
    wait_ms: waitMs,
    samples,
    snapshot,
  };
}

async function ppFacebookFocusTelemetry() {
  let browser = null;
  try {
    const response = await chrome.runtime.sendMessage({ action: "posterpro_read_marketplace_focus" });
    browser = response?.ok ? response.context : { read_error: response?.error_code || "MARKETPLACE_FOCUS_READ_FAILED" };
  } catch (error) {
    browser = { read_error: String(error?.message || error).slice(0, 160) };
  }
  const vv = globalThis.visualViewport || null;
  return {
    timestamp: new Date().toISOString(),
    timestamp_ms: Date.now(),
    document_has_focus: document.hasFocus?.() === true,
    visibility_state: String(document.visibilityState || "unknown"),
    viewport: { width: Number(globalThis.innerWidth) || null, height: Number(globalThis.innerHeight) || null },
    visual_viewport: vv ? { width: Number(vv.width) || null, height: Number(vv.height) || null, scale: Number(vv.scale) || null } : null,
    device_pixel_ratio: Number(globalThis.devicePixelRatio) || null,
    browser,
  };
}

async function ppVerifyFacebookLocationAtReview(configuredLocation, descriptionTrace = null) {
  const configured = String(configuredLocation || "").trim();
  const readVisibleText = () => String(document.body?.innerText || document.body?.textContent || "").replace(/\s+/g, " ").trim();
  if (!configured) return { filled: false, detected: false, diagnostic: { stage: "MARKETPLACE_LOCATION_NOT_CONFIGURED", stages: [] } };
  if (!/^.+?,\s*(?:[A-Za-z]{2}|[A-Za-z ]+?)\s+\d{5}(?:-\d{4})?$/.test(configured)) {
    return { filled: false, detected: false, diagnostic: { stage: "FACEBOOK_LOCATION_PAYLOAD_NOT_CANONICAL", location_requirement_satisfied: false, location_value_verified: false, stages: [] } };
  }
  const stages = [];
  const clickedTargets = new Set();
  const visitedStates = new Set();
  let nextClicked = false;
  let locationControlDetected = false;
  let currentStep = 0;
  let locationOptionSelection = null;
  const adapter = (globalThis.POSTERPRO_MARKETPLACE_ADAPTERS || {}).facebook;

  for (let step = 0; step <= 4; step += 1) {
    currentStep = step;
    let snapshot = ppFacebookStageSnapshot(step);
    const stageKind = ppFacebookWorkflowStage(snapshot);
    snapshot.workflow_stage = stageKind;
    const stateSignature = ppFacebookSnapshotSignature(snapshot);
    if (visitedStates.has(stateSignature)) break;
    visitedStates.add(stateSignature);
    stages.push({ phase: "BEFORE_INTERACTION", ...snapshot });
    const traceAtLoop = await descriptionTrace?.(`LOCATION_FLOW_ENTRY_${step}`);
    if (traceAtLoop && !traceAtLoop.matches) return { filled: false, detected: false, diagnostic: {
      stage: "FACEBOOK_DESCRIPTION_NOT_PERSISTED", error_code: traceAtLoop.error_code,
      first_bad_operation: traceAtLoop.operation, description_mutation_trace: descriptionTrace.trace?.(), next_clicked: nextClicked, stages,
    } };
    if (ppFacebookLocationSelectionMatches(configured)) return {
      filled: true, detected: true, verified_value: configured,
      diagnostic: { stage: "LOCATION_VERIFIED_SELECTED_CONTROL", verified_mode: "FACEBOOK_SELECTED_LOCATION_CONTROL", location_requirement_satisfied: true, location_value_verified: true, location_option_selection: locationOptionSelection, next_clicked: nextClicked, stages },
    };

    const locationResolution = ppFacebookLocationControlResolution();
    const input = locationResolution.node;
    const traceAfterResolve = await descriptionTrace?.(`AFTER_LOCATION_CONTROL_RESOLUTION_${step}`);
    if (traceAfterResolve && !traceAfterResolve.matches) return { filled: false, detected: false, diagnostic: {
      stage: "FACEBOOK_DESCRIPTION_NOT_PERSISTED", error_code: traceAfterResolve.error_code,
      first_bad_operation: traceAfterResolve.operation, description_mutation_trace: descriptionTrace.trace?.(), next_clicked: nextClicked, stages,
    } };
    if (locationResolution.ambiguous) stages.push({ phase: "LOCATION_CONTROL_AMBIGUOUS", workflow_stage: stageKind, candidates: locationResolution.evidence });
    if (input && ["INPUT", "TEXTAREA"].includes(input.tagName)) {
      locationControlDetected = true;
      const existingLocation = String(input.value || "").trim();
      const existingLocationIsCommitted = ppVisible(input) && input.isConnected !== false
        && !input.disabled && input.getAttribute?.("aria-expanded") !== "true"
        && ppFacebookLocationCityMatches(configured, existingLocation);
      if (existingLocationIsCommitted) return {
        filled: true, detected: true, verified_value: existingLocation,
        diagnostic: {
          stage: "LOCATION_VERIFIED_EXISTING_MARKETPLACE_LOCALITY",
          verified_mode: "FACEBOOK_EXISTING_COLLAPSED_LOCATION_CONTROL",
          location_requirement_satisfied: true,
          location_value_verified: true,
          displayed_location: ppSafeEvidenceText(existingLocation, 120),
          requested_location: ppSafeEvidenceText(configured, 120),
          location_option_selection: null,
          next_clicked: nextClicked,
          stages,
        },
      };
      if (input === ppFacebookDescriptionControl()) return { filled: false, detected: true, diagnostic: { stage: "FACEBOOK_LOCATION_DESCRIPTION_CONTROL_COLLISION", error_code: "FACEBOOK_LOCATION_CONTROL_CROSSFIELD_REJECTED", workflow_stage: stageKind, candidates: locationResolution.evidence, next_clicked: nextClicked, stages } };
      if (String(input.value || "").trim().toLowerCase() !== configured.toLowerCase()) ppSetValue(input, configured);
      const optionDeadline = Date.now() + 1800;
      let options = ppFacebookLocationOptionCandidates(configured);
      while (!options.length && Date.now() < optionDeadline) {
        await ppWait(150);
        options = ppFacebookLocationOptionCandidates(configured);
      }
      stages.push({ phase: "AFTER_LOCATION_INPUT_AND_SUGGESTION_WAIT", input_value_matches: String(input.value || "").trim().toLowerCase() === configured.toLowerCase(), matching_option_count: options.length, ...ppFacebookStageSnapshot(step) });
      if (options.length === 1) {
        const option = options[0];
        const action = option.closest?.('[role="option"],[role="radio"],[role="menuitem"],[role="button"],button,label') || option;
        const optionLabel = ppSafeEvidenceText(option.innerText || option.textContent || option.getAttribute?.("aria-label") || option.labels?.[0]?.innerText, 140);
        action.click?.();
        const selectDeadline = Date.now() + 1800;
        let stillVisible = ppVisible(option);
        while (stillVisible && Date.now() < selectDeadline) {
          await ppWait(100);
          stillVisible = ppVisible(option);
        }
        const selectedState = option.checked === true || option.getAttribute?.("aria-selected") === "true" || option.getAttribute?.("aria-checked") === "true" || ["selected", "checked"].includes(String(option.getAttribute?.("data-state") || "").toLowerCase());
        const inputMatches = ppFacebookLocationSuggestionMatches(configured, input.value);
        const expanded = input.getAttribute?.("aria-expanded");
        locationOptionSelection = { option_label: optionLabel, matching_option_count: options.length, selected_state: selectedState, option_dismissed: !stillVisible, input_value_matches: inputMatches, aria_expanded_after: expanded || null };
        stages.push({ phase: "AFTER_LOCATION_SUGGESTION_SELECTION", location_option_selection: locationOptionSelection, ...ppFacebookStageSnapshot(step) });
        const selectionEvidence = selectedState || (!stillVisible && expanded !== "true");
        if (selectionEvidence && (inputMatches || ppFacebookLocationCityMatches(configured, input.value))) return { filled: true, detected: true, verified_value: configured, diagnostic: { stage: "LOCATION_VERIFIED_BY_SELECTED_SUGGESTION", verified_mode: "FACEBOOK_LOCATION_AUTOCOMPLETE", location_requirement_satisfied: true, location_value_verified: true, location_option_selection: locationOptionSelection, next_clicked: nextClicked, stages } };
      }
    }

    // Open only a control whose own visible label identifies location; select
    // only a rendered option that matches the operator-confirmed city/state/ZIP.
    const triggers = stageKind === "DETAILS" ? [] : Array.from(document.querySelectorAll('button,[role="button"],[role="combobox"],label')).filter(ppVisible);
    const trigger = !input && triggers.find((node) => {
      const label = String(node.innerText || node.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim();
      return label && /location|city|town|postal|zip|seller area|marketplace area/i.test(label) && !clickedTargets.has(node) && !node.disabled;
    });
    if (trigger) {
      locationControlDetected = true;
      clickedTargets.add(trigger);
      trigger.click();
      await ppWait(250);
      stages.push({ phase: "AFTER_LOCATION_CONTROL_OPEN", ...ppFacebookStageSnapshot(step) });
      const options = Array.from(document.querySelectorAll('[role="option"],[role="menuitem"],[role="radio"],button,[role="button"],li'))
        .filter(ppVisible).filter((node) => ppFacebookLocationSuggestionMatches(configured, String(node.innerText || node.getAttribute?.("aria-label") || "")));
      if (options[0]) {
        const option = options[0];
        const actionable = option.closest?.('button,[role="button"],[role="option"],[role="menuitem"],[role="radio"],li') || option;
        const optionLabel = ppSafeEvidenceText(option.innerText || option.textContent || option.getAttribute?.("aria-label"), 140);
        actionable.click();
        await ppWait(250);
        stages.push({ phase: "AFTER_LOCATION_OPTION_SELECT", ...ppFacebookStageSnapshot(step) });
        locationOptionSelection = { option_label: optionLabel, matching_option_count: options.length, selected_state: option.checked === true || option.getAttribute?.("aria-selected") === "true" || option.getAttribute?.("aria-checked") === "true", option_dismissed: !ppVisible(option) };
        if (ppFacebookLocationSelectionMatches(configured) || (locationOptionSelection.option_dismissed && ppFacebookLocationSuggestionMatches(configured, readVisibleText()))) return { filled: true, detected: true, verified_value: configured, diagnostic: { stage: "LOCATION_VERIFIED_EXPLICIT_SELECTION", verified_mode: "FACEBOOK_LOCATION_PICKER", location_requirement_satisfied: true, location_value_verified: true, location_option_selection: locationOptionSelection, next_clicked: nextClicked, stages } };
      }
    }

    snapshot = ppFacebookStageSnapshot(step);
    if (snapshot.user_presence_challenge) return { filled: false, detected: locationControlDetected, diagnostic: { stage: "FACEBOOK_USER_PRESENCE_CHALLENGE", user_presence_challenge: true, next_clicked: nextClicked, stages } };
    if (snapshot.final_publish_boundary) {
      if (!snapshot.location_validation_error && !locationControlDetected) return { filled: true, detected: true, verified_value: "LOCATION_INHERITED_BY_FACEBOOK", diagnostic: { stage: "LOCATION_INHERITED_BY_FACEBOOK", verified_mode: "FACEBOOK_INHERITED_LOCATION", location_requirement_satisfied: true, location_value_verified: false, next_clicked: nextClicked, stages } };
      return { filled: false, detected: locationControlDetected, diagnostic: { stage: snapshot.location_validation_error ? "LOCATION_REQUIRED_AT_FINAL_BOUNDARY" : "LOCATION_CONTROL_UNRESOLVED_AT_FINAL_BOUNDARY", location_validation_error: snapshot.location_validation_error, next_clicked: nextClicked, stages } };
    }
    if (step === 4) break;
    const detailsIntegrity = ppFacebookDetailsIntegrityEvidence({ location: configured, title: ppFacebookCurrentPayload?.title,
      price: ppFacebookCurrentPayload?.price, description: ppFacebookCurrentPayload?.description }, `LOCATION_FLOW_BEFORE_CONTINUE_${step}`);
    stages.push({ phase: "DETAILS_FIELD_INTEGRITY", ...detailsIntegrity });
    if (!detailsIntegrity.description.matches) return { filled: false, detected: locationControlDetected,
      diagnostic: { stage: detailsIntegrity.description.error_code || "FACEBOOK_DESCRIPTION_NOT_PERSISTED", error_code: detailsIntegrity.description.error_code,
        workflow_stage: detailsIntegrity.workflow_stage, description_evidence: detailsIntegrity.description, cross_field_guard: detailsIntegrity.cross_field_guard,
        next_clicked: nextClicked, stages } };
    const controls = Array.from(document.querySelectorAll('button,[role="button"],input[type="button"]'));
    const next = controls.find((node) => {
      const label = String(node.innerText || node.value || node.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim().toLowerCase();
      return ppVisible(node) && !node.disabled && node.getAttribute?.("aria-disabled") !== "true" && ["next", "continue"].includes(label);
    });
    if (!next || clickedTargets.has(next)) break;
    const beforeAdvance = await descriptionTrace?.(`BEFORE_SAFE_STAGE_ADVANCE_${step}`);
    if (beforeAdvance && !beforeAdvance.matches) return { filled: false, detected: locationControlDetected, diagnostic: {
      stage: "FACEBOOK_DESCRIPTION_NOT_PERSISTED", error_code: beforeAdvance.error_code,
      first_bad_operation: beforeAdvance.operation, description_mutation_trace: descriptionTrace.trace?.(), next_clicked: nextClicked, stages,
    } };
    clickedTargets.add(next);
    nextClicked = true;
    const advanceStartedAt = Date.now();
    await ppReportFacebookFormProgress("ADVANCING_STAGE", "started", { index: step + 1 });
    const before = ppFacebookSnapshotSignature(snapshot);
    next.click();
    const deadline = Date.now() + 8000;
    let after = snapshot;
    while (Date.now() < deadline) {
      await ppWait(200);
      after = ppFacebookStageSnapshot(step + 1);
      if (ppFacebookSnapshotSignature(after) !== before) break;
    }
    await ppReportFacebookFormProgress("ADVANCING_STAGE", "completed", { index: step + 1, duration_ms: Date.now() - advanceStartedAt });
    stages.push({ phase: "AFTER_SAFE_CONTINUE", ...after });
    const afterAdvance = await descriptionTrace?.(`AFTER_SAFE_STAGE_ADVANCE_${step}`);
    if (afterAdvance && !afterAdvance.matches) return { filled: false, detected: locationControlDetected, diagnostic: {
      stage: "FACEBOOK_DESCRIPTION_NOT_PERSISTED", error_code: afterAdvance.error_code,
      first_bad_operation: afterAdvance.operation, description_mutation_trace: descriptionTrace.trace?.(), next_clicked: nextClicked, stages,
    } };
    if (after.page_path !== snapshot.page_path || JSON.stringify(after.headings) !== JSON.stringify(snapshot.headings)) clickedTargets.clear();
    if (ppFacebookLocationReviewEvidence(configured, after, readVisibleText())) return { filled: true, detected: true, verified_value: configured, diagnostic: { stage: "LOCATION_VERIFIED_AFTER_SAFE_CONTINUE", verified_mode: "FACEBOOK_REVIEW_LOCATION", location_requirement_satisfied: true, location_value_verified: true, location_option_selection: locationOptionSelection, next_clicked: nextClicked, stages } };
  }
  const finalSnapshot = ppFacebookStageSnapshot(currentStep);
  const inherited = finalSnapshot.final_publish_boundary && !finalSnapshot.location_validation_error && !locationControlDetected;
  return {
    filled: inherited, detected: inherited || locationControlDetected,
    verified_value: inherited ? "LOCATION_INHERITED_BY_FACEBOOK" : null,
    diagnostic: {
      stage: finalSnapshot.user_presence_challenge ? "FACEBOOK_USER_PRESENCE_CHALLENGE" : inherited ? "LOCATION_INHERITED_BY_FACEBOOK" : finalSnapshot.final_publish_boundary ? (finalSnapshot.location_validation_error ? "LOCATION_REQUIRED_AT_FINAL_BOUNDARY" : "LOCATION_CONTROL_UNRESOLVED_AT_FINAL_BOUNDARY") : "LOCATION_NOT_VERIFIED_SAFE_FLOW_INCOMPLETE",
      verified_mode: inherited ? "FACEBOOK_INHERITED_LOCATION" : null,
      location_requirement_satisfied: inherited,
      location_value_verified: false,
      next_clicked: nextClicked,
      stages,
    },
  };
}

function ppFacebookFormAnchors(adapter) {
  const visibleField = (selectors, labels) => {
    const node = labels.includes("description") || labels.includes("describe") ? ppFacebookDescriptionControl()
      : labels.includes("location") ? ppFacebookLocationControlResolution().node
        : labels.includes("title") || labels.includes("price") ? ppFacebookDetailsControl(labels[0]) : ppFindField(selectors, labels);
    return Boolean(node && ppVisible(node));
  };
  const photoInput = document.querySelector(adapter.photos);
  const visibleForms = Array.from(document.querySelectorAll("form,[role='dialog']")).filter(ppVisible);
  const anchors = {
    title: visibleField(adapter.title, ["title"]),
    price: visibleField(adapter.price, ["price"]),
    description: visibleField(adapter.description, ["description", "describe"]),
    photos: Boolean(photoInput && !photoInput.disabled),
    visible_form: visibleForms.length > 0,
  };
  anchors.core_anchor_count = [anchors.title, anchors.price, anchors.description].filter(Boolean).length;
  return anchors;
}

async function ppWaitForFacebookCreateFormReady(adapter, { timeoutMs = 15000, pollMs = 200 } = {}) {
  const startedAt = Date.now();
  let wake = null;
  let observer = null;
  try {
    if (typeof MutationObserver === "function" && document.documentElement) {
      observer = new MutationObserver(() => wake?.());
      observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
    }
    let anchors;
    do {
      anchors = ppFacebookFormAnchors(adapter);
      if (anchors.title && anchors.price && anchors.description && anchors.photos) {
        return { ready: true, wait_ms: Date.now() - startedAt, anchors };
      }
      const remaining = timeoutMs - (Date.now() - startedAt);
      if (remaining <= 0) break;
      const mutation = new Promise((resolve) => { wake = resolve; });
      await Promise.race([ppWait(Math.min(pollMs, remaining)), mutation]);
      wake = null;
    } while (Date.now() - startedAt <= timeoutMs);
    return { ready: false, wait_ms: Date.now() - startedAt, anchors: ppFacebookFormAnchors(adapter) };
  } finally {
    wake = null;
    observer?.disconnect();
  }
}

function ppMarketplacePageEvidence(marketplace, anchors) {
  const bodyText = String(document.body?.innerText || "").replace(/\s+/g, " ").trim();
  const loginState = ppDetectLoginState(marketplace);
  const loading = Boolean(document.querySelector('[aria-busy="true"],[role="progressbar"],[data-testid*="loading" i]') || /\b(loading|please wait|one moment)\b/i.test(bodyText));
  const headings = Array.from(document.querySelectorAll("h1,h2,h3,[role='heading']"))
    .filter(ppVisible).map((node) => ppSafeEvidenceText(node.innerText || node.textContent, 100)).filter(Boolean).slice(0, 8);
  const visibleInputs = Array.from(document.querySelectorAll("input,textarea,button,[role='button']")).filter(ppVisible);
  const url = ppSafePageUrl();
  const challenge = loginState === "CAPTCHA_REQUIRED" || loginState === "CHECKPOINT_REQUIRED" || loginState === "ACCOUNT_RESTRICTION";
  const shell = /marketplace|create new listing|new listing|item for sale|sell an item/i.test(bodyText);
  const classification = loginState === "LOGIN_REQUIRED" ? "LOGIN_REQUIRED"
    : challenge ? loginState
      : loading || shell ? "FORM_HYDRATION_TIMEOUT" : "MARKETPLACE_FORM_CHANGED";
  return { classification, evidence: {
    current_url: url, page_title: ppSafeEvidenceText(document.title || "", 160),
    document_ready_state: ["loading", "interactive", "complete"].includes(document.readyState) ? document.readyState : "unknown",
    login_state: loginState, body_text_summary: ppSafeEvidenceText(bodyText, 360), visible_headings: headings,
    loading_indicators: loading, visible_control_count: visibleInputs.length,
    captcha_or_checkpoint_text: /captcha|security check|checkpoint|confirm you are human/i.test(bodyText),
    form_anchors: anchors,
  } };
}

function ppFacebookMediaRoot(input) {
  const dialog = input?.closest?.('form, [role="dialog"], [data-pagelet*="Marketplace"]');
  if (dialog) return dialog;
  const title = ppFacebookDetailsControl("title");
  const ancestors = new Set();
  for (let node = input; node; node = node.parentElement) ancestors.add(node);
  for (let node = title; node; node = node.parentElement) if (ancestors.has(node)) return node;
  return input?.parentElement || document.body || document;
}

function ppFacebookMediaSnapshot(input) {
  const scope = ppFacebookMediaRoot(input);
  const sourceEntries = [];
  for (const image of Array.from(scope?.querySelectorAll?.("img") || []).filter(ppVisible)) {
    const source = String(image.currentSrc || image.src || "");
    if (source) sourceEntries.push({ kind: "image", source, label: ppSafeEvidenceText(image.alt || image.getAttribute?.("aria-label") || "", 100) });
  }
  for (const element of Array.from(scope?.querySelectorAll?.("[style], [role='img'], canvas, [data-testid*='photo' i], [data-testid*='media' i]") || []).filter(ppVisible)) {
    const background = String(globalThis.getComputedStyle?.(element)?.backgroundImage || "");
    if (background && background !== "none" && /url\(/i.test(background)) {
      sourceEntries.push({ kind: "background", source: background, label: ppSafeEvidenceText(element.getAttribute?.("aria-label") || element.getAttribute?.("data-testid") || "", 100) });
    }
  }
  const mediaRoles = Array.from(scope?.querySelectorAll?.('[role="img"], canvas, [data-testid*="photo" i], [data-testid*="media" i]') || []).filter(ppVisible);
  const actionLabels = Array.from(scope?.querySelectorAll?.("button, [role='button'], [aria-label]") || [])
    .filter(ppVisible)
    .map((node) => ppSafeEvidenceText(`${node.getAttribute?.("aria-label") || ""} ${node.innerText || node.textContent || ""}`, 120))
    .filter((text) => text && /remove (photo|image)|delete (photo|image)|edit photo|photo \d|image \d/i.test(text));
  return {
    scope_kind: scope?.tagName?.toLowerCase() || "document",
    entries: sourceEntries,
    media_role_count: mediaRoles.length,
    media_action_count: actionLabels.length,
    media_actions: [...new Set(actionLabels)].slice(0, 8),
  };
}

function ppFacebookImagePreviewEvidence(input, priorSnapshot = null) {
  const before = priorSnapshot || { entries: [], media_role_count: 0, media_action_count: 0 };
  const after = ppFacebookMediaSnapshot(input);
  const priorEntries = new Set((before.entries || []).map((entry) => `${entry.kind}\n${entry.source}`));
  const newEntries = after.entries.filter((entry) => !priorEntries.has(`${entry.kind}\n${entry.source}`));
  const priorActions = new Set(before.media_actions || []);
  const newActions = after.media_actions.filter((label) => !priorActions.has(label));
  const roleDelta = Math.max(0, after.media_role_count - Number(before.media_role_count || 0));
  const actionDelta = Math.max(0, after.media_action_count - Number(before.media_action_count || 0));
  const acceptedEntry = newEntries.find((entry) => entry.kind === "image" || entry.kind === "background") || null;
  const acceptedAction = newActions[0] || null;
  const accepted = Boolean(acceptedEntry || acceptedAction || roleDelta > 0);
  const source = acceptedEntry?.source || "";
  return {
    accepted,
    preview_count_delta: newEntries.length + roleDelta + actionDelta,
    accepted_preview: acceptedEntry ? {
      representation: acceptedEntry.kind,
      source_kind: source.startsWith("blob:") || source.includes("blob:") ? "blob" : source.startsWith("data:") || source.includes("data:image") ? "data" : "marketplace-media",
      alt: acceptedEntry.label,
    } : acceptedAction ? { representation: "media-control", label: acceptedAction } : roleDelta > 0 ? { representation: "media-role", count_delta: roleDelta } : null,
    evidence_delta: {
      image_or_background_count: newEntries.length,
      media_role_count: roleDelta,
      media_action_count: actionDelta,
      scope_kind: after.scope_kind,
    },
  };
}

async function ppWaitForFacebookImagePreview(input, priorSnapshot, timeoutMs = 5000) {
  const started = Date.now();
  let evidence = ppFacebookImagePreviewEvidence(input, priorSnapshot);
  while (!evidence.accepted && Date.now() - started < timeoutMs) {
    await ppWait(150);
    evidence = ppFacebookImagePreviewEvidence(input, priorSnapshot);
  }
  return { ...evidence, wait_ms: Date.now() - started };
}

async function ppFillListing({ marketplace, payload = {}, images = [], diagnostic = false }) {
  const adapters = globalThis.POSTERPRO_MARKETPLACE_ADAPTERS || {};
  const market = String(marketplace || "").toLowerCase();
  const adapter = adapters[market];
  if (!adapter) return { ok: false, error_code: "ADAPTER_NOT_IMPLEMENTED", error: `No field adapter is available for ${marketplace}.` };
  if (!adapter.hosts.some((host) => location.hostname === host || location.hostname.endsWith(`.${host}`))) return { ok: false, error_code: "UNEXPECTED_MARKETPLACE_DOMAIN", error: "The active page does not match the requested marketplace." };
  let facebookCreateBaseline = null;
  if (market === "facebook" && !diagnostic) {
    const sourceImageCount = Array.isArray(payload.image_urls) ? payload.image_urls.length : 0;
    const intendedUploadCount = Array.isArray(images) ? images.length : 0;
    if (intendedUploadCount > 10) return {
      ...ppContentScriptIdentity(), ok: false, capability_ready: false, stage: "CLEAN_FORM_BASELINE",
      error_code: "FACEBOOK_IMAGE_COUNT_EXCEEDS_LIMIT", submission_performed: false,
      source_image_count: sourceImageCount, intended_upload_count: intendedUploadCount,
      missing_required_fields: ["photos"],
    };
    await ppReportFacebookFormProgress("CLEAN_FORM_CHECK_STARTED", "started");
    const formReadiness = await ppWaitForFacebookCreateFormReady(adapter, { timeoutMs: 15000, pollMs: 200 });
    if (!formReadiness.ready) return {
      ...ppContentScriptIdentity(), ok: false, capability_ready: false, stage: "FACEBOOK_FORM_READY",
      error_code: "FACEBOOK_FORM_READY_TIMEOUT", submission_performed: false,
      form_ready_wait_ms: formReadiness.wait_ms, form_anchors: formReadiness.anchors,
      missing_required_fields: ["form_ready"],
    };
    await ppReportFacebookFormProgress("FACEBOOK_FORM_READY", "completed", { duration_ms: formReadiness.wait_ms });
    facebookCreateBaseline = ppFacebookCreateBaselineEvidence();
    if (!facebookCreateBaseline.clean) return {
      ...ppContentScriptIdentity(), ok: false, capability_ready: false, stage: "CLEAN_FORM_BASELINE",
      error_code: "FACEBOOK_DIRTY_CREATE_FORM", submission_performed: false,
      clean_create_form: facebookCreateBaseline, source_image_count: sourceImageCount,
      intended_upload_count: intendedUploadCount, form_ready_wait_ms: formReadiness.wait_ms,
      missing_required_fields: ["clean_create_form"],
    };
    await ppReportFacebookFormProgress("CLEAN_FORM_VERIFIED", "completed", { count: facebookCreateBaseline.initial_photo_preview_count });
  }
  if (market === "facebook") {
    ppFacebookCurrentPayload = payload;
    ppFacebookDetailsDescriptionVerification = null;
    ppFacebookDetailsFieldVerification = { title: null, price: null };
    await ppReportFacebookFormProgress("FORM_FILLING_STARTED", "started");
  }

  const login_state = ppDetectLoginState(market);
  if (["LOGIN_REQUIRED", "CAPTCHA_REQUIRED", "CHECKPOINT_REQUIRED", "ACCOUNT_RESTRICTION", "UNKNOWN"].includes(login_state)) {
    return { ...ppContentScriptIdentity(), ok: false, stage: login_state, login_state, page_state: login_state, error_code: login_state, populated_fields: [], field_results: [], missing_required_fields: [], submission_performed: false };
  }
  const populated_fields = [];
  const missing_required_fields = [];
  const field_results = [];
  const facebookReadinessTimeline = [];
  const facebookDescriptionMutationTrace = [];
  let facebookOfferState = null;
  const captureDescription = async (operation) => {
    if (market !== "facebook") return { matches: true };
    if (!ppNormalizeFacebookDescription(payload.description)) return { matches: true, not_applicable: true };
    await ppFacebookWaitForRenderFrames();
    await ppWait(80);
    const checkpoint = ppFacebookDescriptionMutationCheckpoint(operation, payload.description, payload);
    facebookDescriptionMutationTrace.push(checkpoint);
    return checkpoint;
  };
  const stopOnDescriptionLoss = (operation, checkpoint) => ({
    ...ppContentScriptIdentity(), ok: false, capability_ready: false,
    stage: "FACEBOOK_DESCRIPTION_MUTATION_TRACE",
    error_code: checkpoint.error_code || "FACEBOOK_DESCRIPTION_NOT_PERSISTED",
    first_bad_operation: operation,
    description_mutation_trace: facebookDescriptionMutationTrace.slice(-24),
    submission_performed: false, missing_required_fields: ["description"],
  });
  const record = (field, required, detected, attempted, filled, value, error_code = null, node = null, selectors = [], labels = [], interactionDiagnostic = null) => {
    field_results.push({
      field, required, detected, attempted, filled,
      verified_value: filled ? String(value ?? "").slice(0, 160) : null,
      error_code,
      ...(filled ? {} : { selector_diagnostic: ppSelectorDiagnostic(field, node, selectors, labels) }),
      ...(market === "mercari" && ["category", "condition", "shipping"].includes(field) && interactionDiagnostic ? { interaction_diagnostic: interactionDiagnostic } : {}),
      ...(market === "facebook" && ["category", "location"].includes(field) && interactionDiagnostic ? { interaction_diagnostic: interactionDiagnostic } : {}),
    });
    if (market === "facebook") facebookReadinessTimeline.push(ppFacebookReadinessPulse(payload, field, field));
  };
  const fields = [["title", adapter.title, ["title"]], ["price", adapter.price, ["price"]], ["description", adapter.description, ["description", "describe"]]];
  // Later interactions can intentionally advance a marketplace SPA and
  // unmount the first-step fields. Capture create-form readiness up front.
  const resolveCore = (name, selectors, labels) => market === "facebook"
    ? name === "description" ? ppFacebookDescriptionControl() : ppFacebookDetailsControl(name)
    : ppFindField(selectors, labels);
  const coreFormDetectedAtStart = fields.every(([name, selectors, labels]) => Boolean(resolveCore(name, selectors, labels)));
  if (market === "facebook") await ppReportFacebookFormProgress("FACEBOOK_FORM_READY", coreFormDetectedAtStart ? "completed" : "checkpoint", { count: fields.filter(([name, selectors, labels]) => Boolean(resolveCore(name, selectors, labels))).length });
  for (const [name, selectors, labels] of fields) {
    const progressStage = name === "title" ? "TITLE_SET" : name === "price" ? "PRICE_SET" : "DESCRIPTION_SET";
    if (market === "facebook") await ppReportFacebookFormProgress(progressStage, "started", { field: name });
    const node = resolveCore(name, selectors, labels);
    const value = name === "price" ? payload[name] : payload[name];
    const descriptionFill = market === "facebook" && name === "description"
      ? await ppFacebookSetAndVerifyDescription(adapter, value, "DESCRIPTION_AFTER_FILL")
      : null;
    const detailsFill = market === "facebook" && ["title", "price"].includes(name)
      ? await ppFacebookSetAndVerifyDetailsField(name, value) : null;
    const filled = descriptionFill ? descriptionFill.ok : detailsFill ? detailsFill.ok : ppSetValue(node, value);
    if (filled) populated_fields.push(name); else missing_required_fields.push(name);
    record(name, true, Boolean(node), true, filled,
      market === "facebook" && name === "description" ? descriptionFill?.evidence?.matches === true
        : market === "facebook" && detailsFill ? detailsFill.evidence?.observed : value,
      filled ? null : (descriptionFill?.error_code || detailsFill?.error_code || (node ? "VALUE_NOT_ACCEPTED" : "FIELD_NOT_FOUND")), node, selectors, labels);
    if (market === "facebook") await ppReportFacebookFormProgress(progressStage, "completed", { field: name, error_code: filled ? null : (descriptionFill?.error_code || detailsFill?.error_code || "FACEBOOK_CONTROL_NOT_ACCEPTED") });
    if (descriptionFill && field_results.length) {
      field_results[field_results.length - 1].description_evidence = descriptionFill.evidence;
      field_results[field_results.length - 1].description_assignment_returned = descriptionFill.assignment_returned;
    }
    if (detailsFill && field_results.length) field_results[field_results.length - 1].details_field_evidence = detailsFill.evidence;
    if (market === "facebook" && name === "description" && filled) {
      const checkpoint = await captureDescription("AFTER_DESCRIPTION_PERSISTENCE_VERIFICATION");
      if (!checkpoint.matches) return stopOnDescriptionLoss(checkpoint.operation, checkpoint);
    }
  }

  if (market === "facebook") {
    await ppReportFacebookFormProgress("OFFERS_DISABLED", "started");
    facebookOfferState = await ppFacebookDisableOffers(payload);
    const { node: _offerNode, ...offerEvidence } = facebookOfferState;
    field_results.push({
      field: "offers", required: false, detected: facebookOfferState.control_present,
      attempted: facebookOfferState.action === "SWITCHED_OFF", filled: facebookOfferState.resolved,
      verified_value: facebookOfferState.offers_enabled === false ? "OFF" : facebookOfferState.explicit_opt_in ? "ON (explicit opt-in)" : null,
      error_code: facebookOfferState.error_code || null,
      interaction_diagnostic: offerEvidence,
    });
    if (!facebookOfferState.resolved) missing_required_fields.push("offers");
    await ppReportFacebookFormProgress("OFFERS_DISABLED", "completed", { error_code: facebookOfferState.error_code || null });
    const checkpoint = await captureDescription("AFTER_OFFERS_HANDLING");
    if (!checkpoint.matches) return stopOnDescriptionLoss(checkpoint.operation, checkpoint);
  }

  const diagnosticValues = {
    // Mercari's canonical payload calls this `category_hint`; Facebook uses
    // the same field. Treat all supported category keys consistently so the
    // live job doesn't open the picker with an empty target while diagnostics
    // (which send `category`) appear to work.
    category: payload.marketplace_category || payload.category || payload.category_hint,
    condition: ppCanonicalCondition(market, payload.condition),
    availability: payload.availability || "Available",
    department: payload.department || "Women",
    subcategory: payload.subcategory || "Tops",
    shipping: payload.delivery_method || payload.shipping?.mode,
    shipping_payer: payload.shipping?.shipping_payer,
    shipping_method: payload.shipping?.shipping_method,
    package: payload.shipping?.parcel_size || payload.shipping?.parcel_weight,
  };
  const requiredSemantic = new Set(adapter.required_semantic_fields || ["category", "condition"]);
  const semanticFields = [
    ["category", adapter.category, diagnosticValues.category],
    ["condition", adapter.condition, diagnosticValues.condition],
    ["availability", adapter.availability, diagnosticValues.availability],
    ["department", adapter.department, diagnosticValues.department],
    ["subcategory", adapter.subcategory, diagnosticValues.subcategory],
    ["shipping", adapter.shipping, diagnosticValues.shipping],
    ["shipping_payer", adapter.shipping_payer, diagnosticValues.shipping_payer],
    ["shipping_method", adapter.shipping_method, diagnosticValues.shipping_method],
    ["package", adapter.package, diagnosticValues.package],
  ];
  for (const [name, selectors, value] of semanticFields) {
    const required = requiredSemantic.has(name);
    if (!required && (value == null || !Array.isArray(selectors) || selectors.length === 0)) continue;
    const node = ppFindField(selectors, [name, ...(name === "shipping" ? ["delivery", "parcel"] : [])]);
    if (!required && !node) continue;
    if (market === "facebook" && ["category", "condition"].includes(name) && node) {
      const before = await captureDescription(`BEFORE_${name.toUpperCase()}_HANDLING`);
      if (!before.matches) return stopOnDescriptionLoss(before.operation, before);
      const ownership = ppFacebookValidateMutationOwnership(name, node);
      if (!ownership.ok) return { ...ppContentScriptIdentity(), ok: false, capability_ready: false,
        stage: "FACEBOOK_FIELD_OWNERSHIP_GUARD", error_code: ownership.error_code, target_field: name,
        resolved_control: ppSelectorDiagnostic(name, node, selectors, [name]),
        description_mutation_trace: facebookDescriptionMutationTrace.slice(-24), submission_performed: false,
        missing_required_fields: [name] };
    }
    const progressStage = name === "category" ? "CATEGORY_RESOLUTION_STARTED" : name === "condition" ? "CONDITION_SET" : null;
    if (market === "facebook" && progressStage) await ppReportFacebookFormProgress(progressStage, "started", { field: name });
    const selected = await ppSelectOption(node, value, market, name, { diagnostic, payload });
    if (selected.ok) populated_fields.push(name); else if (required) missing_required_fields.push(name);
    record(name, required, Boolean(node), true, selected.ok, selected.value || value, selected.error_code || null, node, selectors, [name, ...(name === "shipping" ? ["delivery", "parcel"] : [])], selected.diagnostic || null);
    if (market === "facebook" && progressStage) {
      await ppReportFacebookFormProgress(progressStage, "completed", { field: name, error_code: selected.error_code || null });
      if (selected.ok) await ppReportFacebookFormProgress(name === "category" ? "CATEGORY_SET" : "CONDITION_SET", "checkpoint", { field: name });
    }
    if (market === "facebook" && ["category", "condition"].includes(name)) {
      const checkpoint = await captureDescription(`AFTER_${name.toUpperCase()}_HANDLING`);
      if (!checkpoint.matches) return stopOnDescriptionLoss(checkpoint.operation, checkpoint);
    }
  }

  const diagnosticOptionalValues = diagnostic ? {
    brand: payload.brand || "PosterPro Test",
    size: payload.size || "One size",
    original_price: payload.original_price || payload.price || 10,
    // A Facebook location must come from operator configuration or an
    // already-visible destination value. Never invent a synthetic ZIP.
    location: payload.location || (market === "facebook" ? null : undefined),
    quantity: payload.quantity || 1,
    weight: payload.shipping?.parcel_weight || "1 lb",
  } : {};
  const optionalValues = { ...diagnosticOptionalValues, ...payload };
  for (const name of ["brand", "size", "color", "material", "location", "quantity", "weight"]) {
    // Facebook location may be inherited or exposed only after safe Next
    // transitions; it is not necessarily an editable page-one text input.
    if (market === "facebook" && name === "location" && !diagnostic) continue;
    const required = (adapter.required_text_fields || []).includes(name);
    const node = market === "facebook"
      ? name === "location" ? ppFacebookLocationControlResolution().node : ppFacebookOptionalTextControl(name)
      : ppFindField(adapter[name], [name]);
    if ((optionalValues[name] == null || optionalValues[name] === "") && required && node && String(node.value || "").trim()) {
      const existingValue = String(node.value).trim();
      populated_fields.push(name);
      record(name, true, true, false, true, existingValue, null, node, adapter[name], [name]);
      continue;
    }
    if (optionalValues[name] == null || optionalValues[name] === "") {
      if (required) {
        missing_required_fields.push(name);
        record(name, true, Boolean(node), true, false, null, name === "location" && market === "facebook" ? "MARKETPLACE_LOCATION_NOT_CONFIGURED" : "FIELD_OR_MAPPED_VALUE_MISSING", node, adapter[name], [name]);
      }
      continue;
    }
    if (market === "facebook" && node) {
      const ownership = ppFacebookValidateMutationOwnership(name, node);
      if (!ownership.ok) return { ...ppContentScriptIdentity(), ok: false, capability_ready: false,
        stage: "FACEBOOK_FIELD_OWNERSHIP_GUARD", error_code: ownership.error_code, target_field: name,
        submission_performed: false, description_mutation_trace: facebookDescriptionMutationTrace.slice(-24),
        missing_required_fields: [name] };
    }
    const filled = ppSetValue(node, optionalValues[name]);
    // A safe connection diagnostic must prove that PosterPro can reach and
    // exercise the form's core controls. Account/fulfilment text such as a
    // location can be configured later and is not a reason to report the
    // entire Facebook connection as failed. Real CREATE jobs still enforce
    // the adapter's required_text_fields contract.
    if (filled) populated_fields.push(name); else if (required) missing_required_fields.push(name);
    record(name, required, Boolean(node), true, filled, optionalValues[name], filled ? null : (node ? "VALUE_NOT_ACCEPTED" : "FIELD_NOT_FOUND"), node, adapter[name], [name]);
    if (market === "facebook" && filled) {
      const checkpoint = await captureDescription(`AFTER_${name.toUpperCase()}_HANDLING`);
      if (!checkpoint.matches) return stopOnDescriptionLoss(checkpoint.operation, checkpoint);
    }
  }

  let uploadImages = Array.isArray(images) ? images : [];
  if (diagnostic && uploadImages.length === 0) {
    const data_url = ppSyntheticImage();
    if (data_url) uploadImages = [{ data_url, file_name: "posterpro-safe-diagnostic.jpg" }];
  }
  let uploaded_image_count = 0;
  let photoUploadEvidence = null;
  const input = document.querySelector(adapter.photos);
  const priorPhotoSnapshot = market === "facebook" ? ppFacebookMediaSnapshot(input) : null;
  const detectedPhotoInput = Boolean(input && !input.disabled);
  if (detectedPhotoInput && uploadImages.length) {
    const preUploadPreviewCount = market === "facebook" ? ppFacebookCreatePreviewCount(input) : 0;
    const sourceImageCount = Array.isArray(payload.image_urls) ? payload.image_urls.length : uploadImages.length;
    const intendedUploadCount = uploadImages.length;
    if (market === "facebook" && preUploadPreviewCount !== 0) return {
      ...ppContentScriptIdentity(), ok: false, capability_ready: false, stage: "IMAGE_UPLOAD_PREFLIGHT",
      error_code: "FACEBOOK_DIRTY_CREATE_FORM", submission_performed: false,
      clean_create_form: facebookCreateBaseline, photo_upload: {
        source_image_count: sourceImageCount, intended_upload_count: intendedUploadCount,
        pre_upload_preview_count: preUploadPreviewCount, data_transfer_file_count: 0,
      }, missing_required_fields: ["clean_create_form"],
    };
    if (market === "facebook") await ppReportFacebookFormProgress("IMAGE_UPLOAD_STARTED", "started", { count: intendedUploadCount });
    if (market === "facebook") {
      const checkpoint = await captureDescription("BEFORE_IMAGE_UPLOAD");
      if (!checkpoint.matches) return stopOnDescriptionLoss(checkpoint.operation, checkpoint);
    }
    const transfer = new DataTransfer();
    uploadImages.forEach((image, index) => { const file = ppDataUrlToFile(image, index); if (file) transfer.items.add(file); });
    if (market === "facebook" && transfer.files.length !== intendedUploadCount) return {
      ...ppContentScriptIdentity(), ok: false, capability_ready: false, stage: "IMAGE_UPLOAD_PREFLIGHT",
      error_code: "FACEBOOK_IMAGE_TRANSFER_COUNT_MISMATCH", submission_performed: false,
      photo_upload: { source_image_count: sourceImageCount, intended_upload_count: intendedUploadCount,
        pre_upload_preview_count: preUploadPreviewCount, data_transfer_file_count: transfer.files.length },
      missing_required_fields: ["photos"],
    };
    if (transfer.files.length) {
      input.files = transfer.files;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      uploaded_image_count = transfer.files.length; populated_fields.push("photos");
    }
  }
  const facebookPhotoEvidence = market === "facebook" && uploaded_image_count > 0
    ? await ppWaitForFacebookImagePreview(input, priorPhotoSnapshot)
    : null;
  const postUploadPreviewCount = market === "facebook" ? ppFacebookCreatePreviewCount(input) : null;
  const preUploadPreviewCount = market === "facebook" ? (facebookCreateBaseline?.initial_photo_preview_count ?? 0) : null;
  const actualPreviewDelta = market === "facebook" ? postUploadPreviewCount - preUploadPreviewCount : null;
  const photoOk = uploaded_image_count > 0 && (market !== "facebook" || (
    facebookPhotoEvidence?.accepted === true
    && preUploadPreviewCount === 0
    && postUploadPreviewCount === uploaded_image_count
    && actualPreviewDelta === uploaded_image_count
  ));
  if (market === "facebook") photoUploadEvidence = {
    source_image_count: Array.isArray(payload.image_urls) ? payload.image_urls.length : uploaded_image_count,
    intended_upload_count: uploadImages.length,
    pre_upload_preview_count: preUploadPreviewCount,
    data_transfer_file_count: uploaded_image_count,
    post_upload_preview_count: postUploadPreviewCount,
    preview_delta: actualPreviewDelta,
    accepted: photoOk,
  };
  if (market === "facebook") {
    await ppReportFacebookFormProgress("IMAGE_UPLOAD_STARTED", "completed", { count: uploaded_image_count, wait_ms: facebookPhotoEvidence?.wait_ms || 0, error_code: photoOk ? null : "FACEBOOK_IMAGE_UPLOAD_TIMEOUT" });
    await ppReportFacebookFormProgress("IMAGE_UPLOAD_COMPLETE", "checkpoint", { count: uploaded_image_count, wait_ms: facebookPhotoEvidence?.wait_ms || 0 });
    const checkpoint = await captureDescription("AFTER_IMAGE_UPLOAD_SETTLED");
    if (!checkpoint.matches) return stopOnDescriptionLoss(checkpoint.operation, checkpoint);
  }
  if (!photoOk) missing_required_fields.push("photos");
  record("photos", true, detectedPhotoInput, true, photoOk, `${uploaded_image_count} synthetic/canonical image(s)`, photoOk ? null : (market === "facebook" && uploaded_image_count ? "IMAGE_PREVIEW_NOT_ACCEPTED" : detectedPhotoInput ? "IMAGE_TRANSFER_FAILED" : "FILE_INPUT_NOT_FOUND"), input, [adapter.photos], ["photos", "add photos", "upload"]);
  if (market === "facebook") {
    const photoResult = field_results.find((item) => item.field === "photos");
    if (photoResult) photoResult.interaction_diagnostic = {
      upload_count: uploaded_image_count,
      ...(photoUploadEvidence || {}),
      preview_wait_ms: facebookPhotoEvidence?.wait_ms || 0,
      preview_count_delta: facebookPhotoEvidence?.preview_count_delta || 0,
      accepted_preview: facebookPhotoEvidence?.accepted_preview || null,
      evidence_delta: facebookPhotoEvidence?.evidence_delta || null,
    };
  }

  if (market === "facebook" && !diagnostic) {
    await ppReportFacebookFormProgress("LOCATION_SET", "started", { field: "location" });
    const beforeLocation = await captureDescription("BEFORE_LOCATION_VERIFICATION");
    if (!beforeLocation.matches) return stopOnDescriptionLoss(beforeLocation.operation, beforeLocation);
    const locationTrace = async (operation) => captureDescription(operation);
    locationTrace.trace = () => facebookDescriptionMutationTrace.slice(-24);
    const locationResult = await ppVerifyFacebookLocationAtReview(payload.location, locationTrace);
    if (locationResult.diagnostic?.error_code === "FACEBOOK_DESCRIPTION_NOT_PERSISTED"
      || locationResult.diagnostic?.error_code === "FACEBOOK_DESCRIPTION_LOCATION_CROSSFIELD_CORRUPTION") {
      return { ...ppContentScriptIdentity(), ok: false, capability_ready: false,
        stage: "FACEBOOK_DESCRIPTION_MUTATION_TRACE", error_code: locationResult.diagnostic.error_code,
        first_bad_operation: locationResult.diagnostic.first_bad_operation || null,
        description_mutation_trace: locationResult.diagnostic.description_mutation_trace || facebookDescriptionMutationTrace.slice(-24),
        location_diagnostic: locationResult.diagnostic, submission_performed: false,
        missing_required_fields: ["description"] };
    }
    locationResult.diagnostic = locationResult.diagnostic || {};
    locationResult.diagnostic.description_mutation_trace = facebookDescriptionMutationTrace.slice(-24);
    locationResult.diagnostic.location_mutation_performed_while_details = false;
    const locationFilled = locationResult.filled === true;
    const locationStageReached = ppFacebookLocationStageState(locationResult) !== "NOT_MOUNTED_YET";
    const locationStageDeferred = !locationFilled && !locationStageReached
      && locationResult.diagnostic?.stage === "LOCATION_NOT_VERIFIED_SAFE_FLOW_INCOMPLETE";
    if (locationFilled) populated_fields.push("location");
    else if (!locationStageDeferred) missing_required_fields.push("location");
    record(
      "location", !locationStageDeferred, locationResult.detected === true, !locationStageDeferred, locationFilled,
      locationResult.verified_value || payload.location || null,
      locationFilled || locationStageDeferred ? null : (locationResult.diagnostic?.stage || "LOCATION_NOT_VERIFIED"),
      null, adapter.location, ["location"], locationResult.diagnostic || null,
    );
    await ppReportFacebookFormProgress("LOCATION_SET", locationStageDeferred ? "checkpoint" : "completed", { field: "location", error_code: locationFilled || locationStageDeferred ? null : (locationResult.diagnostic?.stage || "FACEBOOK_LOCATION_NOT_VERIFIED") });
  }

  const baseFieldsDetected = coreFormDetectedAtStart;
  const page_state = baseFieldsDetected ? "FORM_AVAILABLE" : "FORM_CHANGED";
  const capability_ready = baseFieldsDetected && missing_required_fields.length === 0 && photoOk;
  if (market === "facebook") {
    await ppReportFacebookFormProgress(capability_ready ? "REQUIRED_FIELDS_VERIFIED" : "FACEBOOK_FORM_BLOCKED", "completed", { required_fields_remaining: missing_required_fields.length, error_code: capability_ready ? null : "REQUIRED_FIELDS_UNRESOLVED" });
    if (capability_ready) await ppReportFacebookFormProgress("READY_TO_SUBMIT", "completed", { required_fields_remaining: 0 });
  }
  const facebookFormReadiness = market === "facebook" ? {
    after_initial_fill: ppFacebookFormReadinessSnapshot(payload, "A_AFTER_INITIAL_FILL_BEFORE_SHIPPING"),
    field_transition_timeline: facebookReadinessTimeline.slice(0, 24),
  } : null;
  return {
    ...ppContentScriptIdentity(),
    ok: diagnostic ? page_state === "FORM_AVAILABLE" : page_state === "FORM_AVAILABLE",
    capability_ready,
    stage: diagnostic ? "COMPLETED" : market === "facebook" && missing_required_fields.length === 0 ? "READY_TO_SUBMIT" : market === "facebook" ? "BLOCKED_BEFORE_SUBMIT" : "AWAITING_OPERATOR_REVIEW",
    marketplace: market,
    action: diagnostic ? "DIAGNOSTIC" : payload.action || "CREATE",
    page_state,
    login_state,
    page_url: location.origin + location.pathname,
    selector_strategy: "central_marketplace_map_then_semantic_labels",
    populated_fields,
    field_results,
    missing_required_fields: [...new Set(missing_required_fields)],
    uploaded_image_count,
    ...(facebookCreateBaseline ? { clean_create_form: facebookCreateBaseline } : {}),
    ...(photoUploadEvidence ? { photo_upload: photoUploadEvidence } : {}),
    ...(market === "facebook" ? { location_stage_state: field_results.find((item) => item.field === "location")?.required === false ? "NOT_MOUNTED_YET" : (field_results.find((item) => item.field === "location")?.filled ? "VERIFIED" : "UNRESOLVED_AT_EXPECTED_STAGE") } : {}),
    ...(facebookFormReadiness ? { facebook_form_readiness: facebookFormReadiness } : {}),
    ...(market === "facebook" ? { description_mutation_trace: facebookDescriptionMutationTrace.slice(-24) } : {}),
    ...(facebookOfferState ? { offers_state: facebookOfferState } : {}),
    operator_review_required: !diagnostic && market !== "facebook",
    submission_performed: false,
    error_code: page_state === "FORM_AVAILABLE" ? (missing_required_fields.length ? "REQUIRED_FIELDS_UNRESOLVED" : null) : "MARKETPLACE_FORM_CHANGED",
  };
}

async function ppInspectFacebookCreateBaseline() {
  if (location.origin !== "https://www.facebook.com" || location.pathname !== "/marketplace/create/item") {
    return { ...ppContentScriptIdentity(), ok: false, clean: false, error_code: "FACEBOOK_CREATE_BASELINE_WRONG_PAGE", page_url: ppSafePageUrl() };
  }
  const adapter = globalThis.POSTERPRO_MARKETPLACE_ADAPTERS?.facebook;
  const readiness = adapter ? await ppWaitForFacebookCreateFormReady(adapter, { timeoutMs: 15000, pollMs: 200 }) : { ready: false, wait_ms: 0, anchors: {} };
  if (!readiness.ready) return {
    ...ppContentScriptIdentity(), ok: false, clean: false, error_code: "FACEBOOK_FORM_READY_TIMEOUT",
    form_ready_wait_ms: readiness.wait_ms, form_anchors: readiness.anchors, page_url: ppSafePageUrl(),
  };
  const evidence = ppFacebookCreateBaselineEvidence();
  return { ...ppContentScriptIdentity(), ...evidence, ok: evidence.clean, clean: evidence.clean,
    error_code: evidence.clean ? null : "FACEBOOK_DIRTY_CREATE_FORM", form_ready_wait_ms: readiness.wait_ms, page_url: ppSafePageUrl() };
}

function ppSafePageUrl() {
  return `${location.origin}${location.pathname}`;
}

function ppIdentityMatches(expectedUrl) {
  try {
    const expected = new URL(expectedUrl);
    return expected.origin === location.origin && expected.pathname.replace(/\/$/, "") === location.pathname.replace(/\/$/, "");
  } catch {
    return false;
  }
}

function ppFindListingAction(patterns) {
  const controls = Array.from(document.querySelectorAll('button,[role="button"],a,input[type="button"],input[type="submit"]'))
    .filter((node) => !node.disabled && node.getAttribute("aria-hidden") !== "true");
  return controls.find((node) => {
    const label = `${node.innerText || node.value || ""} ${node.getAttribute("aria-label") || ""} ${node.getAttribute("title") || ""}`.trim().toLowerCase();
    return patterns.some((pattern) => label === pattern || label.includes(pattern));
  }) || null;
}

function ppMarketplaceListingIdentity(marketplace) {
  const host = location.hostname.toLowerCase();
  const expectedHost = marketplace === "mercari" ? /(^|\.)mercari\.com$/ : /(^|\.)facebook\.com$/;
  if (!expectedHost.test(host)) return null;
  const links = Array.from(document.querySelectorAll('a[href]'));
  const itemLink = links.find((link) => {
    try {
      const url = new URL(link.href, location.href);
      return url.hostname.toLowerCase().endsWith(marketplace === "mercari" ? "mercari.com" : "facebook.com")
        && (marketplace === "mercari" ? /\/item\//i.test(url.pathname) : /\/marketplace\/item\//i.test(url.pathname));
    } catch { return false; }
  });
  if (itemLink) {
    const url = new URL(itemLink.href, location.href);
    const id = url.pathname.match(/(?:item|marketplace\/item)\/([^/?#]+)/i)?.[1] || null;
    return { external_listing_id: id, external_url: url.origin + url.pathname };
  }
  const match = location.pathname.match(marketplace === "mercari" ? /\/item\/([^/?#]+)/i : /\/marketplace\/item\/([^/?#]+)/i);
  return match ? { external_listing_id: match[1], external_url: location.origin + location.pathname } : null;
}

// A post-submit URL is identity evidence only when the rendered item itself
// matches this CREATE's title and price. Never accept the first Marketplace
// link on a reused SPA/document as the result of the current submission.
function ppFacebookSubmittedListingIdentity(payload = {}, preSubmitItemIds = new Set()) {
  const expectedTitle = String(payload.title || "").trim();
  const expectedPrice = Number(String(payload.price ?? "").replace(/[^0-9.]/g, ""));
  if (!expectedTitle || !Number.isFinite(expectedPrice) || expectedPrice <= 0) return null;
  const verify = (root) => {
    const body = String(root?.innerText || root?.textContent || "").replace(/\s+/g, " ").trim();
    const observedTitle = ppFacebookObservedTitle(root, expectedTitle);
    const observedPrice = ppFacebookObservedPrice(body, expectedPrice);
    return observedTitle && observedPrice ? { observed_title: observedTitle, observed_price: observedPrice } : null;
  };
  const direct = location.pathname.match(/^\/marketplace\/item\/([^/?#]+)/i);
  if (direct) {
    const observed = verify(document.querySelector("main,[role='main']") || document.body);
    if (!observed) return null;
    return {
      external_listing_id: direct[1],
      external_url: `https://www.facebook.com/marketplace/item/${encodeURIComponent(direct[1])}/`,
      identity_evidence: "DIRECT_ITEM_PAGE_EXACT_TITLE_PRICE",
      ...observed,
    };
  }

  const matches = [];
  for (const anchor of Array.from(document.querySelectorAll("a[href]"))) {
    let url;
    try { url = new URL(anchor.href, location.href); } catch (_) { continue; }
    if (url.hostname.toLowerCase() !== "www.facebook.com") continue;
    const id = url.pathname.match(/^\/marketplace\/item\/([^/?#]+)/i)?.[1];
    if (!id || preSubmitItemIds.has(id)) continue;
    let card = anchor;
    for (let depth = 0; depth < 5 && card.parentElement; depth += 1) {
      card = card.parentElement;
      if (card.getAttribute?.("role") === "article" || card.getAttribute?.("data-testid")?.toLowerCase().includes("listing")) break;
    }
    const observed = verify(card);
    if (!observed) continue;
    matches.push({
      external_listing_id: id,
      external_url: `https://www.facebook.com/marketplace/item/${encodeURIComponent(id)}/`,
      identity_evidence: "NEW_POST_SUBMIT_ITEM_LINK_EXACT_TITLE_PRICE",
      ...observed,
    });
  }
  return matches.length === 1 ? matches[0] : null;
}

function ppFacebookNormalizeListingMatch(value) {
  return String(value || "").normalize("NFKC").replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

function ppFacebookObservedTitle(container, expectedTitle) {
  const expected = ppFacebookNormalizeListingMatch(expectedTitle);
  const nodes = [container, ...Array.from(container?.querySelectorAll?.("h1,h2,h3,[role='heading'],span,div") || [])];
  for (const node of nodes) {
    const raw = String(node?.innerText || node?.textContent || "").replace(/\s+/g, " ").trim();
    if (raw && ppFacebookNormalizeListingMatch(raw) === expected) return raw.slice(0, 240);
  }
  return null;
}

function ppFacebookObservedPrice(text, expectedPrice) {
  const expected = Number(expectedPrice).toFixed(2).replace(".", "\\.");
  const raw = String(text || "");
  const match = raw.match(new RegExp(`(?:\\$|USD\\s*)(${expected})(?!\\d)`, "i"));
  if (match) return match[1];
  const expectedNumber = Number(expectedPrice);
  const rounded = Math.round(expectedNumber);
  if (!Number.isInteger(rounded) || !ppFacebookPriceEquivalent(expectedNumber, rounded)) return null;
  const normalized = raw.match(new RegExp(`(?:\\$|USD\\s*)(${rounded})(?![\\d.]\\d)`, "i"));
  return normalized?.[1] || null;
}

async function ppFacebookReconcileSellerListing(payload = {}) {
  const host = String(location.hostname || "").toLowerCase();
  const path = String(location.pathname || "");
  const title = String(payload.expected_title || "").trim();
  const expectedPrice = Number(payload.expected_price);
  if (!/(^|\.)facebook\.com$/.test(host) || !/^\/marketplace\/(?:you\/selling|item\/)/i.test(path)) {
    return { state: "AMBIGUOUS", error_code: "FACEBOOK_RECONCILIATION_PAGE_UNVERIFIED", page_url: ppSafePageUrl() };
  }
  if (!title || !Number.isFinite(expectedPrice) || expectedPrice <= 0) {
    return { state: "AMBIGUOUS", error_code: "FACEBOOK_RECONCILIATION_MATCH_KEYS_MISSING", page_url: ppSafePageUrl() };
  }
  const expectedTitle = ppFacebookNormalizeListingMatch(title);
  const expectedPriceText = expectedPrice.toFixed(2);
  const expectedDescription = ppNormalizeFacebookDescription(payload.expected_description || "");
  const remoteDescriptionEvidence = (root) => {
    if (!expectedDescription) return { status: "UNAVAILABLE", matches: false, expected_length: 0, observed_length: 0, expected_hash: null, observed_hash: null };
    const rendered = ppNormalizeFacebookDescription(root?.innerText || root?.textContent || "");
    const index = rendered.indexOf(expectedDescription);
    const matchedText = index >= 0 ? rendered.slice(index, index + expectedDescription.length) : "";
    return {
      status: index >= 0 ? "VERIFIED" : "NOT_VERIFIED",
      matches: index >= 0 && matchedText === expectedDescription,
      expected_length: expectedDescription.length,
      observed_length: matchedText.length,
      expected_hash: ppFacebookDescriptionHash(expectedDescription),
      observed_hash: matchedText ? ppFacebookDescriptionHash(matchedText) : null,
      evidence_source: "authenticated_listing_render",
    };
  };
  const bodyText = String(document.body?.innerText || document.body?.textContent || "").replace(/\s+/g, " ").trim();
  const direct = path.match(/^\/marketplace\/item\/([^/?#]+)/i);
  if (direct) {
    const visible = ppFacebookNormalizeListingMatch(bodyText);
    const observedPrice = ppFacebookObservedPrice(bodyText, expectedPrice);
    const priceMatch = Boolean(observedPrice);
    const observedTitle = ppFacebookObservedTitle(document.body, title);
    const titleMatch = Boolean(observedTitle);
    if (titleMatch && priceMatch) return {
      state: "FOUND", source: "authenticated_navigation_result", exact_title_match: true,
      exact_price_match: true, external_listing_id: direct[1],
      external_url: `https://www.facebook.com/marketplace/item/${encodeURIComponent(direct[1])}/`,
      observed_title: observedTitle, observed_price: observedPrice,
      price_match_mode: Number(observedPrice) === expectedPrice ? "EXACT" : "FACEBOOK_WHOLE_DOLLAR_WITHIN_ONE_CENT",
      description_verification: remoteDescriptionEvidence(document.querySelector("main,[role='main']") || document.body),
      page_url: ppSafePageUrl(),
    };
    return { state: "AMBIGUOUS", source: "authenticated_navigation_result", exact_title_match: titleMatch, exact_price_match: priceMatch, page_url: ppSafePageUrl() };
  }

  // Search only the signed-in seller's Marketplace listings. If this Facebook
  // build exposes no account-list search, an absent card is not proof of
  // absence (the page may be virtualized or paginated), so fail closed.
  const search = Array.from(document.querySelectorAll('input[type="search"],input[placeholder],[role="searchbox"]'))
    .filter((node) => ppVisible(node))
    .find((node) => /listing|marketplace|your items|selling/i.test(`${node.getAttribute?.("aria-label") || ""} ${node.getAttribute?.("placeholder") || ""} ${node.closest?.('[role="search"]')?.innerText || ""}`));
  let searchApplied = false;
  if (search) {
    const setter = Object.getOwnPropertyDescriptor(globalThis.HTMLInputElement?.prototype || {}, "value")?.set;
    if (setter) setter.call(search, title); else search.value = title;
    search.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    search.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    searchApplied = ppFacebookNormalizeListingMatch(search.value) === expectedTitle;
    if (searchApplied) {
      const deadline = Date.now() + 2500;
      let previous = "";
      let stable = 0;
      while (Date.now() < deadline && stable < 2) {
        await ppWait(200);
        const current = String(document.body?.innerText || document.body?.textContent || "").replace(/\s+/g, " ").trim();
        stable = current === previous ? stable + 1 : 0;
        previous = current;
      }
    }
  }

  const anchors = Array.from(document.querySelectorAll('a[href]'));
  const candidates = [];
  for (const anchor of anchors) {
    let url;
    try { url = new URL(anchor.href, location.href); } catch (_) { continue; }
    if (url.hostname.toLowerCase() !== "www.facebook.com" || !/^\/marketplace\/item\/([^/?#]+)/i.test(url.pathname)) continue;
    let card = anchor;
    for (let depth = 0; depth < 5 && card.parentElement; depth += 1) {
      card = card.parentElement;
      if (card.getAttribute?.("role") === "article" || card.getAttribute?.("data-testid")?.toLowerCase().includes("listing")) break;
    }
    const cardText = String(card.innerText || card.textContent || anchor.innerText || anchor.textContent || "").replace(/\s+/g, " ").trim().slice(0, 700);
    const normalizedCard = ppFacebookNormalizeListingMatch(cardText);
    const observedTitle = ppFacebookObservedTitle(card, title);
    const exactTitle = Boolean(observedTitle);
    const observedPrice = ppFacebookObservedPrice(cardText, expectedPrice);
    const exactPrice = Boolean(observedPrice);
    const id = url.pathname.match(/^\/marketplace\/item\/([^/?#]+)/i)?.[1] || null;
    if (id) candidates.push({ external_listing_id: id, external_url: `https://www.facebook.com/marketplace/item/${encodeURIComponent(id)}/`, exact_title_match: exactTitle, exact_price_match: exactPrice, observed_title: observedTitle, observed_price: observedPrice, price_match_mode: observedPrice && Number(observedPrice) !== expectedPrice ? "FACEBOOK_WHOLE_DOLLAR_WITHIN_ONE_CENT" : observedPrice ? "EXACT" : null, observed_text: ppSafeEvidenceText(cardText, 500) });
  }
  const exact = candidates.filter((item) => item.exact_title_match && item.exact_price_match);
  if (exact.length === 1) return {
    state: "FOUND", source: "authenticated_facebook_seller_listings", exact_title_match: true,
    exact_price_match: true, external_listing_id: exact[0].external_listing_id,
    external_url: exact[0].external_url, observed_title: title.slice(0, 240),
    observed_title: exact[0].observed_title, observed_price: exact[0].observed_price, price_match_mode: exact[0].price_match_mode,
    candidate_count: candidates.length,
    description_verification: { status: "UNAVAILABLE", matches: false, expected_length: expectedDescription.length, observed_length: 0, expected_hash: expectedDescription ? ppFacebookDescriptionHash(expectedDescription) : null, observed_hash: null, evidence_source: "seller_card_does_not_expose_description" },
    page_url: ppSafePageUrl(),
    evidence: exact[0],
  };
  if (exact.length > 1) return { state: "AMBIGUOUS", source: "authenticated_facebook_seller_listings", reason: "MULTIPLE_EXACT_TITLE_PRICE_MATCHES", candidate_count: exact.length, candidates: exact.slice(0, 4), page_url: ppSafePageUrl() };

  const explicitEmpty = /(?:no listings found|no results found|you have no listings matching)/i.test(bodyText);
  const searchComplete = Boolean(searchApplied && explicitEmpty && !/loading|searching|please wait/i.test(bodyText));
  if (searchComplete && payload.known_remote_presence !== true) return {
    state: "NOT_FOUND", source: "authenticated_facebook_seller_listings", exact_search_used: true,
    exact_title_search: true, account_search_complete: true, result_count: 0,
    empty_state_verified: true, searched_title: title.slice(0, 240), observed_title: title.slice(0, 240),
    observed_price: expectedPriceText, exact_title_match: true, exact_price_match: true,
    authenticated_account_context: true,
    page_url: ppSafePageUrl(),
  };
  return {
    state: "AMBIGUOUS", source: "authenticated_facebook_seller_listings",
    reason: payload.known_remote_presence === true
      ? "OPERATOR_CONFIRMED_REMOTE_PRESENCE_NOT_LOCATED"
      : "NO_EXACT_MATCH_BUT_AUTHENTICATED_SEARCH_NOT_PROVEN_COMPLETE",
    exact_search_used: searchApplied, candidate_count: candidates.length,
    candidates: candidates.filter((item) => item.exact_title_match || item.exact_price_match).slice(0, 4),
    page_url: ppSafePageUrl(),
  };
}

function ppFacebookExactContinueControl() {
  return Array.from(document.querySelectorAll('button,[role="button"],input[type="button"]'))
    // Disabled Next is precisely the state this diagnostic must observe. The
    // generic ppVisible helper excludes disabled nodes, so test renderability
    // here and let the caller's explicit enabled check preserve the gate.
    .filter((node) => node && node.getAttribute?.("aria-hidden") !== "true"
      && node.getClientRects?.().length > 0
      && globalThis.getComputedStyle?.(node)?.visibility !== "hidden"
      && globalThis.getComputedStyle?.(node)?.display !== "none")
    .find((node) => ["next", "continue"].includes(String(node.innerText || node.value || node.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim().toLowerCase())) || null;
}

function ppFacebookReadinessScope() {
  const candidates = Array.from(document.querySelectorAll('form,[role="main"],main,[role="dialog"]')).filter(ppVisible);
  const scored = candidates.map((node) => {
    const text = String(node.innerText || node.textContent || "").toLowerCase();
    const controls = node.querySelectorAll?.('input,textarea,select,[role="textbox"],[role="combobox"],[role="radio"],[role="checkbox"],[contenteditable="true"]') || [];
    const controlText = Array.from(controls).map((control) => `${control.getAttribute?.("aria-label") || ""} ${control.getAttribute?.("placeholder") || ""} ${control.name || ""}`).join(" ").toLowerCase();
    const score = (/(create|listing|delivery method|shipping label|sell an item)/.test(text) ? 4 : 0)
      + (/(title|price|category|condition|description|location)/.test(controlText) ? 3 : 0)
      + Math.min(3, controls.length / 4);
    return { node, score, control_count: controls.length };
  }).filter((row) => row.score > 0);
  scored.sort((a, b) => b.score - a.score || b.control_count - a.control_count);
  return scored[0]?.node || document;
}

function ppFacebookControlLabel(node) {
  const direct = node.labels ? Array.from(node.labels).map((label) => label.innerText || label.textContent || "").join(" ") : "";
  const labelledBy = String(node.getAttribute?.("aria-labelledby") || "").split(/\s+/).filter(Boolean)
    .map((id) => document.getElementById?.(id)?.innerText || document.getElementById?.(id)?.textContent || "").join(" ");
  return ppSafeEvidenceText(direct || labelledBy || node.getAttribute?.("aria-label") || node.getAttribute?.("name")
    || node.getAttribute?.("placeholder") || node.getAttribute?.("title") || node.parentElement?.innerText || node.innerText || node.textContent, 140);
}

function ppFacebookRendered(node) {
  if (!node || node.getAttribute?.("aria-hidden") === "true" || !node.getClientRects?.().length) return false;
  const style = globalThis.getComputedStyle?.(node);
  return style?.visibility !== "hidden" && style?.display !== "none";
}

function ppFacebookAssociatedMessages(node, scope) {
  const ids = String(node?.getAttribute?.("aria-describedby") || "").split(/\s+/).filter(Boolean);
  const described = ids.map((id) => document.getElementById?.(id)).filter((item) => item && (!scope?.contains || scope.contains(item)))
    .map((item) => ppSafeEvidenceText(item.innerText || item.textContent, 160)).filter(Boolean);
  const nearby = node?.parentElement;
  const nearbyText = nearby ? ppSafeEvidenceText(nearby.innerText || nearby.textContent, 220) : "";
  const messages = [...new Set([...described, ...(nearbyText && /required|invalid|error|choose|select|add|upload|processing|pending|complete/i.test(nearbyText) ? [nearbyText] : [])])].slice(0, 4);
  return messages;
}

function ppFacebookExpectedFieldValues(payload = {}) {
  return {
    title: payload.title ?? null,
    price: payload.price ?? null,
    category: payload.marketplace_category || payload.category || payload.category_hint || null,
    condition: payload.condition ?? null,
    description: payload.description ?? null,
    location: payload.location ?? null,
    availability: payload.availability ?? null,
    shipping: payload.delivery_method || payload.shipping?.mode || null,
    package_weight: payload.shipping?.package_weight ?? payload.weight ?? null,
    shipping_carrier: payload.shipping?.domestic_service ?? null,
    shipping_payer: payload.shipping?.free_shipping === true ? "seller pays / free shipping" : payload.shipping?.buyer_pays_shipping === true ? "buyer pays" : null,
  };
}

function ppFacebookReadinessFieldMatches(label, field) {
  const text = String(label || "").toLowerCase();
  const patterns = {
    title: /title|item name/, price: /price|amount/, category: /category/, condition: /condition/,
    description: /description|describe/, location: /location|city|town|postal|zip/,
    availability: /availability|quantity|stock/, shipping: /delivery method|shipping option|shipping method/,
    package_weight: /package weight|weight/, shipping_carrier: /shipping carrier|carrier|service/,
    shipping_payer: /shipping cost|free shipping|buyer pays|seller pays/,
  };
  return Boolean(patterns[field]?.test(text));
}

function ppFacebookReadinessFieldEvidence(node, field, requested, scope) {
  const tag = String(node.tagName || "").toLowerCase();
  const role = node.getAttribute?.("role") || null;
  const label = ppFacebookControlLabel(node);
  const type = node.getAttribute?.("type") || node.type || null;
  const selectedOption = tag === "select" ? Array.from(node.selectedOptions || []).map((option) => ppSafeEvidenceText(option.textContent || option.label, 100)).join("; ") : "";
  const value = selectedOption || (typeof node.value === "string" && node.value ? ppSafeEvidenceText(node.value, 300)
    : ppSafeEvidenceText(node.innerText || node.textContent || node.getAttribute?.("aria-valuetext") || node.getAttribute?.("aria-label"), 300));
  const placeholder = ppSafeEvidenceText(node.getAttribute?.("placeholder") || "", 100);
  const checked = typeof node.checked === "boolean" ? node.checked : node.getAttribute?.("aria-checked") === "true" ? true : node.getAttribute?.("aria-checked") === "false" ? false : null;
  const selected = node.getAttribute?.("aria-selected") === "true" || node.getAttribute?.("data-state") === "selected";
  const invalid = node.getAttribute?.("aria-invalid") === "true";
  const required = Boolean(node.required || node.getAttribute?.("aria-required") === "true");
  const visible = ppFacebookRendered(node);
  const messages = ppFacebookAssociatedMessages(node, scope);
  const fieldSignals = [label, placeholder, node.getAttribute?.("aria-label") || "", node.getAttribute?.("name") || ""];
  const valueLooksPlaceholder = /^(select|choose|add|pick|enter|choose an?)/i.test(value || placeholder);
  const valueMatchesRequest = requested != null && value && String(value).trim().toLowerCase().includes(String(requested).trim().toLowerCase());
  const positiveSelection = checked === true || selected || (tag === "select" && Boolean(node.value)) || node.getAttribute?.("aria-checked") === "true";
  let acceptedState = "not_observable";
  if (invalid || valueLooksPlaceholder || messages.some((message) => /required|invalid|error|must be|choose|select|add|enter/i.test(message))) acceptedState = "rejected_or_incomplete_observable";
  else if (valueMatchesRequest && positiveSelection) acceptedState = "committed_selection_observable";
  else if (valueMatchesRequest && !required) acceptedState = "value_present_no_rejection_observed";
  else if (valueMatchesRequest && required) acceptedState = "value_present_acceptance_unproven";
  return {
    field, requested_value: requested == null ? null : ppSafeEvidenceText(requested, 240),
    label, tag, role, type, value: value || null, parsed_numeric_value: /price|weight/.test(field) && value ? (Number(String(value).replace(/[^\d.]/g, "")) || null) : null,
    placeholder: placeholder || null, placeholder_state: valueLooksPlaceholder,
    checked, selected, required, aria_required: node.getAttribute?.("aria-required") || null,
    aria_invalid: node.getAttribute?.("aria-invalid") || null, invalid, disabled: Boolean(node.disabled || node.getAttribute?.("aria-disabled") === "true"),
    visible, connected: Boolean(node.isConnected), validation_or_help: messages,
    requested_value_matches_observed: Boolean(valueMatchesRequest), facebook_accepted_state: acceptedState,
    signals: fieldSignals.filter(Boolean).slice(0, 4).map((text) => ppSafeEvidenceText(text, 120)),
  };
}

function ppFacebookFormReadinessSnapshot(payload = {}, checkpoint = "UNSPECIFIED") {
  const scope = ppFacebookReadinessScope();
  const selectors = 'input,textarea,select,[role="textbox"],[role="combobox"],[role="radio"],[role="checkbox"],[role="switch"],[contenteditable="true"],button,[role="button"]';
  const controls = Array.from(scope.querySelectorAll?.(selectors) || []).filter((node) => ppFacebookRendered(node) || node.required || node.getAttribute?.("aria-required") === "true").slice(0, 120);
  const requested = ppFacebookExpectedFieldValues(payload);
  const fields = Object.keys(requested).map((field) => {
    const matched = controls.filter((node) => ppFacebookReadinessFieldMatches(`${ppFacebookControlLabel(node)} ${node.getAttribute?.("name") || ""} ${node.getAttribute?.("placeholder") || ""}`, field));
    return { field, requested_value: requested[field], controls: matched.slice(0, 8).map((node) => ppFacebookReadinessFieldEvidence(node, field, requested[field], scope)) };
  });
  const validationNodes = Array.from(scope.querySelectorAll?.('[role="alert"],[aria-live], [aria-invalid="true"],[aria-required="true"],.error,.warning') || []).filter(ppFacebookRendered).slice(0, 32);
  const validation = validationNodes.map((node) => ({
    role: node.getAttribute?.("role") || null, aria_live: node.getAttribute?.("aria-live") || null,
    aria_invalid: node.getAttribute?.("aria-invalid") || null,
    text: ppSafeEvidenceText(node.innerText || node.textContent || ppFacebookControlLabel(node), 220),
    tag: String(node.tagName || "").toLowerCase() || null,
  })).filter((row) => row.text);
  const placeholders = controls.filter((node) => /^(select|choose|add|pick|enter|choose an?)/i.test(String(node.value || node.innerText || node.getAttribute?.("placeholder") || "").trim()))
    .slice(0, 24).map((node) => ({ label: ppFacebookControlLabel(node), value: ppSafeEvidenceText(node.value || node.innerText || node.getAttribute?.("placeholder"), 100), role: node.getAttribute?.("role") || null }));
  const mediaControls = controls.filter((node) => /photo|image|upload|media/i.test(`${ppFacebookControlLabel(node)} ${node.getAttribute?.("aria-label") || ""}`));
  const imageNodes = Array.from(scope.querySelectorAll?.("img,[role='img']") || []).filter(ppVisible);
  const mediaText = ppSafeEvidenceText(Array.from(scope.querySelectorAll?.('[role="status"],[role="alert"],[aria-live], [data-testid*="upload" i],[data-testid*="media" i]') || [])
    .filter(ppVisible).map((node) => String(node.innerText || node.textContent || "").replace(/\s+/g, " ").trim())
    .filter((text) => /upload|processing|pending|failed|error|complete|success/i.test(text)).slice(0, 12).join(" | "), 1000);
  const nextNode = ppFacebookExactContinueControl();
  const vv = globalThis.visualViewport || null;
  const rootText = ppSafeEvidenceText(scope.innerText || scope.textContent || "", 1800);
  const shippingControls = fields.filter((row) => ["shipping", "package_weight", "shipping_carrier", "shipping_payer"].includes(row.field));
  const photoButtons = Array.from(scope.querySelectorAll?.('button,[role="button"]') || []).filter(ppVisible)
    .filter((node) => /view photo|remove photo|edit photo|delete photo/i.test(ppFacebookControlLabel(node)));
  const photoAccepted = imageNodes.length > 0 || photoButtons.some((node) => /view photo/i.test(ppFacebookControlLabel(node)));
  const knownFieldNodes = new Set(fields.flatMap((row) => row.controls.map((control) => control.tag && control.label ? `${control.tag}|${control.label}` : "")));
  const unknownRequiredControls = controls.filter((node) => node.required || node.getAttribute?.("aria-required") === "true" || node.getAttribute?.("aria-invalid") === "true")
    .filter((node) => !knownFieldNodes.has(`${String(node.tagName || "").toLowerCase()}|${ppFacebookControlLabel(node)}`))
    .slice(0, 24).map((node) => ppFacebookReadinessFieldEvidence(node, "unknown_required_control", null, scope));
  return {
    checkpoint, captured_at: new Date().toISOString(), captured_at_ms: Date.now(),
    scope: { tag: String(scope.tagName || "document").toLowerCase(), role: scope.getAttribute?.("role") || null, heading_context: rootText.slice(0, 300) },
    viewport: { inner_width: Number(globalThis.innerWidth) || null, inner_height: Number(globalThis.innerHeight) || null,
      visual_width: Number(vv?.width) || null, visual_height: Number(vv?.height) || null, visual_scale: Number(vv?.scale) || null,
      dpr: Number(globalThis.devicePixelRatio) || null },
    controls_enumerated: controls.length,
    visible_controls: controls.slice(0, 120).map((node) => ppFacebookReadinessFieldEvidence(node, "observable_control", null, scope)),
    unknown_required_controls: unknownRequiredControls, fields,
    description_persistence: ppFacebookDescriptionEvidence(payload.description, checkpoint, payload),
    photos: { media_tile_count: imageNodes.length, media_action_controls: mediaControls.length,
      view_remove_edit_controls: photoButtons.length, facebook_considers_uploads_complete: photoAccepted ? "visible_media_or_view_controls_present" : "not_observable",
      processing_or_upload_evidence: mediaText, visible_media_labels: imageNodes.slice(0, 16).map((node) => ppSafeEvidenceText(node.getAttribute?.("alt") || node.getAttribute?.("aria-label") || "media tile", 80)),
      errors: validation.filter((row) => /photo|image|upload|media/i.test(row.text)) },
    validation, placeholder_controls: placeholders,
    next: ppFacebookNextControlEvidence(nextNode),
    shipping_controls: shippingControls,
  };
}

function ppFacebookDetailsIntegrityEvidence(payload = {}, stage = "BEFORE_NEXT") {
  const titleNode = ppFacebookDetailsControl("title");
  const priceNode = ppFacebookDetailsControl("price");
  const read = (node) => String(node?.value ?? node?.innerText ?? node?.textContent ?? "").replace(/\s+/g, " ").trim();
  const liveTitle = read(titleNode);
  const livePrice = Number(read(priceNode).replace(/[^\d.]/g, ""));
  const requestedPrice = Number(String(payload.price ?? "").replace(/[^\d.]/g, ""));
  const description = payload.description
    ? ppFacebookDescriptionEvidence(payload.description, stage, payload)
    : { matches: true, not_applicable: true, error_code: null, cross_field: { safe: true, equals_location: false, equals_title: false, equals_price: false } };
  const locationResolution = ppFacebookLocationControlResolution();
  const observedLocation = read(locationResolution.node);
  const stageKind = ppFacebookWorkflowStage();
  const observedTitle = titleNode ? liveTitle : ppFacebookDetailsFieldVerification.title?.observed || "";
  const observedPrice = priceNode ? livePrice : Number(ppFacebookDetailsFieldVerification.price?.observed);
  const locationSelected = Boolean(payload.location && ppFacebookLocationSelectionMatches(payload.location));
  const locationState = locationResolution.node
    ? { state: ppFacebookLocationCityMatches(payload.location, observedLocation) || locationSelected ? "MATCH" : "MISMATCH", observed: ppSafeEvidenceText(observedLocation, 120), requested: ppSafeEvidenceText(payload.location, 120) }
    : { state: locationSelected ? "MATCH_SELECTED_STATE" : stageKind === "DETAILS" ? "NOT_MOUNTED_YET" : "NOT_OBSERVABLE", observed: null, requested: ppSafeEvidenceText(payload.location, 120) };
  const titleMatches = !payload.title || (titleNode
    ? Boolean(liveTitle && liveTitle.toLocaleLowerCase() === String(payload.title).replace(/\s+/g, " ").trim().toLocaleLowerCase())
    : stageKind !== "DETAILS" && ppFacebookDetailsFieldVerification.title?.verified === true
      && ppFacebookDetailsFieldVerification.title?.matches === true
      && observedTitle.toLocaleLowerCase() === String(payload.title).replace(/\s+/g, " ").trim().toLocaleLowerCase());
  const priceMatches = payload.price == null || (priceNode
    ? ppFacebookPriceEquivalent(requestedPrice, livePrice)
    : stageKind !== "DETAILS" && ppFacebookDetailsFieldVerification.price?.verified === true
      && ppFacebookDetailsFieldVerification.price?.matches === true && ppFacebookPriceEquivalent(requestedPrice, observedPrice));
  const locationNeededNow = stageKind === "LOCATION" || stageKind === "LOCATION_SHIPPING";
  const locationMatches = !locationNeededNow || locationState.state === "MATCH" || locationState.state === "MATCH_SELECTED_STATE";
  return {
    stage, workflow_stage: stageKind,
    title: { requested: ppSafeEvidenceText(payload.title, 200), observed: ppSafeEvidenceText(observedTitle, 200), matches: titleMatches,
      evidence_source: titleNode ? "live_details_control" : (ppFacebookDetailsFieldVerification.title?.verified ? "durable_details_stage_verification" : "none"),
      details_stage_verification: ppFacebookDetailsFieldVerification.title },
    price: { requested: payload.price ?? null, observed: Number.isFinite(observedPrice) ? observedPrice : null, matches: priceMatches,
      evidence_source: priceNode ? "live_details_control" : (ppFacebookDetailsFieldVerification.price?.verified ? "durable_details_stage_verification" : "none"),
      details_stage_verification: ppFacebookDetailsFieldVerification.price },
    description,
    location: { ...locationState, control_ambiguity: locationResolution.ambiguous, candidates: locationResolution.evidence },
    cross_field_guard: { ok: description.cross_field?.safe === true, error_code: description.error_code || null, ...description.cross_field },
    ready: titleMatches && priceMatches && description.matches && locationMatches,
  };
}

function ppFacebookReadinessPulse(payload, operation, field) {
  const snapshot = ppFacebookFormReadinessSnapshot(payload, `AFTER_${String(operation || "FIELD").toUpperCase()}`);
  const fieldEvidence = snapshot.fields.find((row) => row.field === field) || null;
  return { field, captured_at: snapshot.captured_at, next: snapshot.next, field_state: fieldEvidence,
    validation_count: snapshot.validation.length, placeholder_count: snapshot.placeholder_controls.length };
}

let ppFacebookLastNextRenderIdentity = null;
function ppFacebookNextControlEvidence(node) {
  if (!node) return null;
  if (!ppFacebookNextNodeIds.has(node)) ppFacebookNextNodeIds.set(node, `next-node-${++ppFacebookNextNodeSequence}`);
  const renderIdentity = ppFacebookNextNodeIds.get(node);
  const replacedSincePreviousSample = ppFacebookLastNextRenderIdentity !== null && ppFacebookLastNextRenderIdentity !== renderIdentity;
  ppFacebookLastNextRenderIdentity = renderIdentity;
  const rect = node.getBoundingClientRect?.();
  const parent = node.parentElement || null;
  return {
    render_identity: renderIdentity,
    replaced_since_previous_sample: replacedSincePreviousSample,
    tag: String(node.tagName || "").toLowerCase() || null,
    role: node.getAttribute?.("role") || null,
    type: node.getAttribute?.("type") || node.type || null,
    text: ppSafeEvidenceText(node.innerText || node.value || node.getAttribute?.("aria-label"), 80),
    disabled: Boolean(node.disabled),
    disabled_attribute: node.hasAttribute?.("disabled") || false,
    aria_disabled: node.getAttribute?.("aria-disabled") || null,
    tabindex: node.getAttribute?.("tabindex") || null,
    connected: Boolean(node.isConnected),
    rect: rect ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null,
    parent: parent ? {
      tag: String(parent.tagName || "").toLowerCase() || null,
      role: parent.getAttribute?.("role") || null,
      aria_label: ppSafeEvidenceText(parent.getAttribute?.("aria-label"), 80),
      text: ppSafeEvidenceText(parent.innerText || parent.textContent, 160),
    } : null,
    described_by: ppSafeEvidenceText(node.getAttribute?.("aria-describedby"), 120) || null,
    aria_invalid: node.getAttribute?.("aria-invalid") || null,
    enabled_by_posterpro: !node.disabled && node.getAttribute?.("aria-disabled") !== "true",
  };
}

function ppFacebookPostShippingReadiness(dialogOpen, next) {
  if (dialogOpen) return { ready: false, error_code: "FACEBOOK_SHIPPING_CONFIGURATION_NOT_COMMITTED", shipping_configuration_complete: false };
  if (!next || next.disabled || next.getAttribute?.("aria-disabled") === "true") {
    return { ready: false, error_code: "FACEBOOK_FORM_NOT_READY_FOR_NEXT", shipping_configuration_complete: true };
  }
  return { ready: true, error_code: null, shipping_configuration_complete: true };
}

function ppFacebookShippingOptionCandidates(configuredService) {
  const normalized = String(configuredService || "").toLowerCase().replace(/[_-]+/g, " ").trim();
  const target = /usps.*ground.*advantage/.test(normalized) ? /usps\s+ground\s+advantage/i
    : /usps/.test(normalized) ? /usps/i
      : /ups/.test(normalized) ? /ups/i
        : /fedex/.test(normalized) ? /fed\s*ex/i : null;
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"]')).filter(ppVisible);
  const root = dialogs[dialogs.length - 1] || document;
  const candidates = Array.from(root.querySelectorAll('button,[role="button"],[role="option"],[role="radio"],[role="menuitem"],label,[data-testid]'))
    .filter(ppVisible)
    .map((node) => ({ node, label: ppSafeEvidenceText(node.innerText || node.textContent || node.getAttribute?.("aria-label"), 140) }))
    .filter((row) => row.label);
  const loading = dialogs.some((dialog) => /\bloading\b/i.test(String(dialog.innerText || dialog.textContent || "")));
  return { target, candidates, loading, menu_present: dialogs.length > 0 };
}

function ppFacebookShippingDialogEvidence() {
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"]')).filter(ppVisible);
  const dialog = [...dialogs].reverse().find((item) => /change shipping method/i.test(String(item.innerText || item.textContent || "")));
  if (!dialog) return null;
  const labelOf = (node) => String(node.innerText || node.textContent || node.value || node.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim().slice(0, 140);
  const controls = Array.from(dialog.querySelectorAll('input,select,textarea,[role="combobox"],[role="textbox"],[role="radio"],[role="checkbox"],button,[role="button"]'))
    .filter(ppVisible).slice(0, 20).map((node) => ({
      tag: String(node.tagName || "").toLowerCase(), role: node.getAttribute?.("role") || null,
      label: ppSafeEvidenceText(labelOf(node), 140), value: ppSafeEvidenceText(node.value || node.getAttribute?.("aria-valuetext") || "", 100),
      required: Boolean(node.required || node.getAttribute?.("aria-required") === "true"),
      invalid: Boolean(node.getAttribute?.("aria-invalid") === "true"),
      disabled: Boolean(node.disabled || node.getAttribute?.("aria-disabled") === "true"),
    }));
  return { dialog, controls, evidence: { heading: "Change shipping method", controls } };
}

const ppFacebookDialogGenerationIds = new WeakMap();
let ppFacebookDialogGenerationSequence = 0;

function ppFacebookDialogGeneration(dialog) {
  if (!dialog || (typeof dialog !== "object" && typeof dialog !== "function")) return null;
  if (!ppFacebookDialogGenerationIds.has(dialog)) ppFacebookDialogGenerationIds.set(dialog, ++ppFacebookDialogGenerationSequence);
  return ppFacebookDialogGenerationIds.get(dialog);
}

function ppFacebookUpdateLabel(node) {
  return String(node?.innerText || node?.textContent || node?.value || node?.getAttribute?.("aria-label") || "")
    .replace(/\s+/g, " ").trim();
}

function ppFacebookUpdateCandidates(dialog) {
  return Array.from(dialog?.querySelectorAll?.('button,[role="button"],input[type="button"],input[type="submit"]') || [])
    .filter(ppVisible)
    .filter((node) => /^update$/i.test(ppFacebookUpdateLabel(node)));
}

function ppFacebookResolveUpdateTarget(dialog) {
  const candidates = ppFacebookUpdateCandidates(dialog);
  const visibleExactLabelNodes = Array.from(dialog?.querySelectorAll?.("*") || [])
    .filter((node) => ppVisible(node) && /^update$/i.test(ppFacebookUpdateLabel(node)))
    .slice(0, 24)
    .map((node) => ({
      tag: String(node.tagName || "").toLowerCase() || null,
      role: node.getAttribute?.("role") || null,
      type: node.getAttribute?.("type") || node.type || null,
      actionable_candidate: candidates.includes(node),
      label: ppSafeEvidenceText(ppFacebookUpdateLabel(node), 100),
    }));
  const candidateEvidence = candidates.map((candidate, index) => ({
    index, tag: String(candidate.tagName || "").toLowerCase() || null,
    role: candidate.getAttribute?.("role") || null,
    type: candidate.getAttribute?.("type") || candidate.type || null,
    label: ppSafeEvidenceText(ppFacebookUpdateLabel(candidate), 100),
    disabled: Boolean(candidate.disabled), aria_disabled: candidate.getAttribute?.("aria-disabled") || null,
  }));
  return {
    candidates, candidateEvidence, visibleExactLabelNodes,
    match_count: candidates.length,
    error_code: candidates.length === 1 ? null : "FACEBOOK_UPDATE_TARGET_AMBIGUOUS",
  };
}

function ppFacebookUpdateTargetEvidence(node, currentDialog) {
  if (!node) return null;
  const role = node.getAttribute?.("role") || null;
  const closestButton = node.closest?.("button") || null;
  const closestRoleButton = node.closest?.('[role="button"]') || null;
  const closestDialog = node.closest?.('[role="dialog"]') || null;
  const connected = Boolean(node.isConnected);
  let contained = false;
  try { contained = Boolean(document.contains?.(node)); } catch (_) { contained = false; }
  let rect = null;
  try {
    const value = node.getBoundingClientRect?.();
    if (value) rect = {
      x: Number(value.x ?? value.left) || 0, y: Number(value.y ?? value.top) || 0,
      left: Number(value.left ?? value.x) || 0, top: Number(value.top ?? value.y) || 0,
      right: Number(value.right) || 0, bottom: Number(value.bottom) || 0,
      width: Number(value.width) || 0, height: Number(value.height) || 0,
    };
  } catch (_) { /* geometry is diagnostic-only */ }
  let hit = null;
  if (rect && rect.width > 0 && rect.height > 0) {
    try { hit = document.elementFromPoint?.(rect.left + rect.width / 2, rect.top + rect.height / 2) || null; } catch (_) { hit = null; }
  }
  const hitWithinTarget = Boolean(hit && (hit === node || node.contains?.(hit)));
  const hitContainsTarget = Boolean(hit && hit !== node && hit.contains?.(node));
  const className = typeof node.className === "string" ? node.className : String(node.className?.baseVal || "");
  const describeAncestor = (ancestor) => ancestor ? {
    tag: String(ancestor.tagName || "").toLowerCase() || null,
    role: ancestor.getAttribute?.("role") || null,
  } : null;
  const covering = hit && !hitWithinTarget && !hitContainsTarget ? {
    tag: String(hit.tagName || "").toLowerCase() || null,
    role: hit.getAttribute?.("role") || null,
    label: ppSafeEvidenceText(ppFacebookUpdateLabel(hit) || hit.getAttribute?.("aria-label") || "", 100),
    visible: ppVisible(hit),
  } : null;
  return {
    tag: String(node.tagName || "").toLowerCase() || null,
    role, type: node.getAttribute?.("type") || node.type || null,
    aria_disabled: node.getAttribute?.("aria-disabled") || null,
    disabled: Boolean(node.disabled), tabindex: node.getAttribute?.("tabindex") || null,
    class_name: className.replace(/[^a-zA-Z0-9 _-]/g, "").slice(0, 120),
    text: ppSafeEvidenceText(ppFacebookUpdateLabel(node), 100),
    aria_label: ppSafeEvidenceText(node.getAttribute?.("aria-label") || "", 100),
    is_connected: connected, document_contains: contained,
    closest_button: describeAncestor(closestButton),
    closest_role_button: describeAncestor(closestRoleButton),
    closest_dialog_generation: ppFacebookDialogGeneration(closestDialog),
    closest_dialog_is_current: closestDialog === currentDialog,
    rect, nonzero_rect: Boolean(rect && rect.width > 0 && rect.height > 0),
    center: rect && rect.width > 0 && rect.height > 0 ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : null,
    element_from_point: hit ? { tag: String(hit.tagName || "").toLowerCase() || null, role: hit.getAttribute?.("role") || null } : null,
    hit_is_target_or_descendant: hitWithinTarget,
    hit_is_target_ancestor: hitContainsTarget,
    covering_element: covering,
    viewport_environment: {
      inner_width: Number(globalThis.innerWidth) || null, inner_height: Number(globalThis.innerHeight) || null,
      outer_width: Number(globalThis.outerWidth) || null, outer_height: Number(globalThis.outerHeight) || null,
      device_pixel_ratio: Number(globalThis.devicePixelRatio) || null,
      visual_viewport: globalThis.visualViewport ? { width: Number(globalThis.visualViewport.width) || null, height: Number(globalThis.visualViewport.height) || null, scale: Number(globalThis.visualViewport.scale) || null, offset_left: Number(globalThis.visualViewport.offsetLeft) || 0, offset_top: Number(globalThis.visualViewport.offsetTop) || 0, page_left: Number(globalThis.visualViewport.pageLeft) || 0, page_top: Number(globalThis.visualViewport.pageTop) || 0 } : null,
      scroll_x: Number(globalThis.scrollX) || 0, scroll_y: Number(globalThis.scrollY) || 0,
      document_client_width: Number(document.documentElement?.clientWidth) || null,
      document_client_height: Number(document.documentElement?.clientHeight) || null,
    },
  };
}

function ppFacebookShippingValidationSnapshot(dialog) {
  if (!dialog) return { invalid_fields: [], required_blank_fields: [], disabled_required_controls: [], empty_comboboxes: [], messages: [] };
  const controls = Array.from(dialog.querySelectorAll?.('input,textarea,select,[role="combobox"],[role="textbox"],[aria-invalid="true"],[aria-required="true"],[required]') || [])
    .filter(ppVisible);
  const fieldLabel = (node) => ppSafeEvidenceText(
    node.labels?.[0]?.innerText || node.getAttribute?.("aria-label") || node.getAttribute?.("placeholder")
      || node.getAttribute?.("name") || node.innerText || node.textContent || node.getAttribute?.("role") || "shipping control", 100,
  );
  const valueOf = (node) => String(node.value ?? node.getAttribute?.("aria-valuetext") ?? node.innerText ?? node.textContent ?? "").replace(/\s+/g, " ").trim();
  const unique = (items) => [...new Map(items.map((item) => [JSON.stringify(item), item])).values()].slice(0, 16);
  const invalidFields = [], requiredBlankFields = [], disabledRequiredControls = [], emptyComboboxes = [];
  for (const node of controls) {
    const label = fieldLabel(node);
    const required = Boolean(node.required || node.getAttribute?.("aria-required") === "true");
    const invalid = node.getAttribute?.("aria-invalid") === "true";
    const disabled = Boolean(node.disabled || node.getAttribute?.("aria-disabled") === "true");
    const value = valueOf(node);
    if (invalid) invalidFields.push({ label, tag: String(node.tagName || "").toLowerCase(), role: node.getAttribute?.("role") || null });
    if (required && !value) requiredBlankFields.push({ label, tag: String(node.tagName || "").toLowerCase(), role: node.getAttribute?.("role") || null });
    if (required && disabled) disabledRequiredControls.push({ label, tag: String(node.tagName || "").toLowerCase(), role: node.getAttribute?.("role") || null });
    if (node.getAttribute?.("role") === "combobox" && !value) emptyComboboxes.push({ label, tag: String(node.tagName || "").toLowerCase() });
  }
  const messages = [];
  const seenMessages = new Set();
  for (const control of controls) {
    for (const attribute of ["aria-describedby", "aria-errormessage"]) {
      const ids = String(control.getAttribute?.(attribute) || "").split(/\s+/).filter(Boolean);
      for (const id of ids) {
        const described = document.getElementById?.(id) || null;
        if (!described || !(described === dialog || dialog.contains?.(described)) || !ppVisible(described)) continue;
        const text = String(described.innerText || described.textContent || "").replace(/\s+/g, " ").trim();
        if (!text || text.length > 220) continue;
        const key = `${text}\n${attribute}`;
        if (seenMessages.has(key)) continue;
        seenMessages.add(key);
        messages.push({ text: ppSafeEvidenceText(text, 180), role: described.getAttribute?.("role") || null, aria_live: described.getAttribute?.("aria-live") || null, association: attribute });
      }
    }
  }
  for (const node of Array.from(dialog.querySelectorAll?.('*') || []).filter(ppVisible)) {
    const text = String(node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
    if (!text || text.length > 220) continue;
    let color = "";
    try { color = String(globalThis.getComputedStyle?.(node)?.color || ""); } catch (_) { color = ""; }
    const rgb = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
    const redLike = Boolean(rgb && Number(rgb[1]) > Number(rgb[2]) * 1.35 && Number(rgb[1]) > Number(rgb[3]) * 1.2);
    const warningLike = /error|invalid|required|must |please |warning|choose|select|enter|missing/i.test(text);
    const role = node.getAttribute?.("role") || "";
    if (!(role === "alert" || node.getAttribute?.("aria-live") || warningLike || redLike)) continue;
    if (Array.from(node.children || []).some((child) => ppVisible(child) && String(child.innerText || child.textContent || "").replace(/\s+/g, " ").trim() === text)) continue;
    const key = `${text}\n${role}\n${color}`;
    if (seenMessages.has(key)) continue;
    seenMessages.add(key);
    messages.push({ text: ppSafeEvidenceText(text, 180), role: role || null, aria_live: node.getAttribute?.("aria-live") || null, red_like_style: redLike, warning_like_text: warningLike });
    if (messages.length >= 12) break;
  }
  return { invalid_fields: unique(invalidFields), required_blank_fields: unique(requiredBlankFields), disabled_required_controls: unique(disabledRequiredControls), empty_comboboxes: unique(emptyComboboxes), messages };
}

async function ppFacebookActivateUpdateOnce(dialog, target, authorizationNonce) {
  const eventTypes = ["pointermove", "mousemove", "pointerdown", "mousedown", "pointerup", "mouseup", "click"];
  const events = [];
  const byEvent = new Map();
  const listeners = [];
  let observedUpdateTarget = target;
  const rect = target?.getBoundingClientRect?.();
  const center = rect && Number(rect.width) > 0 && Number(rect.height) > 0
    ? { x: Number(rect.left) + Number(rect.width) / 2, y: Number(rect.top) + Number(rect.height) / 2 }
    : null;
  let hitTarget = null;
  try { if (center) hitTarget = document.elementFromPoint?.(center.x, center.y) || null; } catch (_) { hitTarget = null; }
  const targetIsLive = Boolean(target?.isConnected && document.contains?.(target) && target.closest?.('[role="dialog"]') === dialog);
  const hitIsInsideTarget = Boolean(hitTarget && (hitTarget === target || target?.contains?.(hitTarget)));
  const describe = (node) => node ? {
    tag: String(node.tagName || "").toLowerCase() || null,
    role: node.getAttribute?.("role") || null,
    label: ppSafeEvidenceText(ppFacebookUpdateLabel(node) || node.getAttribute?.("aria-label") || "", 100),
  } : null;
  const viewport = () => {
    const vv = globalThis.visualViewport || null;
    const de = document.documentElement || {};
    return {
      inner_width: Number(globalThis.innerWidth) || null, inner_height: Number(globalThis.innerHeight) || null,
      outer_width: Number(globalThis.outerWidth) || null, outer_height: Number(globalThis.outerHeight) || null,
      device_pixel_ratio: Number(globalThis.devicePixelRatio) || null,
      visual_viewport: vv ? { width: Number(vv.width) || null, height: Number(vv.height) || null, scale: Number(vv.scale) || null, offset_left: Number(vv.offsetLeft) || 0, offset_top: Number(vv.offsetTop) || 0, page_left: Number(vv.pageLeft) || 0, page_top: Number(vv.pageTop) || 0 } : null,
      scroll_x: Number(globalThis.scrollX) || 0, scroll_y: Number(globalThis.scrollY) || 0,
      document_client_width: Number(de.clientWidth) || null, document_client_height: Number(de.clientHeight) || null,
    };
  };
  const describePathNode = (node) => ({ ...describe(node), text: ppSafeEvidenceText(node?.innerText || node?.textContent || node?.getAttribute?.("aria-label") || "", 80) });
  const physicalHitTarget = describe(hitTarget);
  if (!targetIsLive || !hitIsInsideTarget || !authorizationNonce) {
    return {
      error: !targetIsLive ? "FACEBOOK_UPDATE_TARGET_NOT_CURRENT"
        : !hitIsInsideTarget ? "FACEBOOK_UPDATE_HIT_TARGET_OUTSIDE_ACTION" : "FACEBOOK_CDP_AUTHORIZATION_MISSING",
      invocation_count: 0, activation_method: "chrome_debugger_cdp_input", events,
      event_counts: Object.fromEntries(eventTypes.map((type) => [type, 0])),
      actionable_target: describe(target), physical_hit_target: physicalHitTarget,
    };
  }
  const attach = (node, scope) => {
    if (!node?.addEventListener) return;
    for (const type of eventTypes) {
      const listener = (event) => {
        let evidence = byEvent.get(event);
        if (!evidence) {
          evidence = {
            type, listener_scopes: [], trusted: Boolean(event.isTrusted),
            capture_order: events.length + 1, captured_at_ms: Date.now(),
            event_time_stamp_ms: Number.isFinite(event.timeStamp) ? Number(event.timeStamp) : null,
            event_target: describe(event.target),
            client_x: Number.isFinite(event.clientX) ? event.clientX : null,
            client_y: Number.isFinite(event.clientY) ? event.clientY : null,
            page_x: Number.isFinite(event.pageX) ? event.pageX : null,
            page_y: Number.isFinite(event.pageY) ? event.pageY : null,
            screen_x: Number.isFinite(event.screenX) ? event.screenX : null,
            screen_y: Number.isFinite(event.screenY) ? event.screenY : null,
            button: Number.isFinite(event.button) ? event.button : null,
            buttons: Number.isFinite(event.buttons) ? event.buttons : null,
            target_is_update: event.target === observedUpdateTarget,
            target_is_update_descendant: Boolean(observedUpdateTarget.contains?.(event.target)),
            composed_path: (typeof event.composedPath === "function" ? event.composedPath() : []).slice(0, 8).map(describePathNode),
            default_prevented: Boolean(event.defaultPrevented),
          };
          byEvent.set(event, evidence);
          events.push(evidence);
        }
        if (!evidence.listener_scopes.includes(scope)) evidence.listener_scopes.push(scope);
      };
      node.addEventListener(type, listener, true);
      listeners.push([node, type, listener]);
    }
  };
  globalThis.__POSTERPRO_CDP_UPDATE_CAPTURE = {
    events,
    attachFreshTarget(nextDialog, nextTarget) {
      observedUpdateTarget = nextTarget;
      attach(nextDialog, "dialog-post-foreground");
      attach(nextTarget, "target-post-foreground");
    },
  };
  attach(globalThis, "window");
  attach(document, "document");
  attach(dialog, "dialog");
  attach(target, "target");
  let error = null;
  let response = null;
  try {
    response = await chrome.runtime.sendMessage({
      action: "posterpro_marketplace_control_activation",
      authorization_nonce: authorizationNonce,
      marketplace: "facebook",
      expected_origin: "https://www.facebook.com",
      expected_stage: "FACEBOOK_SHIPPING_UPDATE",
      expected_transition: "SHIPPING_DIALOG_CLOSED",
      target: {
        x: center.x, y: center.y, left: Number(rect.left), top: Number(rect.top),
        width: Number(rect.width), height: Number(rect.height),
        viewport_width: Number(globalThis.innerWidth), viewport_height: Number(globalThis.innerHeight),
        viewport_environment: viewport(),
        label: ppSafeEvidenceText(ppFacebookUpdateLabel(target), 32),
        role: target.getAttribute?.("role") || null,
        dialog_heading: "Change shipping method", hit_target_inside: hitIsInsideTarget,
        is_connected: Boolean(target.isConnected), document_contains: Boolean(document.contains?.(target)),
        current_dialog_match: target.closest?.('[role="dialog"]') === dialog,
        enabled: !target.disabled && target.getAttribute?.("aria-disabled") !== "true",
        evidence_source: "content-script-live-target",
        content_script_build_id: ppContentScriptIdentity().content_script_build_id,
      },
    });
    if (!response?.ok) error = response?.error_code || "FACEBOOK_CDP_INPUT_REJECTED";
  } catch (caught) { error = ppSafeEvidenceText(String(caught?.message || caught), 180); }
  finally {
    for (const [node, type, listener] of listeners) node.removeEventListener?.(type, listener, true);
    delete globalThis.__POSTERPRO_CDP_UPDATE_CAPTURE;
  }
  const commandResults = response?.command_results || [];
  const timedEvents = events.map((event) => ({
    ...event,
    command_time_overlap: commandResults.filter((command) => Number.isFinite(command.started_at_ms)
      && Number.isFinite(command.completed_at_ms) && event.captured_at_ms >= command.started_at_ms
      && event.captured_at_ms <= command.completed_at_ms).map((command) => command.label),
  }));
  const freshRect = response?.post_foreground_target?.rect || rect;
  const activationPoint = response?.activation_coordinate || response?.browser_input?.activation_coordinate || null;
  const activationEventTypes = ["pointerdown", "mousedown", "pointerup", "mouseup", "click"];
  const trustedAtFreshTarget = timedEvents.some((event) => event.trusted && activationEventTypes.includes(event.type)
    && event.target_is_update_descendant
    && Number.isFinite(event.client_x) && Number.isFinite(event.client_y)
    && activationPoint && event.client_x === activationPoint.x && event.client_y === activationPoint.y
    && event.client_x >= Number(freshRect.left) && event.client_x < Number(freshRect.right)
    && event.client_y >= Number(freshRect.top) && event.client_y < Number(freshRect.bottom));
  const trustedElsewhere = timedEvents.some((event) => event.trusted && activationEventTypes.includes(event.type));
  const classification = [];
  if (response?.error_code === "MARKETPLACE_CONTROL_GEOMETRY_UNSTABLE") classification.push("F_GEOMETRY_UNSTABLE");
  const delta = response?.coordinate_deltas || {};
  if ([delta.x, delta.y, delta.width, delta.height, delta.viewport_width, delta.viewport_height,
    delta.visual_viewport_width, delta.visual_viewport_height, delta.scroll_x, delta.scroll_y]
    .some((value) => Number.isFinite(Number(value)) && Math.abs(Number(value)) >= 1)) classification.push("A_POST_FOREGROUND_TARGET_MOVED");
  if (response?.content_browser_hit_test_agree === true) classification.push("B_CONTENT_AND_BROWSER_HIT_TEST_MATCH");
  if (response?.content_browser_hit_test_agree === false && response?.browser_hit_test?.ok) classification.push("C_CONTENT_BROWSER_HIT_TEST_DISAGREE");
  if (trustedAtFreshTarget) classification.push("D_TRUSTED_INPUT_AT_VALIDATED_CONTROL");
  else if (trustedElsewhere) classification.push("E_TRUSTED_INPUT_OTHER_LOCATION");
  return {
    error,
    invocation_count: response?.invocation_count || (error ? 0 : 1),
    activation_method: response?.activation_method || "chrome_debugger_cdp_input",
    actionable_target: describe(target),
    physical_hit_target: physicalHitTarget,
    events: timedEvents,
    event_counts: Object.fromEntries(eventTypes.map((type) => [type, timedEvents.filter((event) => event.type === type).length])),
    browser_input: response || null,
    activation_coordinate: response?.activation_coordinate || null,
    content_integer_hit_test: response?.content_integer_hit_test || null,
    browser_context: response?.browser_context || null,
    protocol_layout_metrics: response?.protocol_layout_metrics || null,
    pre_foreground_target: response?.pre_foreground_target || null,
    post_foreground_target: response?.post_foreground_target || null,
    post_foreground_samples: response?.post_foreground_samples || [],
    coordinate_deltas: response?.coordinate_deltas || null,
    browser_hit_test: response?.browser_hit_test || null,
    content_browser_hit_test_agree: response?.content_browser_hit_test_agree ?? null,
    activation_classifications: classification,
    delivery_status: response?.error_code === "MARKETPLACE_CONTROL_GEOMETRY_UNSTABLE" ? "MARKETPLACE_CONTROL_GEOMETRY_UNSTABLE"
      : response?.error_code === "CDP_HIT_NODE_ANCESTRY_UNAVAILABLE" ? "CDP_HIT_NODE_ANCESTRY_UNAVAILABLE"
      : response?.error_code === "MARKETPLACE_CONTENT_BROWSER_HIT_TEST_DISAGREE" ? "MARKETPLACE_CONTENT_BROWSER_HIT_TEST_DISAGREE"
      : !response?.ok ? "CDP_COMMAND_REJECTED"
      : !events.some((event) => event.trusted) ? "CDP_COMMAND_ACCEPTED_NO_TRUSTED_PAGE_EVENT"
        : trustedAtFreshTarget ? "CDP_INPUT_OBSERVED_AT_VALIDATED_CONTROL"
          : events.some((event) => event.trusted) ? "CDP_INPUT_OBSERVED_IN_PAGE_OTHER_LOCATION" : "CDP_COMMAND_ACCEPTED_NO_TRUSTED_PAGE_EVENT",
    viewport_environment: viewport(),
  };
}

function ppFacebookPostForegroundActivationTarget(dialog, node, stabilitySamples) {
  const evidence = ppFacebookUpdateTargetEvidence(node, dialog);
  const rect = evidence?.rect;
  const center = evidence?.center;
  const viewport = evidence?.viewport_environment || {};
  const hit = evidence?.element_from_point || null;
  const hitIsInside = Boolean(evidence?.hit_is_target_or_descendant);
  const target = {
    x: Number(center?.x), y: Number(center?.y),
    left: Number(rect?.left), top: Number(rect?.top), width: Number(rect?.width), height: Number(rect?.height),
    viewport_width: Number(viewport.inner_width), viewport_height: Number(viewport.inner_height),
    viewport_environment: viewport,
    label: ppSafeEvidenceText(ppFacebookUpdateLabel(node), 32), role: node.getAttribute?.("role") || null,
    dialog_heading: "Change shipping method", hit_target_inside: hitIsInside,
    is_connected: Boolean(node.isConnected), document_contains: Boolean(document.contains?.(node)),
    current_dialog_match: node.closest?.('[role="dialog"]') === dialog,
    enabled: !node.disabled && node.getAttribute?.("aria-disabled") !== "true",
    evidence_source: "content-script-live-target",
    content_script_build_id: ppContentScriptIdentity().content_script_build_id,
    rect, center, element_from_point: hit,
    unobscured: Boolean(hitIsInside && !evidence?.covering_element),
    dialog_generation: evidence?.closest_dialog_generation || null,
  };
  return { target, evidence, stability_samples: stabilitySamples };
}

async function ppFacebookResolveUpdateAfterForeground(message) {
  if (message?.expected_content_build_id !== ppContentScriptIdentity().content_script_build_id
    || !message?.authorization_nonce || message?.expected_stage !== "FACEBOOK_SHIPPING_UPDATE") {
    return { ok: false, error_code: "FACEBOOK_POST_FOREGROUND_IDENTITY_OR_AUTH_REJECTED" };
  }
  const deadline = Date.now() + 1600;
  const stabilitySamples = [];
  let previousSignature = null;
  let stableCount = 0;
  let latest = null;
  const nextFrame = () => new Promise((resolve) => {
    if (typeof globalThis.requestAnimationFrame === "function") globalThis.requestAnimationFrame(() => resolve());
    else setTimeout(resolve, 50);
  });
  while (Date.now() < deadline && stabilitySamples.length < 12) {
    await nextFrame();
    const liveDialog = ppFacebookShippingDialogEvidence();
    const resolution = liveDialog ? ppFacebookResolveUpdateTarget(liveDialog.dialog) : { candidates: [], candidateEvidence: [], error_code: "FACEBOOK_SHIPPING_DIALOG_NOT_FOUND" };
    if (resolution.error_code || resolution.candidates.length !== 1 || !liveDialog) {
      stabilitySamples.push({ at_ms: Date.now(), dialog_present: Boolean(liveDialog), match_count: resolution.candidates.length, error_code: resolution.error_code });
      stableCount = 0;
      previousSignature = null;
      continue;
    }
    const node = resolution.candidates[0];
    const resolved = ppFacebookPostForegroundActivationTarget(liveDialog.dialog, node, stabilitySamples.length + 1);
    const t = resolved.target;
    const signature = [t.dialog_generation, t.left, t.top, t.width, t.height, t.viewport_width, t.viewport_height,
      t.viewport_environment.document_client_width, t.viewport_environment.document_client_height,
      t.viewport_environment.visual_viewport?.width, t.viewport_environment.visual_viewport?.height,
      t.viewport_environment.visual_viewport?.scale, t.viewport_environment.scroll_x, t.viewport_environment.scroll_y].join("|");
    const rect = t.rect || {};
    stabilitySamples.push({ at_ms: Date.now(), dialog_generation: t.dialog_generation,
      rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
      center: t.center, viewport_environment: t.viewport_environment, hit_target: t.element_from_point,
      hit_target_inside: t.hit_target_inside, connected: t.is_connected, current_dialog: t.current_dialog_match });
    const closeEnough = (left, right) => Math.abs(Number(left) - Number(right)) <= 0.5;
    if (previousSignature === signature && latest && closeEnough(latest.target.x, t.x) && closeEnough(latest.target.y, t.y)
      && t.is_connected && t.document_contains && t.current_dialog_match && t.enabled && t.unobscured
      && t.hit_target_inside && t.x >= 0 && t.y >= 0 && t.x < t.viewport_width && t.y < t.viewport_height) stableCount += 1;
    else stableCount = 1;
    latest = { ...resolved, dialog: liveDialog.evidence, match_count: resolution.candidates.length,
      candidate_evidence: resolution.candidateEvidence };
    previousSignature = signature;
    if (stableCount >= 2) {
      globalThis.__POSTERPRO_CDP_UPDATE_CAPTURE?.attachFreshTarget?.(liveDialog.dialog, node);
      return { ok: true, target: latest.target, target_evidence: latest.evidence,
        dialog_evidence: latest.dialog, match_count: latest.match_count,
        candidate_evidence: latest.candidate_evidence, stability_samples: stabilitySamples };
    }
  }
  return { ok: false, error_code: latest ? "MARKETPLACE_CONTROL_GEOMETRY_UNSTABLE" : "FACEBOOK_POST_FOREGROUND_TARGET_NOT_RESOLVED",
    target: latest?.target || null, target_evidence: latest?.evidence || null,
    match_count: latest?.match_count ?? 0, candidate_evidence: latest?.candidate_evidence || [],
    stability_samples: stabilitySamples };
}

function ppFacebookValidateUpdateActivationPoint(message) {
  const coordinate = message?.activation_coordinate || {};
  const { x, y } = coordinate;
  const int32 = (value) => Number.isInteger(value) && value >= 0 && value <= 2147483647;
  if (message?.expected_content_build_id !== ppContentScriptIdentity().content_script_build_id
    || message?.expected_stage !== "FACEBOOK_SHIPPING_UPDATE" || !message?.authorization_nonce) {
    return { ok: false, error_code: "FACEBOOK_INTEGER_HIT_TEST_IDENTITY_OR_AUTH_REJECTED" };
  }
  if (!int32(x) || !int32(y)) return { ok: false, error_code: "FACEBOOK_INTEGER_HIT_TEST_COORDINATE_INVALID" };
  const liveDialog = ppFacebookShippingDialogEvidence();
  if (!liveDialog) return { ok: false, error_code: "FACEBOOK_SHIPPING_DIALOG_NOT_FOUND" };
  const resolution = ppFacebookResolveUpdateTarget(liveDialog.dialog);
  if (resolution.error_code || resolution.candidates.length !== 1) {
    return { ok: false, error_code: "FACEBOOK_UPDATE_TARGET_AMBIGUOUS",
      match_count: resolution.candidates.length, candidate_evidence: resolution.candidateEvidence };
  }
  const target = resolution.candidates[0];
  const evidence = ppFacebookUpdateTargetEvidence(target, liveDialog.dialog);
  const rect = evidence?.rect;
  const viewport = evidence?.viewport_environment || {};
  const insideRect = Boolean(rect && x >= rect.left && x < rect.right && y >= rect.top && y < rect.bottom);
  const insideViewport = x < Number(viewport.inner_width) && y < Number(viewport.inner_height);
  let hit = null;
  try { hit = document.elementFromPoint?.(x, y) || null; } catch (_) { hit = null; }
  const targetInsideUpdate = Boolean(hit && (hit === target || target.contains?.(hit)));
  const ancestry = [];
  let current = hit;
  for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
    ancestry.push({ tag: String(current.tagName || "").toLowerCase() || null,
      role: current.getAttribute?.("role") || null,
      label: ppSafeEvidenceText(ppFacebookUpdateLabel(current) || current.getAttribute?.("aria-label") || "", 80) });
    if (current === target) break;
  }
  const currentTarget = Boolean(target.isConnected && document.contains?.(target)
    && target.closest?.('[role="dialog"]') === liveDialog.dialog
    && !target.disabled && target.getAttribute?.("aria-disabled") !== "true");
  const valid = currentTarget && insideRect && insideViewport && targetInsideUpdate;
  return {
    ok: valid,
    error_code: valid ? null : !insideRect ? "FACEBOOK_INTEGER_HIT_TEST_OUTSIDE_UPDATE_RECT"
      : !insideViewport ? "FACEBOOK_INTEGER_HIT_TEST_OUTSIDE_VIEWPORT"
        : !targetInsideUpdate ? "FACEBOOK_INTEGER_HIT_TEST_OUTSIDE_UPDATE"
          : "FACEBOOK_INTEGER_HIT_TEST_TARGET_NOT_CURRENT",
    activation_coordinate: { x, y, rounding_method: String(coordinate.rounding_method || "") },
    current_dialog: { role: "dialog", heading: "Change shipping method",
      generation: ppFacebookDialogGeneration(liveDialog.dialog) },
    update_target: ppFacebookElementStructure(target),
    target_rect: rect,
    content_integer_hit_test: {
      target: hit ? { tag: String(hit.tagName || "").toLowerCase() || null,
        role: hit.getAttribute?.("role") || null,
        label: ppSafeEvidenceText(ppFacebookUpdateLabel(hit) || hit.getAttribute?.("aria-label") || "", 80) } : null,
      target_inside_update: targetInsideUpdate, inside_rect: insideRect, inside_viewport: insideViewport, ancestry,
    },
    target_inside_update: valid,
    viewport_environment: viewport,
  };
}

function ppFacebookShippingControl(dialog, labelPattern) {
  const nodes = Array.from(dialog.querySelectorAll('[role="combobox"],input,select,button,[role="button"],label')).filter(ppVisible);
  return nodes.find((node) => labelPattern.test(String(node.innerText || node.textContent || node.value || node.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim())) || null;
}

function ppFacebookShippingOptions(kind = "all") {
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[role="listbox"],[role="menu"]')).filter(ppVisible);
  const selector = '[role="option"],[role="menuitem"],[role="radio"],input[type="radio"],label,[role="button"],button,li,[data-testid],[tabindex="0"]';
  // Facebook frequently portals chooser options beside (not inside) its dialog.
  // Search visible semantic options page-wide, but retain only safe shipping- or
  // weight-shaped labels so unrelated seller/account text is never reported.
  const roots = [...dialogs, document];
  const seen = new Set();
  const candidates = [];
  for (const root of roots) {
    for (const node of Array.from(root.querySelectorAll(selector))) {
      if (seen.has(node) || !ppVisible(node) || node.disabled || node.getAttribute?.("aria-disabled") === "true") continue;
      seen.add(node);
      const rawLabel = node.labels?.[0]?.innerText || node.innerText || node.textContent || node.getAttribute?.("aria-label") || node.parentElement?.innerText;
      const label = ppSafeEvidenceText(rawLabel, 140);
      if (!label) continue;
      const relevant = kind === "carrier" ? /usps|ups|fed\s*ex|ground advantage|priority mail|shipping carrier/i.test(label)
        : kind === "weight" ? /\b\d+(?:\.\d+)?\s*(?:lb|lbs|pound|pounds)\b|package weight/i.test(label)
          : true;
      if (relevant) candidates.push({
        node, label,
        tag: String(node.tagName || "").toLowerCase(),
        role: node.getAttribute?.("role") || null,
        type: node.getAttribute?.("type") || null,
        checked: node.checked === true || node.getAttribute?.("aria-checked") === "true",
        clickable: node.labels?.[0] || node.closest?.('[role="option"],[role="radio"],[role="menuitem"],[role="button"],button,label,[tabindex="0"]') || node,
      });
    }
  }
  const unique = new Map();
  for (const candidate of candidates) {
    const key = candidate.label.toLowerCase();
    const old = unique.get(key);
    // Prefer the native radio input when present; its associated label is the
    // semantic click surface and selected state is directly verifiable.
    if (!old || candidate.type === "radio" || candidate.role === "option" || candidate.role === "radio") unique.set(key, candidate);
  }
  return Array.from(unique.values());
}

async function ppFacebookChooseShippingControl(control, expectedLabel, fieldName) {
  if (!control) return { ok: false, error_code: "FACEBOOK_SHIPPING_CONTROL_NOT_FOUND", field: fieldName, options: [] };
  const expected = String(expectedLabel || "").replace(/\s+/g, " ").trim().toLowerCase();
  const controlPattern = /carrier/i.test(String(fieldName || "")) ? /shipping carrier/i : /shipping option/i;
  const findLiveControl = () => {
    const liveDialog = ppFacebookShippingDialogEvidence();
    return liveDialog ? ppFacebookShippingControl(liveDialog.dialog, controlPattern) : null;
  };
  let observedControl = findLiveControl() || control;
  const before = String(observedControl.innerText || observedControl.textContent || observedControl.value || "").replace(/\s+/g, " ").trim();
  if (before.toLowerCase().includes(expected)) return { ok: true, selected: before, already_selected: true, options: [] };
  // Facebook can leave the carrier combobox expanded when its modal finishes
  // mounting. Re-clicking an already-open trigger closes its portal and makes
  // a real USPS choice look absent; inspect its live options first.
  if (observedControl.getAttribute?.("aria-expanded") !== "true") observedControl.click?.();
  const started = Date.now();
  let options = [];
  let matches = [];
  const observations = [];
  while (Date.now() - started < 9000) {
    options = ppFacebookShippingOptions("carrier");
    matches = options.filter(({ label }) => {
      const normalized = label.toLowerCase();
      return normalized === expected || normalized.startsWith(`${expected} `) || normalized.startsWith(`${expected} (`) || normalized.startsWith(`${expected},`);
    });
    if (observations.length < 12 && (observations.length === 0 || Date.now() - started - observations.at(-1).elapsed_ms >= 500)) {
      observations.push({ elapsed_ms: Date.now() - started, expanded: (findLiveControl() || observedControl).getAttribute?.("aria-expanded") || null, option_labels: [...new Set(options.map((row) => row.label))].slice(0, 12) });
    }
    if (matches.length) break;
    await ppWait(200);
  }
  const visibleOptions = [...new Set(options.map((row) => row.label))].slice(0, 24);
  if (matches.length !== 1) return { ok: false, error_code: matches.length > 1 ? "FACEBOOK_SHIPPING_OPTION_AMBIGUOUS" : "FACEBOOK_SHIPPING_OPTION_NOT_RENDERED", field: fieldName, before, expanded_before: control.getAttribute?.("aria-expanded") || null, visible_options: visibleOptions, observations, wait_ms: Date.now() - started };
  const chosen = matches[0].node;
  const clickable = chosen.closest?.('[role="option"],[role="menuitem"],[role="radio"],[role="button"],button,li,[tabindex="0"]') || chosen;
  clickable.click?.();
  const verifyStarted = Date.now();
  let after = "";
  do {
    await ppWait(100);
    observedControl = findLiveControl() || observedControl;
    after = String(observedControl.innerText || observedControl.textContent || observedControl.value || "").replace(/\s+/g, " ").trim();
    if (after.toLowerCase().includes(expected)) break;
  } while (Date.now() - verifyStarted < 1800);
  return after.toLowerCase().includes(expected)
    ? { ok: true, selected: after, selected_option: matches[0].label, options: visibleOptions, observations, wait_ms: Date.now() - started }
    : { ok: false, error_code: "FACEBOOK_SHIPPING_SELECTION_NOT_PERSISTED", field: fieldName, before, after, selected_option: matches[0].label, options: visibleOptions };
}

function ppFacebookWeightOptionMatches(label, packageWeight) {
  const wanted = Number(packageWeight);
  if (!Number.isFinite(wanted) || wanted <= 0) return false;
  const normalized = String(label || "").toLowerCase().replace(/[–—−]/g, "-").replace(/,/g, "").trim();
  const range = normalized.match(/(\d+(?:\.\d+)?)\s*(?:-|\bto\b)\s*(\d+(?:\.\d+)?)\s*(?:lb|lbs|pound|pounds)\b/);
  if (range) {
    const low = Number(range[1]);
    const high = Number(range[2]);
    return wanted > low && wanted <= high;
  }
  const pounds = normalized.match(/\d+(?:\.\d+)?\s*(?:lb|lbs|pound|pounds)\b/g) || [];
  const values = pounds.map((value) => Number(value.match(/\d+(?:\.\d+)?/)[0])).filter(Number.isFinite);
  if (!values.length) return false;
  if (/\bunder\b|\bless than\b|\bup to\b|^</.test(normalized)) return wanted <= values[0];
  if (/\bover\b|\bmore than\b|^>/.test(normalized)) return wanted >= values[0];
  if (values.length === 1) return Math.abs(values[0] - wanted) < 0.001;
  const low = Math.min(...values);
  const high = Math.max(...values);
  // Facebook's adjacent bands share a printed boundary (e.g. 1–2 and 2–5).
  // Assign the boundary to the lower band to avoid silently rounding up twice.
  return wanted > low && wanted <= high;
}

async function ppFacebookChoosePackageWeight(control, packageWeight) {
  if (!control || !Number.isFinite(Number(packageWeight)) || Number(packageWeight) <= 0) {
    return { ok: false, error_code: "FACEBOOK_PACKAGE_WEIGHT_UNAVAILABLE", package_weight: packageWeight ?? null };
  }
  const current = String(control.innerText || control.textContent || control.value || control.getAttribute?.("aria-valuetext") || "").replace(/\s+/g, " ").trim();
  if (ppFacebookWeightOptionMatches(current, packageWeight)) return { ok: true, selected: current, already_selected: true, options: [] };
  if (String(control.tagName || "").toLowerCase() === "input") {
    const setter = Object.getOwnPropertyDescriptor(control.ownerDocument?.defaultView?.HTMLInputElement?.prototype || {}, "value")?.set;
    if (setter) setter.call(control, String(packageWeight));
    else control.value = String(packageWeight);
    control.dispatchEvent?.(new Event("input", { bubbles: true }));
    control.dispatchEvent?.(new Event("change", { bubbles: true }));
    const after = String(control.value || "").trim();
    return Number(after) === Number(packageWeight)
      ? { ok: true, selected: after, options: [], input_value: after }
      : { ok: false, error_code: "FACEBOOK_PACKAGE_WEIGHT_SELECTION_NOT_PERSISTED", before: current, after };
  }
  const expanded = control.getAttribute?.("aria-expanded") === "true";
  if (!expanded) control.click?.();
  const deadline = Date.now() + 8000;
  let options = [];
  let matches = [];
  const observations = [];
  do {
    options = ppFacebookShippingOptions("weight");
    matches = options.filter(({ label }) => ppFacebookWeightOptionMatches(label, packageWeight));
    if (observations.length < 12 && (observations.length === 0 || Date.now() - deadline + 8000 - observations.at(-1).elapsed_ms >= 500)) {
      observations.push({ elapsed_ms: Date.now() - deadline + 8000, expanded: control.getAttribute?.("aria-expanded") || null, option_labels: [...new Set(options.map((row) => row.label))].slice(0, 12) });
    }
    if (matches.length || Date.now() >= deadline) break;
    await ppWait(200);
  } while (Date.now() < deadline);
  if (matches.length !== 1) return {
    ok: false,
    error_code: matches.length > 1 ? "FACEBOOK_PACKAGE_WEIGHT_OPTION_AMBIGUOUS" : "FACEBOOK_PACKAGE_WEIGHT_OPTION_NOT_RENDERED",
    package_weight: Number(packageWeight),
    options: [...new Set(options.map((row) => row.label))].slice(0, 20),
    observations,
  };
  const option = matches[0];
  const beforeSelected = Boolean(option.checked);
  const clickable = option.clickable || option.node.closest?.('[role="option"],[role="menuitem"],[role="radio"],[role="button"],button,li,label,[tabindex="0"]') || option.node;
  if (!beforeSelected) clickable.click?.();
  const selected = Date.now() + 1800;
  let after = "";
  let selectedState = beforeSelected;
  do {
    await ppWait(100);
    after = String(control.innerText || control.textContent || control.value || control.getAttribute?.("aria-valuetext") || "").replace(/\s+/g, " ").trim();
    selectedState = Boolean(option.node.checked === true || option.node.getAttribute?.("aria-checked") === "true" || option.clickable?.getAttribute?.("aria-checked") === "true");
    if (selectedState || ppFacebookWeightOptionMatches(after, packageWeight)) break;
  } while (Date.now() < selected);
  const persisted = selectedState || ppFacebookWeightOptionMatches(after, packageWeight);
  return persisted
    ? { ok: true, selected: after || option.label, selected_option: option.label, selected_state: selectedState, actionable_element: { tag: option.tag, role: option.role, type: option.type, click_target_tag: String(clickable.tagName || "").toLowerCase(), click_target_role: clickable.getAttribute?.("role") || null }, options: [...new Set(options.map((row) => row.label))].slice(0, 20) }
    : { ok: false, error_code: "FACEBOOK_PACKAGE_WEIGHT_SELECTION_NOT_PERSISTED", before: current, after, selected_option: option.label, actionable_element: { tag: option.tag, role: option.role, type: option.type, click_target_tag: String(clickable.tagName || "").toLowerCase(), click_target_role: clickable.getAttribute?.("role") || null } };
}

async function ppFacebookConfigureShippingDialog(payload = {}, browserInputNonce = null, priorFormReadiness = null) {
  const dialog = ppFacebookShippingDialogEvidence();
  if (!dialog) return { handled: false };
  const service = String(payload.shipping?.domestic_service || "").replace(/[_-]+/g, " ").trim();
  const prepaidControl = ppFacebookShippingControl(dialog.dialog, /shipping option/i);
  let carrierControl = ppFacebookShippingControl(dialog.dialog, /shipping carrier/i);
  let weightControl = ppFacebookShippingControl(dialog.dialog, /package weight/i);
  const currentPrepaid = String(prepaidControl?.innerText || prepaidControl?.textContent || prepaidControl?.value || "").replace(/\s+/g, " ").trim();
  const trace = [
    ...(priorFormReadiness?.after_initial_fill ? [{ phase: "FORM_READINESS_A", form_readiness: priorFormReadiness.after_initial_fill, field_transition_timeline: priorFormReadiness.field_transition_timeline || [] }] : []),
    { phase: "DIALOG_OPEN", dialog: dialog.evidence, snapshot: ppFacebookStageSnapshot(2) },
  ];
  const weightEvidence = dialog.controls.find((item) => /package weight/i.test(item.label || "")) || null;
  const packageRequired = Boolean(weightEvidence?.required || weightEvidence?.invalid);
  const packageWeight = Number(payload.shipping?.package_weight);
  let packageValue = String(weightControl?.value || weightControl?.getAttribute?.("aria-valuetext") || weightControl?.getAttribute?.("data-value") || weightControl?.innerText || "").replace(/package weight/i, "").trim();
  let weight = { ok: true, not_required: !packageRequired, selected: packageValue || null };
  // Use the canonical profile before carrier selection: Facebook can withhold
  // service choices until a package weight has been selected. Estimates are
  // usable, and a blank visible weight chooser is not evidence of an existing
  // selection even when Facebook omits aria-required.
  if (weightControl && Number.isFinite(packageWeight) && packageWeight > 0 && !ppFacebookWeightOptionMatches(packageValue, packageWeight)) {
    weight = await ppFacebookChoosePackageWeight(weightControl, payload.shipping?.package_weight);
    trace.push({ phase: "PACKAGE_WEIGHT_SELECTION", diagnostic: weight, snapshot: ppFacebookStageSnapshot(2) });
    if (!weight.ok) return { handled: true, ready: false, error_code: weight.error_code, stage: "SHIPPING_PACKAGE_WEIGHT_SELECTION", configured_package_weight: payload.shipping?.package_weight ?? null, estimated: payload.shipping?.estimated === true, weight_diagnostic: weight, trace, dialog: ppFacebookShippingDialogEvidence()?.evidence || dialog.evidence };
  } else if (packageRequired && !packageValue) {
    return { handled: true, ready: false, error_code: "FACEBOOK_PACKAGE_WEIGHT_UNAVAILABLE", stage: "SHIPPING_PACKAGE_WEIGHT_SELECTION", configured_package_weight: payload.shipping?.package_weight ?? null, estimated: payload.shipping?.estimated === true, trace, dialog: dialog.evidence };
  }

  // Re-query after selection because React replaces the dialog controls on change.
  let currentDialog = ppFacebookShippingDialogEvidence();
  if (!currentDialog) return { handled: true, ready: false, error_code: "FACEBOOK_SHIPPING_DIALOG_CLOSED_UNEXPECTEDLY", stage: "SHIPPING_CARRIER_SELECTION", trace };
  carrierControl = ppFacebookShippingControl(currentDialog.dialog, /shipping carrier/i);
  weightControl = ppFacebookShippingControl(currentDialog.dialog, /package weight/i);
  // Facebook can leave its nested weight chooser mounted after the native
  // radio becomes checked. Do not continue clicking controls behind that
  // portal: explicitly collapse the already-expanded weight trigger and wait
  // for the chooser to disappear before touching carrier/free-shipping/Update.
  const findWeightChooser = () => Array.from(document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"]'))
    .filter(ppVisible)
    .find((node) => /package weight/i.test(String(node.getAttribute?.("aria-label") || node.innerText || node.textContent || ""))
      && /shipping options/i.test(String(node.getAttribute?.("aria-label") || node.innerText || node.textContent || ""))) || null;
  let weightChooser = findWeightChooser();
  if (weightChooser) {
    const expanded = weightControl?.getAttribute?.("aria-expanded") === "true";
    trace.push({ phase: "WEIGHT_CHOOSER_REMAINS_OPEN", expanded, selected_weight: weight.selected || null, snapshot: ppFacebookStageSnapshot(2) });
    if (expanded && weightControl && !weightControl.disabled && weightControl.getAttribute?.("aria-disabled") !== "true") {
      weightControl.click?.();
      trace.push({ phase: "WEIGHT_CHOOSER_COLLAPSE_CLICKED", snapshot: ppFacebookStageSnapshot(2) });
      const closeDeadline = Date.now() + 1800;
      while (Date.now() < closeDeadline && findWeightChooser()) await ppWait(100);
      weightChooser = findWeightChooser();
    }
    if (weightChooser) {
      trace.push({ phase: "WEIGHT_CHOOSER_CLOSE_FAILED", snapshot: ppFacebookStageSnapshot(2) });
      return { handled: true, ready: false, error_code: "FACEBOOK_PACKAGE_WEIGHT_CHOOSER_STILL_OPEN", stage: "SHIPPING_PACKAGE_WEIGHT_CHOOSER_CLOSE", weight_diagnostic: weight, trace, dialog: ppFacebookShippingDialogEvidence()?.evidence || currentDialog.evidence };
    }
    // React may replace the parent dialog when the portal closes.
    currentDialog = ppFacebookShippingDialogEvidence();
    if (!currentDialog) return { handled: true, ready: false, error_code: "FACEBOOK_SHIPPING_DIALOG_CLOSED_UNEXPECTEDLY", stage: "SHIPPING_CARRIER_SELECTION", weight_diagnostic: weight, trace };
    carrierControl = ppFacebookShippingControl(currentDialog.dialog, /shipping carrier/i);
    weightControl = ppFacebookShippingControl(currentDialog.dialog, /package weight/i);
    const persistedWeight = String(weightControl?.innerText || weightControl?.textContent || weightControl?.value || weightControl?.getAttribute?.("aria-valuetext") || "").replace(/\s+/g, " ").trim();
    if (Number.isFinite(packageWeight) && packageWeight > 0 && !ppFacebookWeightOptionMatches(persistedWeight, packageWeight)) return {
      handled: true, ready: false, error_code: "FACEBOOK_PACKAGE_WEIGHT_STATE_NOT_PERSISTED",
      stage: "SHIPPING_PACKAGE_WEIGHT_VERIFY", configured_package_weight: payload.shipping?.package_weight ?? null,
      observed_weight: persistedWeight || null, weight_diagnostic: weight, trace,
      dialog: currentDialog.evidence,
    };
    trace.push({ phase: "WEIGHT_STATE_REQUERIED", selected: persistedWeight || null, snapshot: ppFacebookStageSnapshot(2) });
    trace.push({ phase: "WEIGHT_CHOOSER_CLOSED", dialog: currentDialog.evidence, snapshot: ppFacebookStageSnapshot(2) });
  }
  trace.push({ phase: "BEFORE_CARRIER_SELECTION", dialog: currentDialog.evidence, snapshot: ppFacebookStageSnapshot(2) });
  const carrier = await ppFacebookChooseShippingControl(carrierControl, service, "shipping_carrier");
  trace.push({ phase: "CARRIER_SELECTION", diagnostic: carrier, snapshot: ppFacebookStageSnapshot(2) });
  if (!carrier.ok) return { handled: true, ready: false, error_code: carrier.error_code, stage: "SHIPPING_CARRIER_SELECTION", configured_service: service, prepaid_option: currentPrepaid || null, carrier_diagnostic: carrier, weight_diagnostic: weight, trace, dialog: currentDialog.evidence };

  currentDialog = ppFacebookShippingDialogEvidence();
  if (!currentDialog) return { handled: true, ready: false, error_code: "FACEBOOK_SHIPPING_DIALOG_CLOSED_UNEXPECTEDLY", stage: "SHIPPING_CARRIER_SELECTION", carrier_diagnostic: carrier, weight_diagnostic: weight, trace };
  carrierControl = ppFacebookShippingControl(currentDialog.dialog, /shipping carrier/i);
  const persistedCarrier = String(carrierControl?.innerText || carrierControl?.textContent || carrierControl?.value || carrierControl?.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim();
  if (!carrierControl || !persistedCarrier.toLowerCase().includes(service.toLowerCase())) return {
    handled: true, ready: false, error_code: "FACEBOOK_SHIPPING_CARRIER_STATE_NOT_PERSISTED",
    stage: "SHIPPING_CARRIER_VERIFY", configured_service: service, observed_carrier: persistedCarrier || null,
    carrier_diagnostic: carrier, weight_diagnostic: weight, trace,
    dialog: currentDialog.evidence,
  };
  trace.push({ phase: "CARRIER_STATE_REQUERIED", selected: persistedCarrier, snapshot: ppFacebookStageSnapshot(2) });

  const desiredFreeShipping = typeof payload.shipping?.free_shipping === "boolean"
    ? payload.shipping.free_shipping
    : typeof payload.shipping?.buyer_pays_shipping === "boolean"
      ? !payload.shipping.buyer_pays_shipping : null;
  const findFreeShippingControl = () => Array.from(currentDialog.dialog.querySelectorAll('input[type="checkbox"],[role="checkbox"]'))
    .filter(ppVisible)
    .find((node) => /cover the shipping cost|free shipping/i.test(String(node.labels?.[0]?.innerText || node.innerText || node.textContent || node.parentElement?.innerText || "")));
  const freeShippingControl = findFreeShippingControl();
  let freeShipping = { desired: desiredFreeShipping, control_found: Boolean(freeShippingControl), changed: false };
  if (desiredFreeShipping !== null && freeShippingControl) {
    const readFreeShipping = () => {
      // React can replace the parent modal immediately on checkbox change.
      // Refresh the modal reference before reading controlled state.
      const liveDialog = ppFacebookShippingDialogEvidence();
      if (liveDialog) currentDialog = liveDialog;
      const node = findFreeShippingControl();
      const aria = node?.getAttribute?.("aria-checked");
      return typeof node?.checked === "boolean" ? node.checked : aria === "true" ? true : aria === "false" ? false : null;
    };
    if (readFreeShipping() !== desiredFreeShipping) {
      const label = freeShippingControl.labels?.[0]
        || freeShippingControl.closest?.('label,[role="checkbox"],[role="button"]')
        || freeShippingControl;
      label.click?.();
      freeShipping.changed = true;
    }
    const verifyDeadline = Date.now() + 1600;
    while (Date.now() < verifyDeadline && readFreeShipping() !== desiredFreeShipping) await ppWait(100);
    freeShipping.checked = readFreeShipping();
    if (freeShipping.checked !== desiredFreeShipping) return {
      handled: true, ready: false, error_code: "FACEBOOK_FREE_SHIPPING_SELECTION_NOT_PERSISTED",
      stage: "SHIPPING_FREE_SHIPPING_SELECTION", carrier_diagnostic: carrier,
      weight_diagnostic: weight, free_shipping_diagnostic: freeShipping, trace,
      dialog: ppFacebookShippingDialogEvidence()?.evidence || currentDialog.evidence,
    };
    // Checkbox changes can replace the controlled parent dialog. Re-query it
    // before Update and prove the previously selected carrier/weight survived.
    currentDialog = ppFacebookShippingDialogEvidence();
    if (!currentDialog) return {
      handled: true, ready: false, error_code: "FACEBOOK_SHIPPING_DIALOG_CLOSED_UNEXPECTEDLY",
      stage: "SHIPPING_FREE_SHIPPING_VERIFY", carrier_diagnostic: carrier,
      weight_diagnostic: weight, free_shipping_diagnostic: freeShipping, trace,
    };
    const refreshedWeight = ppFacebookShippingControl(currentDialog.dialog, /package weight/i);
    const refreshedCarrier = ppFacebookShippingControl(currentDialog.dialog, /shipping carrier/i);
    const refreshedWeightText = String(refreshedWeight?.innerText || refreshedWeight?.textContent || refreshedWeight?.value || "").replace(/\s+/g, " ").trim();
    const refreshedCarrierText = String(refreshedCarrier?.innerText || refreshedCarrier?.textContent || refreshedCarrier?.value || "").replace(/\s+/g, " ").trim();
    if ((Number.isFinite(packageWeight) && packageWeight > 0 && !ppFacebookWeightOptionMatches(refreshedWeightText, packageWeight))
      || !refreshedCarrierText.toLowerCase().includes(service.toLowerCase())
      || readFreeShipping() !== desiredFreeShipping) return {
      handled: true, ready: false, error_code: "FACEBOOK_SHIPPING_STATE_REVERTED",
      stage: "SHIPPING_CONFIGURATION_PERSISTENCE_VERIFY", configured_service: service,
      configured_package_weight: payload.shipping?.package_weight ?? null,
      observed_weight: refreshedWeightText || null, observed_carrier: refreshedCarrierText || null,
      free_shipping_diagnostic: { ...freeShipping, checked: readFreeShipping() },
      carrier_diagnostic: carrier, weight_diagnostic: weight, trace,
      dialog: currentDialog.evidence,
    };
    trace.push({ phase: "FREE_SHIPPING_AND_CARRIER_REQUERIED", weight: refreshedWeightText, carrier: refreshedCarrierText, free_shipping_checked: readFreeShipping(), snapshot: ppFacebookStageSnapshot(2) });
  } else if (desiredFreeShipping !== null) {
    freeShipping.not_exposed = true;
  }
  trace.push({ phase: "FREE_SHIPPING_SELECTION", diagnostic: freeShipping, snapshot: ppFacebookStageSnapshot(2) });

  // Re-read the parent immediately before committing, so Update cannot target
  // a detached dialog node after one of Facebook's controlled-state rerenders.
  // This is the final synchronous re-query before activation. All subsequent
  // evidence and the single existing .click() are tied to this dialog object.
  currentDialog = ppFacebookShippingDialogEvidence();
  if (!currentDialog) return { handled: true, ready: false, error_code: "FACEBOOK_SHIPPING_DIALOG_CLOSED_UNEXPECTEDLY", stage: "SHIPPING_DIALOG_UPDATE", carrier_diagnostic: carrier, weight_diagnostic: weight, free_shipping_diagnostic: freeShipping, trace };
  const commitWeight = ppFacebookShippingControl(currentDialog.dialog, /package weight/i);
  const commitCarrier = ppFacebookShippingControl(currentDialog.dialog, /shipping carrier/i);
  const commitWeightText = String(commitWeight?.innerText || commitWeight?.textContent || commitWeight?.value || "").replace(/\s+/g, " ").trim();
  const commitCarrierText = String(commitCarrier?.innerText || commitCarrier?.textContent || commitCarrier?.value || "").replace(/\s+/g, " ").trim();
  const commitFreeShipping = findFreeShippingControl();
  const commitFreeShippingChecked = typeof commitFreeShipping?.checked === "boolean"
    ? commitFreeShipping.checked : commitFreeShipping?.getAttribute?.("aria-checked") === "true";
  if ((Number.isFinite(packageWeight) && packageWeight > 0 && !ppFacebookWeightOptionMatches(commitWeightText, packageWeight))
    || !commitCarrierText.toLowerCase().includes(service.toLowerCase())
    || (desiredFreeShipping !== null && freeShipping.control_found && commitFreeShippingChecked !== desiredFreeShipping)) return {
    handled: true, ready: false, error_code: "FACEBOOK_SHIPPING_STATE_REVERTED",
    stage: "SHIPPING_CONFIGURATION_PERSISTENCE_VERIFY", configured_service: service,
    configured_package_weight: payload.shipping?.package_weight ?? null,
    observed_weight: commitWeightText || null, observed_carrier: commitCarrierText || null,
    free_shipping_diagnostic: { ...freeShipping, checked: commitFreeShippingChecked },
    carrier_diagnostic: carrier, weight_diagnostic: weight, trace,
    dialog: currentDialog.evidence,
  };
  trace.push({ phase: "PRE_UPDATE_STATE_VERIFIED", weight: commitWeightText, carrier: commitCarrierText, free_shipping_checked: commitFreeShippingChecked, parent_dialog_open: true, snapshot: ppFacebookStageSnapshot(2) });
  // Do not reuse a node from any earlier state check. Reacquire both the
  // dialog and its exact Update candidate together immediately before click.
  currentDialog = ppFacebookShippingDialogEvidence();
  if (!currentDialog) return { handled: true, ready: false, error_code: "FACEBOOK_SHIPPING_DIALOG_CLOSED_UNEXPECTEDLY", stage: "SHIPPING_DIALOG_UPDATE", carrier_diagnostic: carrier, weight_diagnostic: weight, free_shipping_diagnostic: freeShipping, trace };
  const liveDialog = currentDialog.dialog;
  const dialogGeneration = ppFacebookDialogGeneration(liveDialog);
  const dialogSignature = JSON.stringify({
    generation: dialogGeneration,
    heading: "Change shipping method",
    child_count: Number(liveDialog.childElementCount) || 0,
    actionable_labels: Array.from(liveDialog.querySelectorAll?.('button,[role="button"],input[type="button"],input[type="submit"]') || [])
      .filter(ppVisible).map((node) => ppSafeEvidenceText(ppFacebookUpdateLabel(node), 80)).filter(Boolean).slice(0, 16),
  });
  const updateResolution = ppFacebookResolveUpdateTarget(liveDialog);
  const candidates = updateResolution.candidates;
  const visibleExactLabelNodes = updateResolution.visibleExactLabelNodes;
  const candidateEvidence = updateResolution.candidateEvidence;
  if (updateResolution.error_code) return {
    handled: true, ready: false, error_code: updateResolution.error_code, stage: "SHIPPING_DIALOG_UPDATE_TARGET_RESOLUTION",
    update_match_count: candidates.length, update_candidates: candidateEvidence,
    visible_exact_label_node_count: visibleExactLabelNodes.length, visible_exact_label_nodes: visibleExactLabelNodes,
    dialog_generation: dialogGeneration, dialog_signature: dialogSignature,
    dialog: currentDialog.evidence, carrier_diagnostic: carrier, weight_diagnostic: weight,
    free_shipping_diagnostic: freeShipping, trace,
  };
  const update = candidates[0];
  const targetEvidence = ppFacebookUpdateTargetEvidence(update, liveDialog);
  const validationBefore = ppFacebookShippingValidationSnapshot(liveDialog);
  const validationBeforeSignature = JSON.stringify(validationBefore);
  const formReadinessBeforeUpdate = ppFacebookFormReadinessSnapshot(payload, "B_IMMEDIATELY_BEFORE_SHIPPING_UPDATE_ACTIVATION");
  if (!formReadinessBeforeUpdate.description_persistence.matches) return {
    handled: true, ready: false, error_code: "FACEBOOK_DESCRIPTION_NOT_PERSISTED", stage: "DESCRIPTION_BEFORE_SHIPPING_UPDATE",
    description_evidence: formReadinessBeforeUpdate.description_persistence, trace,
  };
  trace.push({ phase: "FORM_READINESS_B", form_readiness: formReadinessBeforeUpdate });
  trace.push({
    phase: "UPDATE_TARGET_RESOLVED", dialog_generation: dialogGeneration, dialog_signature: dialogSignature,
    dialog_is_current: ppFacebookShippingDialogEvidence()?.dialog === liveDialog,
    update_match_count: candidates.length, update_candidates: candidateEvidence,
    visible_exact_label_node_count: visibleExactLabelNodes.length, visible_exact_label_nodes: visibleExactLabelNodes,
    selected_candidate_index: 0, selected_target: targetEvidence,
    validation: validationBefore,
  });
  trace.push({ phase: "UPDATE_ACTIVATION_SENT", activation_method: "chrome_debugger_cdp_input", invocation_count: 1, target_connected_before: Boolean(update.isConnected), document_contains_before: Boolean(document.contains?.(update)), dialog_generation: dialogGeneration });
  const activation = await ppFacebookActivateUpdateOnce(liveDialog, update, browserInputNonce);
  const focusLeaseId = activation?.browser_input?.browser_context?.focus_lease_id
    || activation?.browser_input?.focus_lease_id || null;
  if (focusLeaseId) ppActiveMarketplaceFocusLeaseId = focusLeaseId;
  const dialogAfterActivation = ppFacebookShippingDialogEvidence();
  const focusTelemetryAfterCommit = await ppFacebookFocusTelemetry();
  const nextAfterActivation = ppFacebookExactContinueControl();
  const formReadinessAfterCommit = ppFacebookFormReadinessSnapshot(payload, "C_AFTER_SHIPPING_COMMIT_BEFORE_NEXT_STABILIZATION");
  if (!formReadinessAfterCommit.description_persistence.matches) return {
    handled: true, ready: false, error_code: "FACEBOOK_DESCRIPTION_NOT_PERSISTED", stage: "DESCRIPTION_AFTER_SHIPPING_COMMIT",
    description_evidence: formReadinessAfterCommit.description_persistence, focus_lease_id: ppActiveMarketplaceFocusLeaseId,
    trace: [...trace, { phase: "FORM_READINESS_C", form_readiness: formReadinessAfterCommit }],
  };
  const isEnabled = (node) => Boolean(node && !node.disabled && node.getAttribute?.("aria-disabled") !== "true");
  const validationAfter = ppFacebookShippingValidationSnapshot(dialogAfterActivation?.dialog || null);
  const priorValidationMessages = new Set(validationBefore.messages.map((message) => message.text));
  const newValidationMessages = validationAfter.messages.filter((message) => !priorValidationMessages.has(message.text));
  const updateCandidatesAfter = dialogAfterActivation ? ppFacebookUpdateCandidates(dialogAfterActivation.dialog) : [];
  const immediateReactResponse = {
    activation_method: activation.activation_method,
    activation_error: activation.error,
    activation_invocation_count: activation.invocation_count,
    actionable_target: activation.actionable_target,
    physical_hit_target: activation.physical_hit_target,
    event_counts: activation.event_counts,
    events: activation.events.slice(0, 20),
    browser_input: activation.browser_input,
    target_still_connected: Boolean(update.isConnected),
    document_contains_target: Boolean(document.contains?.(update)),
    dialog_open: Boolean(dialogAfterActivation),
    dialog_generation_before: dialogGeneration,
    dialog_generation_after: dialogAfterActivation ? ppFacebookDialogGeneration(dialogAfterActivation.dialog) : null,
    dialog_generation_changed: Boolean(dialogAfterActivation && ppFacebookDialogGeneration(dialogAfterActivation.dialog) !== dialogGeneration),
    update_still_present: updateCandidatesAfter.length > 0,
    update_match_count_after: updateCandidatesAfter.length,
    validation: validationAfter,
    validation_changed: JSON.stringify(validationAfter) !== validationBeforeSignature,
    next_enabled: isEnabled(nextAfterActivation),
    new_validation_messages: newValidationMessages,
    shipping_local_error_appeared: newValidationMessages.some((message) => message.role === "alert" || message.red_like_style || message.warning_like_text || /error|invalid|required|warning|must|choose|select|enter|missing/i.test(message.text)),
    focus_telemetry_after_commit: focusTelemetryAfterCommit,
    form_readiness_after_shipping_commit: formReadinessAfterCommit,
    snapshot: ppFacebookStageSnapshot(2),
  };
  trace.push({ phase: "UPDATE_REACT_RESPONSE", ...immediateReactResponse });
  if (activation.error) return {
    handled: true, ready: false, error_code: "FACEBOOK_UPDATE_ACTIVATION_ERROR", stage: "SHIPPING_DIALOG_UPDATE_ACTIVATION",
    update_target: targetEvidence, update_match_count: candidates.length, validation_before: validationBefore,
    activation: immediateReactResponse, trace,
  };
  const deadline = Date.now() + 4000;
  let after = dialogAfterActivation;
  let next = nextAfterActivation;
  const nextFocusTelemetry = [];
  // Closing the modal and enabling Next are separate React commits on some
  // Facebook builds. Wait for both bounded conditions instead of treating a
  // briefly-disabled Next immediately after modal close as a hard failure.
  while (Date.now() < deadline && (after || !isEnabled(next))) {
    await ppWait(200);
    const focusTelemetry = await ppFacebookFocusTelemetry();
    nextFocusTelemetry.push({ sample_index: nextFocusTelemetry.length, ...focusTelemetry });
    const browserFocusKnown = Boolean(focusTelemetry?.browser_context && !focusTelemetry.browser_context.read_error);
    const focusLost = browserFocusKnown && (
      focusTelemetry?.document_has_focus === false
      || focusTelemetry?.visibility_state === "hidden"
      || focusTelemetry?.browser_context?.active !== true
      || focusTelemetry?.browser_context?.highlighted !== true
      || focusTelemetry?.browser_context?.window_focused !== true
    );
    after = ppFacebookShippingDialogEvidence();
    next = ppFacebookExactContinueControl();
    if (focusLost) {
      const focusSnapshot = ppFacebookStageSnapshot(2);
      trace.push({ phase: "FOCUS_LOST_DURING_STAGE", focus_telemetry: focusTelemetry, dialog_open: Boolean(after), next_enabled: isEnabled(next), next_focus_telemetry: nextFocusTelemetry, snapshot: focusSnapshot });
      return { handled: true, ready: false, error_code: "FACEBOOK_FOCUS_LOST_DURING_STAGE", stage: "NEXT_STABILIZATION", focus_telemetry_after_commit: focusTelemetryAfterCommit, next_focus_telemetry: nextFocusTelemetry, dialog_before: dialog.evidence, dialog_after: after?.evidence || null, carrier_diagnostic: carrier, weight_diagnostic: weight, free_shipping_diagnostic: freeShipping, trace, stage_snapshot: focusSnapshot };
    }
  }
  const snapshot = ppFacebookStageSnapshot(2);
  const readiness = ppFacebookPostShippingReadiness(Boolean(after), next);
  const nextControlEvidence = ppFacebookNextControlEvidence(next);
  trace.push({ phase: "AFTER_UPDATE_VERIFY", shipping_configuration_complete: readiness.shipping_configuration_complete, dialog_open: Boolean(after), next_enabled: isEnabled(next), next_control: nextControlEvidence, dialog: after?.evidence || null, next_focus_telemetry: nextFocusTelemetry, snapshot });
  if (readiness.ready) return { handled: true, ready: true, focus_lease_id: focusLeaseId, shipping_configuration_complete: true, stage: "SHIPPING_CONFIGURATION_VERIFIED", dialog_before: dialog.evidence, carrier_diagnostic: carrier, weight_diagnostic: weight, free_shipping_diagnostic: freeShipping, trace, stage_snapshot: snapshot };
  return { handled: true, ready: false, error_code: readiness.error_code, shipping_configuration_complete: readiness.shipping_configuration_complete, stage: after ? "SHIPPING_CONFIGURATION_COMMIT_VERIFY" : "NEXT_STABILIZATION", next_control: nextControlEvidence, dialog_before: dialog.evidence, dialog_after: after?.evidence || null, carrier_diagnostic: carrier, weight_diagnostic: weight, free_shipping_diagnostic: freeShipping, trace, stage_snapshot: snapshot };
}

async function ppAdvanceFacebookToPublishBoundary(payload = {}, browserInputNonce = null, priorFormReadiness = null) {
  const stages = priorFormReadiness ? [{ phase: "FORM_READINESS_A", form_readiness: priorFormReadiness.after_initial_fill || null, field_transition_timeline: priorFormReadiness.field_transition_timeline || [] }] : [];
  const visited = new Set();
  let shippingSelectorAttempted = false;
  const maxTransitions = 4;
  for (let step = 0; step <= maxTransitions; step += 1) {
    const offersState = await ppFacebookDisableOffers(payload);
    if (!offersState.resolved) return { ready: false, error_code: offersState.error_code || "FACEBOOK_OFFER_STATE_UNRESOLVED", stage: "OFFER_PREFERENCE", offers_state: offersState, stages };
    const snapshot = ppFacebookStageSnapshot(step);
    snapshot.workflow_stage = ppFacebookWorkflowStage(snapshot);
    const fieldIntegrity = ppFacebookDetailsIntegrityEvidence(payload, `BEFORE_STAGE_${step}`);
    const descriptionEvidence = fieldIntegrity.description;
    stages.push({ phase: "DETAILS_FIELD_INTEGRITY", ...fieldIntegrity });
    stages.push({ phase: "DESCRIPTION_PERSISTENCE_CHECK", evidence: descriptionEvidence, step });
    if (!descriptionEvidence.matches) return {
      ready: false, error_code: descriptionEvidence.error_code || "FACEBOOK_DESCRIPTION_NOT_PERSISTED", stage: "DESCRIPTION_BEFORE_NEXT_OR_REVIEW",
      description_evidence: descriptionEvidence, stages,
    };
    if (snapshot.workflow_stage === "DETAILS" && (!fieldIntegrity.title.matches || !fieldIntegrity.price.matches)) return {
      ready: false, error_code: "FACEBOOK_DETAILS_FIELD_NOT_PERSISTED", stage: "DETAILS_FIELD_INTEGRITY",
      form_integrity: fieldIntegrity, stages,
    };
    const { node: _offerNode, ...offerEvidence } = offersState;
    snapshot.offers_state = offerEvidence;
    const signature = ppFacebookSnapshotSignature(snapshot);
    stages.push(snapshot);
    if (visited.has(signature)) return { ready: false, error_code: "FACEBOOK_CREATE_FLOW_CYCLE", stage: "SAFE_STAGE_TRANSITIONS", stages };
    visited.add(signature);
    if (snapshot.user_presence_challenge) return { ready: false, error_code: "FACEBOOK_USER_PRESENCE_CHALLENGE", stage: "USER_PRESENCE_CHALLENGE", stages };
    if (snapshot.final_publish_boundary) return { ready: true, stage: "FINAL_PUBLISH_BOUNDARY", stages };

    // Facebook can render the terminal Publish control disabled while its
    // post-Continue React state is still settling. Do not misclassify that
    // transient render as a missing safe continuation, and never activate a
    // disabled Publish control. Keep the existing foreground lease held while
    // waiting, then let the authorized submit path re-resolve the live control.
    if (snapshot.final_publish_control_present) {
      const publishWait = await ppWaitForFacebookFinalPublishBoundary(snapshot, step);
      stages.push({ phase: "FINAL_PUBLISH_STABILIZATION", ...publishWait });
      if (publishWait.ready) return { ready: true, stage: "FINAL_PUBLISH_BOUNDARY", stages };
      return {
        ready: false,
        error_code: publishWait.error_code || "FACEBOOK_FINAL_PUBLISH_NOT_READY",
        stage: publishWait.stage || "FINAL_PUBLISH_READINESS",
        final_publish_wait_ms: publishWait.wait_ms,
        final_publish_samples: publishWait.samples,
        validation_evidence: publishWait.snapshot?.validation_evidence || null,
        buttons: publishWait.snapshot?.buttons || snapshot.buttons,
        visible_headings: publishWait.snapshot?.headings || snapshot.headings,
        stages,
      };
    }

    const next = ppFacebookExactContinueControl();
    if (next && !next.disabled && next.getAttribute?.("aria-disabled") !== "true") {
      if (step >= maxTransitions) return { ready: false, error_code: "FACEBOOK_CREATE_FLOW_STEP_LIMIT", stage: "SAFE_STAGE_TRANSITIONS", max_transitions: maxTransitions, stages };
      const before = signature;
      const transitionStarted = Date.now();
      const controlLabel = String(next.innerText || next.value || next.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim();
      next.click();
      const deadline = Date.now() + 9000;
      let after = snapshot;
      while (Date.now() < deadline) {
        await ppWait(250);
        after = ppFacebookStageSnapshot(step + 1);
        if (ppFacebookSnapshotSignature(after) !== before) break;
      }
      stages.push({ ...after, transition: { control: controlLabel, wait_ms: Date.now() - transitionStarted, changed: ppFacebookSnapshotSignature(after) !== before } });
      if (ppFacebookSnapshotSignature(after) === before) return { ready: false, error_code: "FACEBOOK_CONTINUE_DID_NOT_ADVANCE", stage: "SAFE_STAGE_TRANSITIONS", stages };
      continue;
    }

    const shippingTrigger = Array.from(document.querySelectorAll('button,[role="button"],[role="combobox"],label'))
      .filter(ppVisible)
      .find((node) => /^select shipping label$/i.test(String(node.innerText || node.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim()));
    const deliveryText = snapshot.field_labels.map((item) => item.label || item.aria_label || "").join(" ").toLowerCase();
    const wantsShipping = String(payload.delivery_method || payload.shipping?.mode || "").toLowerCase().includes("ship")
      || String(payload.shipping?.domestic_service || "").trim();
    if (shippingTrigger && wantsShipping && !shippingSelectorAttempted) {
      shippingSelectorAttempted = true;
      shippingTrigger.click();
      const shippingWaitStarted = Date.now();
      let dialogConfig = null;
      // The Marketplace shipping dialog may mount only after the page has
      // completed its delivery-stage rerender. Attempt 30 showed a valid
      // Change shipping method dialog arriving after the old 3.5s cutoff; do
      // not fall through to the unrelated outer picker while that dialog is
      // still mounting. Keep the wait bounded, then continue through the
      // existing exact-control/option validation path.
      const dialogDeadline = shippingWaitStarted + 6500;
      while (Date.now() < dialogDeadline && !dialogConfig?.handled) {
        dialogConfig = await ppFacebookConfigureShippingDialog(payload, browserInputNonce, priorFormReadiness);
        if (dialogConfig.handled) break;
        await ppWait(150);
      }
      if (dialogConfig?.handled) {
        stages.push({ ...ppFacebookStageSnapshot(step), shipping_dialog_wait_ms: Date.now() - shippingWaitStarted, shipping_dialog: dialogConfig });
        if (!dialogConfig.ready) return { ready: false, error_code: dialogConfig.error_code, stage: dialogConfig.stage, stages };
        continue;
      }
      const { target } = ppFacebookShippingOptionCandidates(payload.shipping?.domestic_service);
      let candidates = [];
      let relevant = [];
      let optionsLoading = false;
      let shippingStage = ppFacebookStageSnapshot(step);
      const shippingDeadline = shippingWaitStarted + 9000;
      // Facebook's delivery picker is a portal that can mount asynchronously after
      // the trigger click. An absent dialog on the first microtask is not proof
      // that the service is unavailable; wait for the portal, then let its option
      // list settle before classifying the configured service as missing.
      let settledMenuPolls = 0;
      do {
        const resolved = ppFacebookShippingOptionCandidates(payload.shipping?.domestic_service);
        candidates = resolved.candidates;
        relevant = candidates.filter((row) => resolved.target && resolved.target.test(row.label));
        optionsLoading = resolved.loading;
        shippingStage = ppFacebookStageSnapshot(step);
        const menuPresent = resolved.menu_present;
        settledMenuPolls = menuPresent && !optionsLoading ? settledMenuPolls + 1 : 0;
        const elapsed = Date.now() - shippingWaitStarted;
        if (relevant.length || (menuPresent && elapsed >= 1000 && settledMenuPolls >= 2) || Date.now() >= shippingDeadline) break;
        await ppWait(250);
      } while (Date.now() < shippingDeadline);
      const shippingWaitMs = Date.now() - shippingWaitStarted;
      stages.push({ ...shippingStage, shipping_option_wait_ms: shippingWaitMs, shipping_options_loading: optionsLoading, delivery_menu_options: candidates.map((row) => row.label).slice(0, 24) });
      if (relevant.length === 1) {
        const chosen = relevant[0].node.closest?.('button,[role="button"],[role="option"],[role="radio"],[role="menuitem"],label') || relevant[0].node;
        chosen.click();
        await ppWait(500);
        const afterShipping = ppFacebookStageSnapshot(step);
        stages.push(afterShipping);
        const unresolvedPackageInputs = Array.from(document.querySelectorAll('input,select,[role="combobox"]'))
          .filter((node) => ppVisible(node) && !node.disabled)
          .filter((node) => /weight|length|width|height|package|parcel/i.test(`${node.getAttribute?.("aria-label") || ""} ${node.getAttribute?.("name") || ""} ${node.placeholder || ""} ${node.parentElement?.innerText || ""}`))
          .filter((node) => node.required || node.getAttribute?.("aria-required") === "true" || node.getAttribute?.("aria-invalid") === "true")
          .filter((node) => node.getAttribute?.("aria-invalid") === "true"
            || !String(node.value || node.getAttribute?.("aria-valuetext") || node.getAttribute?.("aria-valuenow") || node.getAttribute?.("data-value") || "").trim());
        if (unresolvedPackageInputs.length) return {
          ready: false, error_code: "FACEBOOK_PACKAGE_FIELDS_UNRESOLVED", stage: "SHIPPING_PACKAGE_DETAILS",
          fields: unresolvedPackageInputs.slice(0, 8).map((node) => ppSafeEvidenceText(node.getAttribute?.("aria-label") || node.getAttribute?.("name") || node.placeholder || node.parentElement?.innerText, 120)),
          shipping_source: { package_weight: payload.shipping.package_weight, package_dimensions: payload.shipping.package_dimensions, estimated: payload.shipping.estimated === true, estimated_fields: payload.shipping.estimated_fields || [], shipping_notes: payload.shipping.shipping_notes || null }, stages,
        };
        continue;
      }
      return {
        ready: false,
        error_code: relevant.length > 1 ? "FACEBOOK_SHIPPING_SERVICE_AMBIGUOUS"
          : target && optionsLoading ? "FACEBOOK_SHIPPING_OPTIONS_LOADING_TIMEOUT"
            : target ? "FACEBOOK_SHIPPING_SERVICE_NOT_RENDERED" : "FACEBOOK_SHIPPING_SERVICE_UNMAPPED",
        stage: "SHIPPING_LABEL_SELECTION", configured_service: payload.shipping?.domestic_service || null,
        shipping_option_wait_ms: shippingWaitMs, shipping_options_loading: optionsLoading, delivery_text: ppSafeEvidenceText(deliveryText, 200), delivery_menu_options: candidates.map((row) => row.label).slice(0, 24), stages,
      };
    }
    const visibleControls = snapshot.buttons.filter((item) => item.enabled).map((item) => item.label);
    return {
      ready: false,
      error_code: next ? "FACEBOOK_REQUIRED_DELIVERY_CONFIGURATION" : "FACEBOOK_SAFE_CONTINUE_NOT_AVAILABLE",
      stage: "SAFE_STAGE_TRANSITIONS", buttons: snapshot.buttons, visible_headings: snapshot.headings,
      delivery_text: ppSafeEvidenceText(deliveryText, 200), enabled_controls: visibleControls.slice(0, 16), stages,
    };
  }
  return { ready: false, error_code: "FACEBOOK_CREATE_FLOW_STEP_LIMIT", stage: "SAFE_STAGE_TRANSITIONS", max_transitions: maxTransitions, stages };
}

async function ppSubmitOperatorAuthorizedListing({ marketplace, payload = {}, browser_input_nonce = null, prior_form_readiness = null }) {
  const market = String(marketplace || "").toLowerCase();
  const bodyText = () => String(document.body?.innerText || "").replace(/\s+/g, " ").trim();
  if (market === "facebook") {
    ppActiveMarketplaceFocusLeaseId = null;
    try {
    const advance = await ppAdvanceFacebookToPublishBoundary(payload, browser_input_nonce, prior_form_readiness);
    const snapshot = ppFacebookStageSnapshot(advance.stages?.length || 0);
    if (!advance.ready) return { ok: false, submission_performed: false, error_code: advance.error_code || "FINAL_PUBLISH_BOUNDARY_NOT_REACHED", stage: advance.stage, diagnostic: snapshot, stage_history: advance.stages, configured_shipping: payload.shipping || null, page_url: ppSafePageUrl() };
    const finalDescriptionGuard = ppFacebookDescriptionSubmissionGuard(payload);
    const finalDescriptionEvidence = finalDescriptionGuard.evidence;
    if (!finalDescriptionGuard.allowed) return {
      ok: false, submission_performed: false, error_code: finalDescriptionGuard.error_code,
      stage: "DESCRIPTION_FINAL_PRE_SUBMIT", description_evidence: finalDescriptionEvidence,
      diagnostic: snapshot, stage_history: advance.stages, page_url: ppSafePageUrl(),
    };
    if (snapshot.user_presence_challenge) return { ok: false, submission_performed: false, error_code: "FACEBOOK_USER_PRESENCE_CHALLENGE", stage: "FINAL_PUBLISH_BOUNDARY", diagnostic: snapshot, page_url: ppSafePageUrl() };
    if (!snapshot.final_publish_boundary) return { ok: false, submission_performed: false, error_code: "FINAL_PUBLISH_BOUNDARY_NOT_REACHED", stage: "FINAL_PUBLISH_BOUNDARY", diagnostic: snapshot, page_url: ppSafePageUrl() };
    if (PP_FACEBOOK_UPDATE_COMMIT_DIAGNOSTIC_BUILD) return {
      ok: false, submission_performed: false, error_code: "FACEBOOK_UPDATE_DIAGNOSTIC_CAPTURE_COMPLETE",
      stage: "FINAL_PUBLISH_BOUNDARY_DIAGNOSTIC_ONLY", final_publish_boundary_reached: true,
      diagnostic: snapshot, stage_history: advance.stages, configured_shipping: payload.shipping || null,
      page_url: ppSafePageUrl(),
    };
    const offersState = await ppFacebookDisableOffers(payload);
    if (!offersState.resolved) return {
      ok: false, submission_performed: false, error_code: offersState.error_code || "FACEBOOK_OFFER_STATE_UNRESOLVED",
      stage: "FINAL_PRE_SUBMIT_VALIDATION", offers_state: (({ node: _offerNode, ...safe }) => safe)(offersState), diagnostic: snapshot, page_url: ppSafePageUrl(),
    };
    const minimumPriceResolution = ppFacebookResolveMinimumPriceControl(payload);
    if (!minimumPriceResolution.resolved) return {
      ok: false, submission_performed: false, error_code: minimumPriceResolution.error_code || "FACEBOOK_MINIMUM_PRICE_NOT_RESOLVED",
      stage: "FINAL_PRE_SUBMIT_VALIDATION", minimum_price_resolution: minimumPriceResolution,
      diagnostic: snapshot, page_url: ppSafePageUrl(),
    };
    const invalidControls = Array.from(document.querySelectorAll('[aria-invalid="true"], [aria-required="true"]'))
      .filter((node) => ppVisible(node) && node.getAttribute?.("aria-invalid") === "true" && !ppFacebookIsInactiveMinimumPriceControl(node))
      .slice(0, 12)
      .map((node) => ppSafeEvidenceText(node.getAttribute?.("aria-label") || node.getAttribute?.("name") || node.placeholder || node.parentElement?.innerText || "required field", 100));
    const validationAlerts = Array.from(document.querySelectorAll('[role="alert"]'))
      .filter(ppVisible)
      .map((node) => ppSafeEvidenceText(node.innerText || node.textContent, 160))
      .filter((text) => /required|choose|select|enter|missing|invalid/i.test(text || ""))
      .filter((text) => !ppFacebookIsInactiveMinimumPriceAlert(text, payload))
      .slice(0, 8);
    if (invalidControls.length || validationAlerts.length) return {
      ok: false, submission_performed: false, error_code: "FACEBOOK_REQUIRED_FIELDS_UNRESOLVED",
      stage: "FINAL_PRE_SUBMIT_VALIDATION", unresolved_fields: invalidControls,
      validation_alerts: validationAlerts, minimum_price_resolution: minimumPriceResolution, diagnostic: snapshot, page_url: ppSafePageUrl(),
    };
    const labels = new Set(["publish", "publish listing", "publish item", "list item", "list item for sale", "post listing"]);
    const submit = Array.from(document.querySelectorAll('button,[role="button"],input[type="submit"],input[type="button"]'))
      .filter((node) => ppVisible(node) && !node.disabled && node.getAttribute("aria-disabled") !== "true" && node.getAttribute("aria-hidden") !== "true")
      .find((node) => labels.has(String(node.innerText || node.value || node.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim().toLowerCase()));
    if (!submit) return { ok: false, submission_performed: false, error_code: "FACEBOOK_FINAL_PUBLISH_CONTROL_NOT_FOUND", stage: "FINAL_PUBLISH_BOUNDARY", diagnostic: snapshot, page_url: ppSafePageUrl() };
    const initialUrl = location.href;
    const preSubmitItemIds = new Set(Array.from(document.querySelectorAll("a[href]"), (anchor) => {
      try {
        const url = new URL(anchor.href, location.href);
        return url.hostname.toLowerCase() === "www.facebook.com"
          ? url.pathname.match(/^\/marketplace\/item\/([^/?#]+)/i)?.[1] || null
          : null;
      } catch (_) { return null; }
    }).filter(Boolean));
    submit.click();
    const successPattern = /listing (?:is )?(?:published|live)|your listing is live|item (?:is )?listed|published successfully|listing created|posted to marketplace/i;
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      await ppWait(400);
      const identity = ppFacebookSubmittedListingIdentity(payload, preSubmitItemIds);
      const successText = successPattern.test(bodyText());
      const directItemNavigation = location.href !== initialUrl && /^\/marketplace\/item\/[^/?#]+\/?$/i.test(location.pathname);
      if (identity && (successText || directItemNavigation || identity.identity_evidence === "NEW_POST_SUBMIT_ITEM_LINK_EXACT_TITLE_PRICE")) {
        return { ok: true, submission_performed: true, confirmation: "FACEBOOK_LISTING_IDENTITY_CAPTURED", ...identity, page_url: ppSafePageUrl(), configured_location: "operator_confirmed_location", description_verification: finalDescriptionEvidence };
      }
      if (/captcha|security check|checkpoint|confirm you are human/i.test(bodyText())) {
        return { ok: false, submission_performed: true, error_code: "SUBMISSION_OUTCOME_UNKNOWN", stage: "POST_SUBMIT_USER_PRESENCE_CHALLENGE", confirmation: "CHALLENGE_AFTER_FINAL_SUBMIT", page_url: ppSafePageUrl(), description_verification: finalDescriptionEvidence };
      }
    }
    return { ok: false, submission_performed: true, error_code: "SUBMISSION_OUTCOME_UNKNOWN", confirmation: "NO_VERIFIABLE_POST_SUBMIT_STATE", page_url: ppSafePageUrl(), description_verification: finalDescriptionEvidence };
    } finally {
      const leaseId = ppActiveMarketplaceFocusLeaseId;
      ppActiveMarketplaceFocusLeaseId = null;
      if (leaseId) await ppReleaseMarketplaceFocusLease(leaseId);
    }
  }
  if (market !== "mercari") return { ok: false, submission_performed: false, error_code: "FINAL_SUBMIT_NOT_VERIFIED_FOR_MARKETPLACE" };
  const successPattern = /your item (?:is )?listed|listing (?:is )?published|successfully listed|item listed successfully/i;
  const initialUrl = location.href;
  const submitLabels = new Set(["list", "list item", "publish", "publish listing", "list item for sale"]);
  const submit = Array.from(document.querySelectorAll('button,[role="button"],input[type="submit"],input[type="button"]'))
    .filter((node) => !node.disabled && node.getAttribute("aria-disabled") !== "true" && node.getAttribute("aria-hidden") !== "true")
    .find((node) => submitLabels.has(String(node.innerText || node.value || node.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim().toLowerCase()));
  if (!submit) return { ok: false, submission_performed: false, error_code: "FINAL_SUBMIT_CONTROL_NOT_FOUND" };
  if (submit.disabled || submit.getAttribute("aria-disabled") === "true") return { ok: false, submission_performed: false, error_code: "FINAL_SUBMIT_CONTROL_DISABLED" };
  submit.click();
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    await ppWait(400);
    const identity = ppMarketplaceListingIdentity(market);
    const confirmed = successPattern.test(bodyText()) || Boolean(identity && location.href !== initialUrl);
    if (confirmed && identity) return { ok: true, submission_performed: true, confirmation: "LISTING_SUCCESS_AND_IDENTITY", ...identity, page_url: ppSafePageUrl() };
    if (confirmed) return { ok: false, submission_performed: true, error_code: "SUBMISSION_OUTCOME_UNKNOWN", confirmation: "SUCCESS_TEXT_WITHOUT_LISTING_IDENTITY", page_url: ppSafePageUrl() };
  }
  return { ok: false, submission_performed: true, error_code: "SUBMISSION_OUTCOME_UNKNOWN", confirmation: "NO_VERIFIABLE_POST_SUBMIT_STATE", page_url: ppSafePageUrl() };
}

async function ppWaitForEditableForm(adapter, timeoutMs = 12000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const facebook = location.hostname === "www.facebook.com" && /^\/marketplace\/create\//i.test(location.pathname);
    const title = facebook ? ppFacebookDetailsControl("title") : ppFindField(adapter.title, ["title"]);
    const price = facebook ? ppFacebookDetailsControl("price") : ppFindField(adapter.price, ["price"]);
    const description = facebook ? ppFacebookDescriptionControl() : ppFindField(adapter.description, ["description"]);
    if (title && price && description) return true;
    await ppWait(250);
  }
  return false;
}

function ppFacebookDescriptionRepairSaveTarget() {
  const scopes = Array.from(document.querySelectorAll("form,[role='dialog'],main,[role='main']")).filter(ppVisible);
  const scope = scopes.find((node) => node.querySelector?.('[aria-label*="Description" i],textarea,[contenteditable="true"]')) || null;
  if (!scope) return null;
  const candidates = Array.from(scope.querySelectorAll('button,[role="button"],input[type="submit"],input[type="button"]'))
    .filter((node) => ppVisible(node) && !node.disabled && node.getAttribute?.("aria-disabled") !== "true")
    .map((node) => ({ node, label: String(node.innerText || node.value || node.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim() }))
    .filter((item) => /^(save|update|save changes|update listing)$/i.test(item.label));
  if (candidates.length !== 1) return null;
  const target = candidates[0].node;
  const rect = target.getBoundingClientRect?.();
  const x = rect ? Math.round(Number(rect.left) + Number(rect.width) / 2) : -1;
  const y = rect ? Math.round(Number(rect.top) + Number(rect.height) / 2) : -1;
  let hit = null;
  try { hit = document.elementFromPoint?.(x, y) || null; } catch (_) { hit = null; }
  return {
    target, scope, label: candidates[0].label, x, y,
    left: Number(rect?.left), top: Number(rect?.top), width: Number(rect?.width), height: Number(rect?.height),
    viewport_width: Number(globalThis.innerWidth), viewport_height: Number(globalThis.innerHeight),
    hit_target_inside: Boolean(hit && (hit === target || target.contains?.(hit))),
    repair_form_identity_verified: true,
  };
}

function ppFacebookDescriptionSaveEvidence(target, expectedIdentity = {}) {
  const resolved = ppFacebookDescriptionRepairSaveTarget();
  const found = resolved?.target || null;
  if (!resolved || !found || (target && found !== target)) return { ok: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_SAVE_TARGET_AMBIGUOUS" };
  const adapter = globalThis.POSTERPRO_MARKETPLACE_ADAPTERS?.facebook;
  const formIdentity = ppFacebookDescriptionControlSnapshot(adapter);
  const expectedTitle = String(expectedIdentity.title || "").replace(/\s+/g, " ").trim();
  const expectedPrice = Number(expectedIdentity.price);
  const observedPrice = Number(String(formIdentity.price || "").replace(/[^0-9.]/g, ""));
  const identityVerified = Boolean(expectedTitle && formIdentity.title?.toLocaleLowerCase() === expectedTitle.toLocaleLowerCase()
    && Number.isFinite(expectedPrice) && observedPrice === expectedPrice);
  const evidence = {
    label: resolved.label, role: "button", dialog_heading: null,
    x: resolved.x, y: resolved.y, left: resolved.left, top: resolved.top,
    width: resolved.width, height: resolved.height,
    viewport_width: resolved.viewport_width, viewport_height: resolved.viewport_height,
    hit_target_inside: resolved.hit_target_inside,
    is_connected: Boolean(found.isConnected), document_contains: Boolean(document.contains(found)),
    current_dialog_match: identityVerified, repair_form_identity_verified: identityVerified,
    enabled: !found.disabled && found.getAttribute?.("aria-disabled") !== "true",
    evidence_source: "content-script-live-target",
    content_script_build_id: ppContentScriptIdentity().content_script_build_id,
  };
  if (!identityVerified) return { ok: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_IDENTITY_AMBIGUOUS", target: evidence, form_identity: formIdentity };
  if (!evidence.hit_target_inside || !evidence.is_connected || !evidence.document_contains || !evidence.enabled) {
    return { ok: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_SAVE_TARGET_INVALID", target: evidence, form_identity: formIdentity };
  }
  return { ok: true, target: evidence, target_node: found, form_identity: formIdentity };
}

function ppFacebookDescriptionControlSnapshot(adapter) {
  const read = (selectors, labels, field = null) => {
    const node = field === "description" ? ppFacebookDescriptionControl()
      : field === "location" ? ppFacebookLocationControlResolution().node : ppFindField(selectors, labels);
    return node ? String(node.isContentEditable ? node.innerText || node.textContent || "" : node.value ?? node.textContent ?? "").replace(/\s+/g, " ").trim() : null;
  };
  const offerControls = Array.from(document.querySelectorAll('input[type="checkbox"],[role="switch"],[role="checkbox"]'))
    .filter(ppVisible)
    .map((node) => {
      const context = String(node.closest?.("label")?.innerText || node.parentElement?.innerText || node.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim();
      return /offer|negotiable/i.test(context) ? {
        label: ppSafeEvidenceText(context, 80), checked: Boolean(node.checked), aria_checked: node.getAttribute?.("aria-checked") || null,
      } : null;
    }).filter(Boolean);
  return {
    title: read(adapter?.title, ["title"]),
    price: read(adapter?.price, ["price"]),
    description: read(adapter?.description, ["description"], "description"),
    category: read(adapter?.category, ["category"]),
    condition: read(adapter?.condition, ["condition"]),
    location: read(adapter?.location, ["location", "ZIP code", "postal code"], "location"),
    availability: read(adapter?.availability, ["availability"]),
    shipping: read(adapter?.shipping, ["shipping", "delivery"]),
    offer_controls: offerControls,
    image_count: Array.from(document.querySelectorAll('img,[role="img"],[data-testid*="photo" i]')).filter(ppVisible).length,
  };
}

async function ppFacebookDescriptionOnlyRepair(message) {
  const payload = message.payload || {};
  const expectedId = String(message.external_listing_id || payload.repair_expected_remote_id || "");
  const expectedUrl = String(message.expected_external_url || payload.repair_expected_remote_url || "");
  const description = String(payload.description || "");
  if (String(message.marketplace || "").toLowerCase() !== "facebook" || payload.repair_scope !== "facebook_description_only"
    || !expectedId || !/^https:\/\/www\.facebook\.com\/marketplace\/item\/[^/]+\/?$/i.test(expectedUrl)
    || new URL(expectedUrl).pathname.match(/^\/marketplace\/item\/([^/]+)/i)?.[1] !== expectedId
    || !description || !message.browser_input_nonce) {
    return { ok: false, action: "UPDATE", identity_verified: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_IDENTITY_AMBIGUOUS", submission_performed: false };
  }
  if (!ppIdentityMatches(expectedUrl) || !/^\/marketplace\/item\//i.test(location.pathname)) {
    return { ok: false, action: "UPDATE", identity_verified: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_IDENTITY_AMBIGUOUS", submission_performed: false };
  }
  const adapter = globalThis.POSTERPRO_MARKETPLACE_ADAPTERS?.facebook;
  const remoteBefore = await ppFacebookReconcileSellerListing({ expected_title: payload.repair_expected_title || payload.title, expected_price: payload.repair_expected_price ?? payload.price, expected_description: "" });
  const edit = ppFindListingAction(["edit listing", "edit item"]);
  if (remoteBefore.state !== "FOUND" || remoteBefore.external_listing_id !== expectedId
    || !remoteBefore.exact_title_match || !remoteBefore.exact_price_match || !edit) {
    return { ok: false, action: "UPDATE", identity_verified: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_IDENTITY_AMBIGUOUS", remote_before: remoteBefore, submission_performed: false };
  }
  edit.click?.();
  if (!await ppWaitForEditableForm(adapter)) return { ok: false, action: "UPDATE", identity_verified: true, error_code: "FACEBOOK_DESCRIPTION_REPAIR_EDIT_FORM_MISSING", submission_performed: false };
  if (!String(location.pathname || "").includes(expectedId)) {
    return { ok: false, action: "UPDATE", identity_verified: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_IDENTITY_AMBIGUOUS", page_url: ppSafePageUrl(), submission_performed: false };
  }
  const before = ppFacebookDescriptionControlSnapshot(adapter);
  const expectedTitle = String(payload.repair_expected_title || payload.title || "").replace(/\s+/g, " ").trim();
  const expectedPrice = Number(payload.repair_expected_price ?? payload.price);
  const actualTitle = before.title;
  const actualPrice = Number(String(before.price || "").replace(/[^0-9.]/g, ""));
  if (!actualTitle || actualTitle.toLocaleLowerCase() !== expectedTitle.toLocaleLowerCase()
    || !Number.isFinite(expectedPrice) || actualPrice !== expectedPrice) {
    return { ok: false, action: "UPDATE", identity_verified: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_IDENTITY_AMBIGUOUS", before, submission_performed: false };
  }
  const filled = await ppFacebookSetAndVerifyDescription(adapter, description, "FACEBOOK_DESCRIPTION_REPAIR_AFTER_FILL");
  if (!filled.ok) return { ok: false, action: "UPDATE", identity_verified: true, error_code: filled.error_code, description_verification: filled.evidence, submission_performed: false };
  const save = ppFacebookDescriptionSaveEvidence(null, { title: expectedTitle, price: expectedPrice });
  if (!save.ok) return { ok: false, action: "UPDATE", identity_verified: true, error_code: save.error_code, save_target: save.target || null, submission_performed: false };
  const beforeSave = ppFacebookDescriptionControlSnapshot(adapter);
  const unchangedFields = ["title", "price", "category", "condition", "location", "availability", "shipping", "offer_controls", "image_count"];
  const changedFields = unchangedFields.filter((field) => JSON.stringify(beforeSave[field]) !== JSON.stringify(before[field]));
  if (changedFields.length) {
    return { ok: false, action: "UPDATE", identity_verified: true, error_code: "FACEBOOK_DESCRIPTION_REPAIR_NON_DESCRIPTION_STATE_CHANGED", changed_fields: changedFields, before, before_save: beforeSave, submission_performed: false };
  }
  const begun = await chrome.runtime.sendMessage({
    action: "posterpro_begin_facebook_description_repair_save",
    authorization_nonce: message.browser_input_nonce,
    external_listing_id: expectedId,
    external_url: expectedUrl,
  });
  if (!begun?.ok) return { ok: false, action: "UPDATE", identity_verified: true, error_code: begun?.error_code || "FACEBOOK_DESCRIPTION_REPAIR_SAVE_NOT_AUTHORIZED", submission_performed: false };
  const observedEvents = [];
  const seenEvents = new WeakMap();
  const scopes = [
    ["window", globalThis], ["document", document], ["edit_scope", save.target_node?.closest?.("form,[role='dialog'],main,[role='main']") || save.target_node?.parentElement],
    ["save_control", save.target_node],
  ];
  const types = ["pointerdown", "mousedown", "pointerup", "mouseup", "click"];
  const listeners = [];
  for (const [scopeName, scopeNode] of scopes) {
    if (!scopeNode?.addEventListener) continue;
    for (const type of types) {
      const listener = (event) => {
        let evidence = seenEvents.get(event);
        if (!evidence) {
          const eventTarget = event.target;
          evidence = {
            type, isTrusted: Boolean(event.isTrusted),
            target: { tag: String(eventTarget?.tagName || "").toLowerCase(), role: eventTarget?.getAttribute?.("role") || null, label: ppSafeEvidenceText(eventTarget?.innerText || eventTarget?.textContent || eventTarget?.getAttribute?.("aria-label") || "", 80) },
            clientX: Number.isFinite(event.clientX) ? event.clientX : null,
            clientY: Number.isFinite(event.clientY) ? event.clientY : null,
            targetIsSaveControl: eventTarget === save.target_node || Boolean(save.target_node?.contains?.(eventTarget)),
            defaultPrevented: Boolean(event.defaultPrevented), scopes: [],
          };
          seenEvents.set(event, evidence); observedEvents.push(evidence);
        }
        if (!evidence.scopes.includes(scopeName)) evidence.scopes.push(scopeName);
      };
      scopeNode.addEventListener(type, listener, true);
      listeners.push([scopeNode, type, listener]);
    }
  }
  const activation = await chrome.runtime.sendMessage({
    action: "posterpro_marketplace_control_activation", marketplace: "facebook",
    expected_stage: "FACEBOOK_DESCRIPTION_REPAIR_SAVE",
    expected_transition: "DESCRIPTION_REPAIR_SAVE_ACCEPTED",
    expected_origin: "https://www.facebook.com",
    authorization_nonce: message.browser_input_nonce,
    target: save.target,
    expected_identity: { title: expectedTitle, price: expectedPrice },
  });
  await ppWait(180);
  for (const [scopeNode, type, listener] of listeners) scopeNode.removeEventListener?.(type, listener, true);
  if (activation?.browser_context?.focus_lease_id) {
    await chrome.runtime.sendMessage({
      action: "posterpro_release_marketplace_focus_lease",
      focus_lease_id: activation.browser_context.focus_lease_id,
    }).catch(() => {});
  }
  const trustedSaveEvents = observedEvents.filter((event) => event.isTrusted && event.targetIsSaveControl && event.clientX === activation?.activation_coordinate?.x && event.clientY === activation?.activation_coordinate?.y);
  if (!activation?.ok || !trustedSaveEvents.some((event) => ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].includes(event.type))) {
    return { ok: false, action: "UPDATE", identity_verified: true, error_code: activation?.error_code || "FACEBOOK_DESCRIPTION_REPAIR_SAVE_DELIVERY_UNPROVEN", activation, save_activation_count: activation?.invocation_count === 1 ? 1 : 0, observed_events: observedEvents, description_verification: filled.evidence, before, before_save: beforeSave, submission_performed: Boolean(activation?.invocation_count === 1) };
  }
  return {
    ok: true, action: "UPDATE", identity_verified: true,
    external_listing_id: expectedId, external_url: expectedUrl,
    description_only: true, save_activation_count: activation.invocation_count,
    save_activation: activation, description_verification: filled.evidence,
    trusted_save_events: observedEvents,
    before, before_save: beforeSave, title_unchanged: true, price_unchanged: true,
    submission_performed: true,
  };
}

async function ppUpdateExactListing(message) {
  if (message.description_repair_only === true) return ppFacebookDescriptionOnlyRepair(message);
  const marketplace = String(message.marketplace || "").toLowerCase();
  const adapter = (globalThis.POSTERPRO_MARKETPLACE_ADAPTERS || {})[marketplace];
  const expectedUrl = String(message.expected_external_url || message.payload?.external_url || "");
  if (!adapter || !ppIdentityMatches(expectedUrl)) {
    return { ok: false, action: "UPDATE", identity_verified: false, error_code: "EXTERNAL_IDENTITY_MISMATCH", page_url: ppSafePageUrl(), submission_performed: false };
  }
  const existingForm = await ppWaitForEditableForm(adapter, 500);
  if (!existingForm) {
    const edit = ppFindListingAction(["edit listing", "edit item"]);
    if (!edit) return { ok: false, action: "UPDATE", identity_verified: true, error_code: "EDIT_CONTROL_NOT_FOUND", page_url: ppSafePageUrl(), submission_performed: false };
    edit.click?.();
    if (!await ppWaitForEditableForm(adapter)) return { ok: false, action: "UPDATE", identity_verified: true, error_code: "EDIT_FORM_NOT_AVAILABLE", page_url: ppSafePageUrl(), submission_performed: false };
  }
  const result = await ppFillListing({ marketplace, payload: { ...message.payload, action: "UPDATE" }, images: message.images || [] });
  return {
    ...result,
    action: "UPDATE",
    identity_verified: true,
    external_listing_id: message.external_listing_id || null,
    external_url: expectedUrl,
    operator_review_required: true,
    submission_performed: false,
  };
}

function ppInspectExactEnd(message) {
  const marketplace = String(message.marketplace || "").toLowerCase();
  const expectedUrl = String(message.expected_external_url || message.payload?.external_url || "");
  if (!(globalThis.POSTERPRO_MARKETPLACE_ADAPTERS || {})[marketplace] || !ppIdentityMatches(expectedUrl)) {
    return { ok: false, action: "END", identity_verified: false, error_code: "EXTERNAL_IDENTITY_MISMATCH", page_url: ppSafePageUrl(), submission_performed: false };
  }
  const alreadyEnded = /listing (is )?(inactive|ended|deleted|removed)|no longer available|this item has been removed/.test(String(document.body?.innerText || "").slice(0, 3000).toLowerCase());
  const endControl = ppFindListingAction(["mark as sold", "delete listing", "deactivate listing", "end listing", "remove listing", "close listing", "mark unavailable"]);
  return {
    ok: true,
    action: "END",
    identity_verified: true,
    external_listing_id: message.external_listing_id || null,
    external_url: expectedUrl,
    page_url: ppSafePageUrl(),
    page_state: alreadyEnded ? "ALREADY_INACTIVE_POSSIBLE" : endControl ? "END_CONTROL_FOUND" : "END_CONTROL_NOT_FOUND",
    end_control_detected: Boolean(endControl),
    required_operator_action: alreadyEnded ? "VERIFY_INACTIVE_STATE_IN_POSTERPRO" : endControl ? "USE_VISIBLE_MARKETPLACE_END_CONTROL_THEN_CONFIRM_IN_POSTERPRO" : "FIND_MARKETPLACE_END_CONTROL_MANUALLY_THEN_CONFIRM_IN_POSTERPRO",
    submission_performed: false,
    operator_review_required: true,
  };
}

function ppFacebookResolveDescriptionSaveTarget(message) {
  if (message?.expected_stage !== "FACEBOOK_DESCRIPTION_REPAIR_SAVE"
    || message?.expected_content_build_id !== ppContentScriptIdentity().content_script_build_id
    || !message?.authorization_nonce) return { ok: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_SAVE_AUTH_INVALID" };
  const result = ppFacebookDescriptionSaveEvidence(null, message.expected_identity || {});
  if (!result.ok) return result;
  const target = { ...result.target };
  delete target.target_node;
  return { ok: true, target, form_identity: result.form_identity };
}

function ppFacebookValidateDescriptionSavePoint(message) {
  if (message?.expected_stage !== "FACEBOOK_DESCRIPTION_REPAIR_SAVE"
    || message?.expected_content_build_id !== ppContentScriptIdentity().content_script_build_id
    || !message?.authorization_nonce) return { ok: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_SAVE_AUTH_INVALID" };
  const coordinate = message.activation_coordinate || {};
  if (!Number.isInteger(coordinate.x) || !Number.isInteger(coordinate.y)) return { ok: false, error_code: "FACEBOOK_DESCRIPTION_REPAIR_COORDINATE_INVALID" };
  const evidence = ppFacebookDescriptionSaveEvidence(null, message.expected_identity || {});
  if (!evidence.ok) return evidence;
  const target = evidence.target_node;
  let hit = null;
  try { hit = document.elementFromPoint(coordinate.x, coordinate.y); } catch (_) { /* fail closed below */ }
  const inside = Boolean(hit && (hit === target || target.contains?.(hit)));
  const rect = target.getBoundingClientRect?.();
  const inRect = Boolean(rect && coordinate.x >= rect.left && coordinate.x <= rect.right && coordinate.y >= rect.top && coordinate.y <= rect.bottom);
  return {
    ok: inside && inRect,
    error_code: inside && inRect ? null : "FACEBOOK_DESCRIPTION_REPAIR_HIT_TEST_FAILED",
    target_inside_control: inside && inRect,
    repair_form_identity_verified: true,
    activation_coordinate: { x: coordinate.x, y: coordinate.y },
    hit_target: hit ? { tag: String(hit.tagName || "").toLowerCase(), role: hit.getAttribute?.("role") || null, text: ppSafeEvidenceText(hit.innerText || hit.textContent || "", 80) } : null,
  };
}

var previousPosterProListener = globalThis.__POSTERPRO_CONTENT_MESSAGE_HANDLER;
if (previousPosterProListener) chrome.runtime.onMessage.removeListener?.(previousPosterProListener);
var currentPosterProListener = (message, _sender, sendResponse) => {
  if (message?.action === "posterpro_probe_facebook_create_context_read_only") {
    ppProbeFacebookCreateContextReadOnly().then(sendResponse).catch((error) => sendResponse({
      ...ppContentScriptIdentity(), ok: false, read_only: true,
      error_code: error?.code || "FACEBOOK_CONTEXT_PROBE_FAILED",
      error: ppSafeEvidenceText(String(error?.message || error), 180), page_url: ppSafePageUrl(),
    }));
    return true;
  }
  if (message?.action === "posterpro_inspect_facebook_create_baseline") {
    ppInspectFacebookCreateBaseline().then(sendResponse).catch((error) => sendResponse({
      ...ppContentScriptIdentity(), ok: false, clean: false,
      error_code: error?.code || "FACEBOOK_FORM_READY_TIMEOUT",
      error: String(error?.message || error).slice(0, 180), page_url: ppSafePageUrl(),
    }));
    return true;
  }
  if (message?.action === "posterpro_content_script_identity") {
    sendResponse(ppContentScriptIdentity());
    return false;
  }
  if (message?.action === "posterpro_probe_facebook_description_dom") {
    const allowedPage = location.origin === "https://www.facebook.com" && /^\/marketplace\/create(?:\/|$)/i.test(location.pathname);
    if (!allowedPage) {
      sendResponse({ ok: false, error_code: "FACEBOOK_DESCRIPTION_PROBE_WRONG_PAGE", page: { origin: location.origin, path: location.pathname } });
      return false;
    }
    sendResponse({ ok: true, read_only: true, content_script_build_id: ppContentScriptIdentity().content_script_build_id,
      probe: ppFacebookDescriptionDomProbe() });
    return false;
  }
  if (message?.action === "posterpro_resolve_facebook_update_target_after_foreground") {
    ppFacebookResolveUpdateAfterForeground(message).then(sendResponse).catch((error) => sendResponse({
      ok: false, error_code: "FACEBOOK_POST_FOREGROUND_RESOLUTION_FAILED",
      error: ppSafeEvidenceText(String(error?.message || error), 180),
    }));
    return true;
  }
  if (message?.action === "posterpro_validate_facebook_update_activation_point") {
    sendResponse(ppFacebookValidateUpdateActivationPoint(message));
    return false;
  }
  if (message?.action === "posterpro_resolve_facebook_description_save_after_foreground") {
    sendResponse(ppFacebookResolveDescriptionSaveTarget(message));
    return false;
  }
  if (message?.action === "posterpro_validate_facebook_description_save_activation_point") {
    sendResponse(ppFacebookValidateDescriptionSavePoint(message));
    return false;
  }
  if (message?.action === "posterpro_reconcile_facebook_seller_listing") {
    ppFacebookReconcileSellerListing(message).then(sendResponse).catch((error) => sendResponse({
      state: "AMBIGUOUS", error_code: "FACEBOOK_RECONCILIATION_READ_FAILED",
      error: ppSafeEvidenceText(String(error?.message || error), 180), page_url: ppSafePageUrl(),
    }));
    return true;
  }
  if (message?.action === "posterpro_submit_operator_authorized_listing") {
    ppSubmitOperatorAuthorizedListing(message).then(sendResponse).catch((error) => sendResponse({ ok: false, submission_performed: false, error_code: "SUBMIT_ADAPTER_FAILED", error: String(error?.message || error).slice(0, 240) }));
    return true;
  }
  if (message?.action === "posterpro_update_exact_listing") {
    ppUpdateExactListing(message).then(sendResponse).catch((error) => sendResponse({ ok: false, action: "UPDATE", identity_verified: false, error_code: "UPDATE_ADAPTER_FAILED", error: String(error?.message || error).slice(0, 200), submission_performed: false }));
    return true;
  }
  if (message?.action === "posterpro_inspect_exact_end") {
    sendResponse(ppInspectExactEnd(message));
    return false;
  }
  if (message?.action === "posterpro_cancel_facebook_form_fill") {
    if (!ppFacebookActiveFormRun || Number(ppFacebookActiveFormRun.jobId) !== Number(message.internal_job_id)) {
      sendResponse({ cancelled: false, error_code: "FACEBOOK_FORM_CANCEL_JOB_MISMATCH" });
      return false;
    }
    ppFacebookActiveFormRun.cancelCode = String(message.reason || "FACEBOOK_FORM_FILL_CANCELLED").slice(0, 100);
    ppFacebookActiveFormRun.cancelled = true;
    ppFacebookActiveFormRun.cancelResolve?.();
    sendResponse({ cancelled: true, job_id: ppFacebookActiveFormRun.jobId, cancel_code: ppFacebookActiveFormRun.cancelCode });
    return false;
  }
  if (message?.action !== "posterpro_fill_listing" && message?.action !== "posterpro_diagnose_listing") return false;
  const diagnostic = message.action === "posterpro_diagnose_listing";
  if (!diagnostic && String(message.marketplace || "").toLowerCase() === "facebook") {
    if (ppFacebookActiveFormRun) {
      sendResponse({ ok: false, error_code: "FACEBOOK_FORM_ALREADY_ACTIVE", submission_performed: false });
      return false;
    }
    let cancelResolve;
    const cancelSignal = new Promise((resolve) => { cancelResolve = resolve; });
    ppFacebookActiveFormRun = { jobId: Number(message.internal_job_id), cancelled: false, cancelSignal, cancelResolve, cancelCode: null };
  }
  const activeRun = ppFacebookActiveFormRun;
  (diagnostic ? ppRunDiagnostic(message) : ppFillListing({ ...message, diagnostic })).then(sendResponse).catch((error) => sendResponse({
    ok: false, error_code: error?.code || "FORM_FILL_FAILED", error: String(error?.message || error).slice(0, 300),
    page_url: location.origin + location.pathname, submission_performed: false,
  })).finally(() => {
    if (activeRun && ppFacebookActiveFormRun === activeRun) ppFacebookActiveFormRun = null;
  });
  return true;
};
globalThis.__POSTERPRO_CONTENT_MESSAGE_HANDLER = currentPosterProListener;
chrome.runtime.onMessage.addListener(currentPosterProListener);
