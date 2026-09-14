function ppFindField(selectors, labelPatterns) {
  for (const selector of selectors || []) {
    const node = document.querySelector(selector);
    if (node && !node.disabled && node.getAttribute("aria-hidden") !== "true") return node;
  }
  for (const label of document.querySelectorAll("label")) {
    const text = String(label.innerText || label.getAttribute("aria-label") || "").trim().toLowerCase();
    if ((labelPatterns || []).some((pattern) => text.includes(pattern))) {
      const control = label.control || label.querySelector("input,textarea,select,[role=combobox],button");
      if (control && !control.disabled) return control;
    }
  }
  for (const node of document.querySelectorAll("input,textarea,select,[contenteditable='true'],[role='combobox'],button[aria-label]")) {
    const text = `${node.getAttribute("aria-label") || ""} ${node.getAttribute("placeholder") || ""} ${node.getAttribute("name") || ""}`.toLowerCase();
    if ((labelPatterns || []).some((pattern) => text.includes(pattern)) && !node.disabled) return node;
  }
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
  return {
    field,
    page_path: location.pathname.split("/").map((part) => (/^\d+$/.test(part) || part.length > 48 ? ":id" : part)).join("/").slice(0, 300),
    selectors_tried: (selectors || []).slice(0, 8).map(String),
    controls: controls.map((control) => ppControlStructure(control, field)).filter(Boolean),
    expected_label_hints: safeLabels,
  };
}

const ppWait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function ppOptionAliases(marketplace, field, value) {
  const text = String(value || "").trim().toLowerCase().replace(/[_-]+/g, " ");
  if (field === "category") {
    if (/clothing|apparel|shirt|dress|jacket|fashion|shoe|footwear/.test(text)) return ["clothing", "clothing & accessories", "clothing and accessories", "apparel", "fashion", "women's clothing", "men's clothing"];
    if (/electronic|computer|phone|camera|audio/.test(text)) return ["electronics", "computers & tablets", "cell phones", "electronics & media"];
    if (/home|garden|furniture|kitchen/.test(text)) return ["home", "home & garden", "home and garden", "home goods", "furniture"];
    if (/toy|game|collectible|card/.test(text)) return ["toys & games", "toys and games", "collectibles", "trading cards"];
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
    if (text.includes("standard")) return ["standard", "ship", "shipping", "ship with mercari", "buyer pays"];
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

async function ppSelectOption(node, value, marketplace, field) {
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
  node.click?.();
  await ppWait(250);
  const aliases = ppOptionAliases(marketplace, field, value);
  const options = Array.from(document.querySelectorAll('[role="option"], [role="menuitem"], option, li, button'))
    .filter((option) => option.getAttribute("aria-hidden") !== "true" && option.offsetParent !== null);
  const exact = options.find((option) => aliases.includes(String(option.innerText || option.textContent || "").trim().toLowerCase()));
  const contains = exact || options.find((option) => aliases.some((alias) => alias && String(option.innerText || option.textContent || "").trim().toLowerCase().includes(alias)));
  if (!contains) return { ok: false, error_code: "DESTINATION_OPTION_NOT_FOUND" };
  contains.click?.();
  await ppWait(100);
  return { ok: true, value: String(contains.innerText || contains.textContent || value).trim().slice(0, 160) };
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
  if (/marketplace\/create|sell\/new|listing\/create|items\/new/.test(url)) return "LOGGED_IN";
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

async function ppFillListing({ marketplace, payload = {}, images = [], diagnostic = false }) {
  const adapters = globalThis.POSTERPRO_MARKETPLACE_ADAPTERS || {};
  const market = String(marketplace || "").toLowerCase();
  const adapter = adapters[market];
  if (!adapter) return { ok: false, error_code: "ADAPTER_NOT_IMPLEMENTED", error: `No field adapter is available for ${marketplace}.` };
  if (!adapter.hosts.some((host) => location.hostname === host || location.hostname.endsWith(`.${host}`))) return { ok: false, error_code: "UNEXPECTED_MARKETPLACE_DOMAIN", error: "The active page does not match the requested marketplace." };

  const login_state = ppDetectLoginState(market);
  if (["LOGIN_REQUIRED", "CAPTCHA_REQUIRED", "CHECKPOINT_REQUIRED", "ACCOUNT_RESTRICTION", "UNKNOWN"].includes(login_state)) {
    return { ok: false, stage: login_state, login_state, page_state: login_state, error_code: login_state, populated_fields: [], field_results: [], missing_required_fields: [], submission_performed: false };
  }
  const populated_fields = [];
  const missing_required_fields = [];
  const field_results = [];
  const record = (field, required, detected, attempted, filled, value, error_code = null, node = null, selectors = [], labels = []) => field_results.push({
    field, required, detected, attempted, filled,
    verified_value: filled ? String(value ?? "").slice(0, 160) : null,
    error_code,
    ...(filled ? {} : { selector_diagnostic: ppSelectorDiagnostic(field, node, selectors, labels) }),
  });
  const fields = [["title", adapter.title, ["title"]], ["price", adapter.price, ["price"]], ["description", adapter.description, ["description", "describe"]]];
  for (const [name, selectors, labels] of fields) {
    const node = ppFindField(selectors, labels);
    const value = name === "price" ? payload[name] : payload[name];
    const filled = ppSetValue(node, value);
    if (filled) populated_fields.push(name); else missing_required_fields.push(name);
    record(name, true, Boolean(node), true, filled, value, filled ? null : (node ? "VALUE_NOT_ACCEPTED" : "FIELD_NOT_FOUND"), node, selectors, labels);
  }

  const diagnosticValues = {
    category: payload.marketplace_category || payload.category,
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
    if (!required && value == null) continue;
    const node = ppFindField(selectors, [name, ...(name === "shipping" ? ["delivery", "parcel"] : [])]);
    const selected = await ppSelectOption(node, value, market, name);
    if (selected.ok) populated_fields.push(name); else if (required) missing_required_fields.push(name);
    record(name, required, Boolean(node), true, selected.ok, selected.value || value, selected.error_code || null, node, selectors, [name, ...(name === "shipping" ? ["delivery", "parcel"] : [])]);
  }

  const diagnosticOptionalValues = diagnostic ? {
    brand: payload.brand || "PosterPro Test",
    size: payload.size || "One size",
    original_price: payload.original_price || payload.price || 10,
    location: payload.location || "02139",
    quantity: payload.quantity || 1,
    weight: payload.shipping?.parcel_weight || "1 lb",
  } : {};
  const optionalValues = { ...diagnosticOptionalValues, ...payload };
  for (const name of ["brand", "size", "color", "material", "location", "quantity", "weight"]) {
    if (optionalValues[name] == null || optionalValues[name] === "") continue;
    const node = ppFindField(adapter[name], [name]);
    const filled = ppSetValue(node, optionalValues[name]);
    const required = (adapter.required_text_fields || []).includes(name);
    if (filled) populated_fields.push(name); else if (required) missing_required_fields.push(name);
    record(name, required, Boolean(node), true, filled, optionalValues[name], filled ? null : (node ? "VALUE_NOT_ACCEPTED" : "FIELD_NOT_FOUND"), node, adapter[name], [name]);
  }

  let uploadImages = Array.isArray(images) ? images : [];
  if (diagnostic && uploadImages.length === 0) {
    const data_url = ppSyntheticImage();
    if (data_url) uploadImages = [{ data_url, file_name: "posterpro-safe-diagnostic.jpg" }];
  }
  let uploaded_image_count = 0;
  const input = document.querySelector(adapter.photos);
  const detectedPhotoInput = Boolean(input && !input.disabled);
  if (detectedPhotoInput && uploadImages.length) {
    const transfer = new DataTransfer();
    uploadImages.forEach((image, index) => { const file = ppDataUrlToFile(image, index); if (file) transfer.items.add(file); });
    if (transfer.files.length) {
      input.files = transfer.files;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      uploaded_image_count = transfer.files.length; populated_fields.push("photos");
    }
  }
  const photoOk = uploaded_image_count > 0;
  if (!photoOk) missing_required_fields.push("photos");
  record("photos", true, detectedPhotoInput, true, photoOk, `${uploaded_image_count} synthetic/canonical image(s)`, photoOk ? null : (detectedPhotoInput ? "IMAGE_TRANSFER_FAILED" : "FILE_INPUT_NOT_FOUND"), input, [adapter.photos], ["photos", "add photos", "upload"]);

  const baseFieldsDetected = fields.every(([name, selectors, labels]) => Boolean(ppFindField(selectors, labels)));
  const page_state = baseFieldsDetected ? "FORM_AVAILABLE" : "FORM_CHANGED";
  const capability_ready = baseFieldsDetected && missing_required_fields.length === 0 && photoOk;
  return {
    ok: diagnostic ? page_state === "FORM_AVAILABLE" : page_state === "FORM_AVAILABLE",
    capability_ready,
    stage: diagnostic ? "COMPLETED" : "AWAITING_OPERATOR_REVIEW",
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
    operator_review_required: !diagnostic,
    submission_performed: false,
    error_code: page_state === "FORM_AVAILABLE" ? (missing_required_fields.length ? "REQUIRED_FIELDS_UNRESOLVED" : null) : "MARKETPLACE_FORM_CHANGED",
  };
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

async function ppWaitForEditableForm(adapter, timeoutMs = 12000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (ppFindField(adapter.title, ["title"]) && ppFindField(adapter.price, ["price"]) && ppFindField(adapter.description, ["description"])) return true;
    await ppWait(250);
  }
  return false;
}

async function ppUpdateExactListing(message) {
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

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.action === "posterpro_update_exact_listing") {
    ppUpdateExactListing(message).then(sendResponse).catch((error) => sendResponse({ ok: false, action: "UPDATE", identity_verified: false, error_code: "UPDATE_ADAPTER_FAILED", error: String(error?.message || error).slice(0, 200), submission_performed: false }));
    return true;
  }
  if (message?.action === "posterpro_inspect_exact_end") {
    sendResponse(ppInspectExactEnd(message));
    return false;
  }
  if (message?.action !== "posterpro_fill_listing" && message?.action !== "posterpro_diagnose_listing") return false;
  const diagnostic = message.action === "posterpro_diagnose_listing";
  ppFillListing({ ...message, diagnostic }).then(sendResponse).catch((error) => sendResponse({
    ok: false, error_code: "FORM_FILL_FAILED", error: String(error?.message || error).slice(0, 300),
    page_url: location.origin + location.pathname, submission_performed: false,
  }));
  return true;
});
