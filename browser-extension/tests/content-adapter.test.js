const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const TEST_VERSION = JSON.parse(fs.readFileSync(path.join(root, "manifest.json"), "utf8")).version;
const TEST_BUILD_ID = `posterpro-${TEST_VERSION}-facebook-background-queue-focus-safety`;

function loadContentFixture(hostname = "www.facebook.com", pathname = "/marketplace/create/item", options = {}) {
  class FakeInput {
    constructor(tagName = "INPUT", attrs = {}) { this.tagName = tagName; this.attrs = attrs; this.type = attrs.type || (tagName === "TEXTAREA" ? "textarea" : "text"); this.disabled = false; this.isContentEditable = false; this.value = ""; this.files = []; this.offsetParent = {}; this.innerText = attrs.text || ""; this.textContent = this.innerText; this.parentElement = attrs.parentElement || null; }
    focus() {}
    blur() {}
    dispatchEvent(event) { this.attrs.onDispatch?.(event); return true; }
    getAttribute(name) { return name === "aria-hidden" ? "false" : (this.attrs[name] ?? null); }
    click() { this.attrs.onClick?.(); }
    getClientRects() { return [{}]; }
    getBoundingClientRect() { return this.attrs.rect || { left: 40, top: 20, right: 160, bottom: 56, width: 120, height: 36 }; }
    addEventListener(type, callback) { this._listeners ||= new Map(); if (!this._listeners.has(type)) this._listeners.set(type, new Set()); this._listeners.get(type).add(callback); }
    removeEventListener(type, callback) { this._listeners?.get(type)?.delete(callback); }
    contains(node) { return node === this || Boolean(this.attrs.children?.includes(node)); }
    closest(selector) { return selector.includes("form") && this.attrs.form ? this.attrs.form : selector.includes("button") || selector.includes("option") || selector.includes("li") ? this : null; }
  }
  class FakeTextArea extends FakeInput {}
  class FakeFileInput extends FakeInput {}
  class FakeDataTransfer {
    constructor() {
      const files = [];
      this.items = { add(file) { files.push(file); } };
      Object.defineProperty(this, "files", { get: () => files });
    }
  }
  class FakeFile {
    constructor(parts, name, options) { this.parts = parts; this.name = name; this.type = options.type; }
  }
  const fields = {
    title: new FakeInput(),
    price: new FakeInput(),
    description: new FakeTextArea("TEXTAREA"),
    brand: new FakeInput(),
    size: new FakeInput(),
    color: new FakeInput(),
    material: new FakeInput(),
    location: new FakeInput(),
    photos: new FakeFileInput("INPUT", { type: "file" }),
  };
  const makeLabel = (text, control) => ({ tagName: "LABEL", innerText: text, textContent: text, control,
    parentElement: null, getAttribute: () => null, contains: (node) => node === control,
    getClientRects: () => [{}], offsetParent: {} });
  const semanticLabels = {
    title: makeLabel("Title", fields.title), price: makeLabel("Price", fields.price),
    description: makeLabel("Description", fields.description), location: makeLabel("Location", fields.location),
    brand: makeLabel("Brand", fields.brand), size: makeLabel("Size", fields.size),
    color: makeLabel("Color", fields.color), ...(options.facebookMaterialControlAbsent ? {} : { material: makeLabel("Material", fields.material) }),
  };
  for (const [name, label] of Object.entries(semanticLabels)) fields[name].labels = [label];
  for (const name of ["title", "price", "description"]) if (options.fieldValues?.[name] != null) fields[name].value = options.fieldValues[name];
  const mercariControls = options.mercariControls || {};
  const mercariNodes = Object.values(mercariControls);
  const photoPreviews = [];
  for (let index = 0; index < Number(options.initialPhotoPreviewCount || 0); index += 1) photoPreviews.push({ src: `blob:stale-${index}`, currentSrc: `blob:stale-${index}`, alt: "Photo preview", offsetParent: {}, getClientRects: () => [{}] });
  fields.photos.parentElement = { querySelectorAll(selector) { return /img|canvas|role='img'|data-testid/i.test(selector) ? photoPreviews : []; } };
  if (options.facebookPhotoPreviewOnUpload) {
    fields.photos.attrs.onDispatch = (event) => {
      if (event?.type === "change" && options.facebookClearDescriptionOnPhotoUpload) fields.description.value = String(options.facebookClearDescriptionOnPhotoUpload);
      if (event?.type === "change" && !photoPreviews.length) {
        for (let index = 0; index < Math.max(1, fields.photos.files?.length || 0); index += 1) photoPreviews.push({ src: `blob:posterpro-safe-preview-${index}`, currentSrc: `blob:posterpro-safe-preview-${index}`, alt: "Photo preview", offsetParent: {}, getClientRects: () => [{}] });
      }
    };
  }
  const links = options.links || [];
  let bodyText = options.bodyText || "Create a listing";
  const formStartedAt = Date.now();
  const formReady = () => !options.formNeverReady && Date.now() - formStartedAt >= (options.formReadyAfterMs || 0);
  const document = {
    querySelector(selector) {
      if (hostname.includes("mercari.com")) {
        if (!formReady()) return null;
        if (selector === 'input[name="sellCondition"]') return options.conditionInputs?.[0] || mercariControls.condition || null;
        if (selector === 'input[name="deliveryOptions"][aria-label="mercariShipping"]' || selector === 'input[name="deliveryOptions"]') return mercariControls.shipping || null;
        if (selector.includes("category")) return mercariControls.category || null;
        if (selector === 'input[type="file"]') return fields.photos;
        if (selector.includes("title")) return fields.title;
        if (selector.includes("price") || selector.includes("inputmode")) return fields.price;
        if (selector.includes("description")) return fields.description;
        if (selector.includes("brand")) return fields.brand;
        return null;
      }
      if (options.formNeverReady && !hostname.includes("mercari.com")) return null;
      if (options.facebookEditPage && !options.editFormReady && /Title|Price|Description|inputmode/i.test(selector)) return null;
      if (/Title/i.test(selector)) return fields.title;
      if (/Price/i.test(selector) || selector.includes("inputmode")) return fields.price;
      if (/Description/i.test(selector)) return fields.description;
      if (/Brand/i.test(selector)) return fields.brand;
      if (/Size/i.test(selector)) return fields.size;
      if (/Color/i.test(selector)) return fields.color;
      if (/Material/i.test(selector)) return fields.material;
      if (/Location|ZIP/i.test(selector)) return options.facebookLocationAbsent ? null : fields.location;
      if (selector === 'input[type="file"]') return fields.photos;
      return null;
    },
    body: {
      get innerText() { return bodyText; },
      get textContent() { return bodyText; },
      querySelectorAll(selector) { return options.reconciliationBodyNodes || []; },
    },
    hasFocus: () => true,
    visibilityState: "visible",
    title: options.pageTitle || "Sell an item | Mercari",
    readyState: "complete",
    documentElement: {},
    createElement(tag) {
      if (tag !== "canvas") return {};
      return { getContext: () => ({ fillRect() {}, fillText() {} }), toDataURL: () => "data:image/jpeg;base64,aGk=" };
    },
    querySelectorAll(selector = "") {
      if (selector === "label") return hostname.includes("facebook.com") && formReady()
        ? Object.entries(semanticLabels).filter(([name]) => name !== "location" || !options.facebookLocationAbsent).map(([, label]) => label) : [];
      if (selector === 'textarea,input,[role="textbox"],[contenteditable="true"],[contenteditable="plaintext-only"]')
        return hostname.includes("facebook.com") && formReady() ? [fields.title, fields.price, fields.description, ...(options.facebookLocationAbsent ? [] : [fields.location])] : [];
      if (selector === 'input,textarea,[role="textbox"],[contenteditable="true"],[contenteditable="plaintext-only"]')
        return hostname.includes("facebook.com") && formReady() ? [fields.title, fields.price, fields.description, fields.brand, fields.size, fields.color, ...(options.facebookMaterialControlAbsent ? [] : [fields.material]), ...(options.facebookLocationAbsent ? [] : [fields.location])] : [];
      if (selector === 'input,[role="combobox"],[role="textbox"],textarea,[contenteditable="true"]')
        return hostname.includes("facebook.com") && formReady() ? [fields.title, fields.price, fields.description, ...(options.facebookLocationAbsent ? [] : [fields.location])] : [];
      if (selector === 'a[href]') return links;
      if (selector === 'input[type="search"],input[placeholder],[role="searchbox"]') return options.reconciliationSearch ? [options.reconciliationSearch] : [];
      if (/button,\[role="button"\].*input\[type="(?:submit|button)"\]/.test(selector)) return options.facebookButtons || (options.submitControl ? [options.submitControl] : []);
      if (hostname.includes("mercari.com")) {
        if (selector === "input") return formReady() ? [fields.title, fields.price, fields.photos, ...mercariNodes].filter(Boolean) : [];
        if (selector === "textarea") return formReady() ? [fields.description] : [];
        if (selector === "button,[role='button']") return options.visibleButtons || [];
        if (selector === "h1,h2,h3,[role='heading']") return options.headings || [];
        if (selector === "form") return formReady() && options.visibleForm ? [options.visibleForm] : [];
        if (selector === "label") return [];
        if (selector === '[role="dialog"], [role="listbox"], [role="menu"]') return options.mercariDialogs || [];
        if (selector === 'input[name="sellCondition"]') return options.conditionInputs || (mercariControls.condition ? [mercariControls.condition] : []);
        if (selector === 'input[name="deliveryOptions"]') return mercariControls.shipping ? [mercariControls.shipping] : [];
        if (/role="option"|role="menuitem"|role="menuitemradio"|role="radio"|role="treeitem"|role="listbox"|option, li|role="dialog"/.test(selector)) return options.mercariOptions || [];
        if (/input,textarea,select|role='button'/.test(selector)) return mercariNodes;
        return [];
      }
      if ((options.formNeverReady || (options.facebookEditPage && !options.editFormReady))
        && /textarea|contenteditable|input,textarea|input\[type|input\[name|input,\[role|textarea,input/.test(selector)) return [];
      if (options.facebookEditPage && !options.editFormReady && /textarea|contenteditable|input,textarea|input\[type|input\[name/i.test(selector)) return [];
      if (selector === "img") return photoPreviews;
      if (selector === '[role="dialog"],[role="listbox"],[role="menu"]') return options.facebookOverlays || [];
      if (selector === 'button,[role="button"],a,input[type="button"],input[type="submit"]') return options.facebookButtons || [];
      if (/textarea|contenteditable/.test(selector)) return [fields.description];
      if (selector === "form,[role='dialog'],main,[role='main']") return options.facebookForm ? [options.facebookForm] : [];
      if (selector === "form,[role='dialog']") return options.facebookForm ? [options.facebookForm] : [];
      if (selector === 'button,[role="button"],input[type="button"]') return options.facebookButtons || [];
      return [];
    },
  };
  document.contains = (node) => Boolean(node?.isConnected !== false);
  document.elementFromPoint = (x, y) => options.elementFromPoint ? options.elementFromPoint(x, y) : null;
  const windowEventListeners = new Map();
  const documentEventListeners = new Map();
  document.addEventListener = (type, fn) => { if (!documentEventListeners.has(type)) documentEventListeners.set(type, new Set()); documentEventListeners.get(type).add(fn); };
  document.removeEventListener = (type, fn) => documentEventListeners.get(type)?.delete(fn);
  document.documentElement.clientWidth = 1280;
  document.documentElement.clientHeight = 800;
  let listener;
  class FakeDomEvent {
    constructor(type, options = {}) { this.type = type; Object.assign(this, options); this.defaultPrevented = false; this.target = null; }
    preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  }
  const context = {
    document,
    location: { hostname, origin: `https://${hostname}`, pathname, href: `https://${hostname}${pathname}` },
      chrome: { runtime: {
        getManifest: () => ({ version: TEST_VERSION }),
        async sendMessage(message) {
          if (message.action === "posterpro_read_marketplace_focus") return { ok: true, context: { active: true, highlighted: true, window_focused: true, tab_id: 17 } };
          if (message.action === "posterpro_release_marketplace_focus_lease") return { ok: true };
          if (message.action === "posterpro_facebook_form_progress") return { ok: true, last_progress_stage: message.stage };
          return options.onBrowserInput ? options.onBrowserInput(message, context) : { ok: false, error_code: "TEST_BROWSER_INPUT_HANDLER_MISSING" };
        },
        onMessage: { addListener(callback) { listener = callback; }, removeListener() {} },
      } },
    HTMLInputElement: FakeInput,
    HTMLTextAreaElement: FakeTextArea,
    Event: FakeDomEvent,
    MouseEvent: FakeDomEvent,
    PointerEvent: FakeDomEvent,
    DataTransfer: FakeDataTransfer,
    File: FakeFile,
    setTimeout,
    clearTimeout,
    atob: (value) => Buffer.from(value, "base64").toString("binary"),
    URL,
    addEventListener(type, fn) { if (!windowEventListeners.has(type)) windowEventListeners.set(type, new Set()); windowEventListeners.get(type).add(fn); },
    removeEventListener(type, fn) { windowEventListeners.get(type)?.delete(fn); },
    outerWidth: 1280, outerHeight: 900, devicePixelRatio: 1, scrollX: 0, scrollY: 0,
    visualViewport: { width: 1280, height: 800, scale: 1, offsetLeft: 0, offsetTop: 0, pageLeft: 0, pageTop: 0 },
    requestAnimationFrame(callback) { return setTimeout(() => callback(Date.now()), 0); },
    __windowEventListeners: windowEventListeners,
    __documentEventListeners: documentEventListeners,
  };
  context.globalThis = context;
  context.innerWidth = 1280;
  context.innerHeight = 800;
  context.getComputedStyle = () => ({ visibility: "visible", display: "block" });
  context.setBodyText = (value) => { bodyText = value; };
  vm.runInNewContext(fs.readFileSync(path.join(root, "marketplace-adapters.js"), "utf8"), context);
  const identitySource = fs.readFileSync(path.join(root, "content.js"), "utf8")
    .replace(/content_script_version: "0\.3\.\d+"/, `content_script_version: "${TEST_VERSION}"`)
    .replace(/content_script_build_id: "posterpro-[^"]+"/, `content_script_build_id: "${options.contentBuildId || TEST_BUILD_ID}"`);
  const contentSource = identitySource + "\nglobalThis.__contentTest = { ppProbeFacebookCreateContextReadOnly, ppWaitForMercariSellFormReady, ppMercariPageEvidence, ppSelectOption, ppMercariInputLabel, ppWaitForFacebookCreateFormReady, ppFacebookImagePreviewEvidence, ppFacebookMediaSnapshot, ppFacebookCreatePreviewCount, ppFacebookCreateBaselineEvidence, ppInspectFacebookCreateBaseline, ppFacebookLocationStageState, ppSelectFacebookCategory, ppFacebookVisibleLocationMatch, ppVerifyFacebookLocationAtReview, ppFacebookWeightOptionMatches, ppFacebookStageSnapshot, ppFacebookWorkflowStage, ppFacebookDetailsControl, ppFacebookSetAndVerifyDetailsField, ppFacebookDetailsIntegrityEvidence, ppFacebookPriceEquivalent, ppFacebookObservedPrice, ppWaitForFacebookFinalPublishBoundary, ppFacebookResolveUpdateTarget, ppFacebookUpdateCandidates, ppFacebookUpdateTargetEvidence, ppFacebookShippingValidationSnapshot, ppFacebookValidateUpdateActivationPoint, ppFacebookActivateUpdateOnce, ppFacebookNextControlEvidence, ppFacebookPostShippingReadiness, ppFacebookFormReadinessSnapshot, ppFacebookReadinessFieldEvidence, ppAdvanceFacebookToPublishBoundary, ppFacebookOfferToggles, ppFacebookDisableOffers, ppFacebookResolveMinimumPriceControl, ppFacebookIsInactiveMinimumPriceAlert, ppFacebookObservedTitle, ppMarketplaceListingIdentity, ppFacebookSubmittedListingIdentity, ppFacebookReconcileSellerListing, ppFindField, ppFacebookDescriptionDomProbe, ppFacebookDescriptionControl, ppFacebookLocationControlResolution, ppFacebookValidateMutationOwnership, ppFacebookDescriptionMutationCheckpoint, ppFacebookDescriptionCrossFieldGuard, ppFacebookDescriptionEvidence, ppFacebookSetAndVerifyDescription, ppFacebookDescriptionSubmissionGuard, ppFacebookDescriptionHash, ppFacebookDescriptionOnlyRepair, ppFacebookDescriptionSaveEvidence, ppSubmitOperatorAuthorizedListing };";
  vm.runInNewContext(contentSource, context);
  return { context, document, fields, options, contentApi: context.__contentTest, listener: (...args) => listener(...args) };
}

test("Facebook CREATE baseline passes only for a blank Details form with zero photo previews", () => {
  const clean = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const evidence = clean.contentApi.ppFacebookCreateBaselineEvidence();
  assert.equal(evidence.clean, true);
  assert.equal(evidence.initial_wizard_stage, "DETAILS");
  assert.equal(evidence.initial_photo_preview_count, 0);
  assert.equal(evidence.initial_title_value, "");
  assert.equal(evidence.initial_price_value, "");
  assert.equal(evidence.initial_description_length, 0);
  const dirty = loadContentFixture("www.facebook.com", "/marketplace/create/item", { initialPhotoPreviewCount: 8 });
  const dirtyEvidence = dirty.contentApi.ppFacebookCreateBaselineEvidence();
  assert.equal(dirtyEvidence.clean, false);
  assert.equal(dirtyEvidence.initial_photo_preview_count, 8);
  assert.ok(dirtyEvidence.reasons.includes("PREEXISTING_PHOTO_PREVIEWS"));
});

test("Facebook CREATE baseline ignores the enclosing hydrated Details dialog but rejects a transient menu", () => {
  let loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const shell = { tagName: "DIV", innerText: "Create listing Details", textContent: "Create listing Details", offsetParent: {},
    getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "dialog" : null,
    contains: (node) => [loaded.fields.title, loaded.fields.price, loaded.fields.description].includes(node) };
  loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", { facebookOverlays: [shell] });
  shell.contains = (node) => [loaded.fields.title, loaded.fields.price, loaded.fields.description].includes(node);
  const shellEvidence = loaded.contentApi.ppFacebookCreateBaselineEvidence();
  assert.equal(shellEvidence.visible_overlay_count, 1);
  assert.equal(shellEvidence.open_overlay_count, 0);
  assert.equal(shellEvidence.clean, true);
  assert.equal(shellEvidence.visible_overlay_evidence[0].marketplace_form_shell, true);

  const menu = { tagName: "DIV", innerText: "Choose category", textContent: "Choose category", offsetParent: {},
    getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "listbox" : null, contains: () => false };
  const dirty = loadContentFixture("www.facebook.com", "/marketplace/create/item", { facebookOverlays: [menu] });
  const menuEvidence = dirty.contentApi.ppFacebookCreateBaselineEvidence();
  assert.equal(menuEvidence.clean, false);
  assert.ok(menuEvidence.reasons.includes("OPEN_DIALOG_OR_MENU"));
});

test("read-only Facebook context probe reports the clean form without changing controls", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", { fieldValues: { title: "", price: "", description: "" } });
  const before = { title: loaded.fields.title.value, price: loaded.fields.price.value, description: loaded.fields.description.value, photos: loaded.fields.photos.files.length };
  const probe = await loaded.contentApi.ppProbeFacebookCreateContextReadOnly();
  assert.equal(probe.read_only, true);
  assert.equal(probe.workflow_stage, "DETAILS");
  assert.equal(probe.photo_preview_count, 0);
  assert.equal(probe.title_length, 0);
  assert.equal(probe.price_value, "");
  assert.equal(probe.description_length, 0);
  assert.deepEqual({ title: loaded.fields.title.value, price: loaded.fields.price.value, description: loaded.fields.description.value, photos: loaded.fields.photos.files.length }, before);
});

test("read-only Facebook CREATE baseline message reports dirty form without mutating fields", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", {
    initialPhotoPreviewCount: 2, fieldValues: { title: "Old title" },
  });
  const before = { title: loaded.fields.title.value, price: loaded.fields.price.value, description: loaded.fields.description.value };
  const result = await new Promise((resolve) => loaded.listener({ action: "posterpro_inspect_facebook_create_baseline" }, {}, resolve));
  assert.equal(result.clean, false);
  assert.equal(result.error_code, "FACEBOOK_DIRTY_CREATE_FORM");
  assert.deepEqual({ title: loaded.fields.title.value, price: loaded.fields.price.value, description: loaded.fields.description.value }, before);
});

test("Facebook Location is deferred before its semantic stage and becomes required once that stage mounts", () => {
  assert.equal(loadContentFixture().contentApi.ppFacebookLocationStageState({ filled: false, diagnostic: { stage: "LOCATION_NOT_VERIFIED_SAFE_FLOW_INCOMPLETE", stages: [{ workflow_stage: "DETAILS" }] } }), "NOT_MOUNTED_YET");
  assert.equal(loadContentFixture().contentApi.ppFacebookLocationStageState({ filled: false, diagnostic: { stage: "LOCATION_REQUIRED_AT_FINAL_BOUNDARY", stages: [{ workflow_stage: "LOCATION_SHIPPING", location_validation_error: true }] } }), "UNRESOLVED_AT_EXPECTED_STAGE");
  assert.equal(loadContentFixture().contentApi.ppFacebookLocationStageState({ filled: true }), "VERIFIED");
});

test("content-script identity reports its own build and extension runtime version", async () => {
  const { listener } = loadContentFixture("www.mercari.com", "/sell/");
  const identity = await new Promise((resolve) => listener({ action: "posterpro_content_script_identity" }, {}, resolve));
  assert.equal(identity.extension_version, TEST_VERSION);
  assert.equal(identity.content_script_version, TEST_VERSION);
  assert.equal(identity.content_script_build_id, TEST_BUILD_ID);
  assert.ok(identity.content_script_injected_at);
  assert.ok(identity.page_loaded_at);
});

test("Facebook description DOM probe reports bounded visible control and label evidence without mutating controls", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const parent = { tagName: "DIV", innerText: "Description Add details about your item", textContent: "Description Add details about your item", parentElement: null,
    getAttribute: () => null, contains(node) { return node === label || node === control; } };
  const label = { tagName: "LABEL", innerText: "Description", textContent: "Description", parentElement: parent, control: null,
    getAttribute(name) { return name === "for" ? "listing-description" : null; }, contains(node) { return node === control; },
    getClientRects: () => [{}], offsetParent: {} };
  const control = new loaded.context.HTMLTextAreaElement("TEXTAREA", { id: "listing-description", placeholder: "Description" });
  control.value = "Existing text";
  control.isConnected = true;
  control.parentElement = parent;
  control.getBoundingClientRect = () => ({ x: 10, y: 20, width: 300, height: 100 });
  label.control = control;
  parent.contains = (node) => node === label || node === control;
  let writes = 0;
  control.attrs.onDispatch = () => { writes += 1; };
  loaded.document.getElementById = (id) => id === "listing-description" ? control : null;
  loaded.document.querySelectorAll = (selector) => {
    if (selector === 'textarea,input,[role="textbox"],[contenteditable="true"],[contenteditable="plaintext-only"]') return [control];
    if (selector === "label,[role=heading],h1,h2,h3,[aria-label]") return [label];
    return [];
  };
  const result = await new Promise((resolve) => loaded.listener({ action: "posterpro_probe_facebook_description_dom" }, {}, resolve));
  assert.equal(result.ok, true);
  assert.equal(result.read_only, true);
  assert.equal(result.probe.candidates.length, 1);
  assert.equal(result.probe.candidates[0].tag_name, "textarea");
  assert.equal(result.probe.candidates[0].value, "Existing text");
  assert.equal(result.probe.candidates[0].description_label_relationship[0].label_control_match, true);
  assert.equal(result.probe.description_labels[0].text, "Description");
  assert.deepEqual(JSON.parse(JSON.stringify(result.probe.description_labels[0].ancestors[0])), { tag: "div", role: null, label: "", text: "Description Add details about your item" });
  assert.equal(writes, 0);
});

test("Facebook description DOM probe refuses non-CREATE pages", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/item/123/");
  const result = await new Promise((resolve) => loaded.listener({ action: "posterpro_probe_facebook_description_dom" }, {}, resolve));
  assert.equal(result.ok, false);
  assert.equal(result.error_code, "FACEBOOK_DESCRIPTION_PROBE_WRONG_PAGE");
});

test("Facebook description is accepted only after the live control retains normalized canonical text", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const expected = "Canonical description\nwith the full product details.";
  const result = await loaded.contentApi.ppFacebookSetAndVerifyDescription(
    loaded.context.POSTERPRO_MARKETPLACE_ADAPTERS.facebook, expected, "DESCRIPTION_AFTER_FILL",
  );
  assert.equal(result.ok, true);
  assert.equal(result.assignment_returned, true);
  assert.equal(result.evidence.matches, true);
  assert.equal(result.evidence.expected_length, result.evidence.observed_length);
  assert.equal(result.evidence.expected_hash, result.evidence.observed_hash);
  assert.equal(result.evidence.evidence_source, "live_description_control");
  assert.equal(loaded.fields.description.value, expected);
});

test("Facebook description guard rejects React-cleared/mismatched text before Publish", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const expected = "Canonical description which must survive the form.";
  loaded.fields.description.attrs.onDispatch = (event) => {
    if (event.type === "change") loaded.fields.description.value = "";
  };
  const fill = await loaded.contentApi.ppFacebookSetAndVerifyDescription(
    loaded.context.POSTERPRO_MARKETPLACE_ADAPTERS.facebook, expected, "DESCRIPTION_AFTER_FILL",
  );
  assert.equal(fill.ok, false);
  assert.equal(fill.error_code, "FACEBOOK_DESCRIPTION_NOT_PERSISTED");
  const guard = loaded.contentApi.ppFacebookDescriptionSubmissionGuard({ description: expected });
  assert.equal(guard.allowed, false);
  assert.equal(guard.error_code, "FACEBOOK_DESCRIPTION_NOT_PERSISTED");
  assert.equal(guard.evidence.observed_length, 0);
});

test("Facebook Details uses the wrapping Description label and location cannot fall back into that textarea", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const canonical = "Canonical item details. ".repeat(90);
  loaded.fields.description.value = "Huntington Station, NY 11746";
  assert.equal(loaded.contentApi.ppFacebookDescriptionControl(), loaded.fields.description);
  assert.equal(loaded.contentApi.ppFacebookLocationControlResolution().node, loaded.fields.location);
  const filled = await loaded.contentApi.ppFacebookSetAndVerifyDescription(null, canonical, "DESCRIPTION_AFTER_FILL");
  assert.equal(filled.ok, true, JSON.stringify(filled));
  assert.equal(filled.evidence.observed_length, canonical.trim().length);
  assert.equal(filled.evidence.description_verified_at_details_stage, true);
  assert.equal(loaded.fields.description.value, canonical);
  assert.equal(loaded.fields.location.value, "");
});

test("Facebook field ownership guard rejects Description as a Location, Category, or Condition target", () => {
  const loaded = loadContentFixture();
  const description = loaded.fields.description;
  for (const field of ["location", "category", "condition", "price"]) {
    const evidence = loaded.contentApi.ppFacebookValidateMutationOwnership(field, description);
    assert.equal(evidence.ok, false);
    assert.equal(evidence.error_code, "FACEBOOK_FIELD_OWNERSHIP_MISMATCH");
    assert.equal(evidence.resolved_as, "Description");
  }
  assert.equal(loaded.contentApi.ppFacebookValidateMutationOwnership("description", description).ok, true);
});

test("Facebook location resolver fails closed when only a Description textarea is available", () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", { facebookLocationAbsent: true });
  loaded.fields.description.value = "Existing description";
  const resolution = loaded.contentApi.ppFacebookLocationControlResolution();
  assert.equal(resolution.node, null);
  assert.equal(resolution.ambiguous, false);
  assert.equal(loaded.fields.description.value, "Existing description");
});

test("Facebook Description rejects locality, title, or price as cross-field corruption", () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const location = loaded.contentApi.ppFacebookDescriptionCrossFieldGuard("Huntington Station, NY 11746", "Huntington Station, NY 11746", {
    title: "Product title", price: 29.99, location: "Huntington Station, NY 11746",
  });
  assert.equal(location.ok, false);
  assert.equal(location.error_code, "FACEBOOK_DESCRIPTION_LOCATION_CROSSFIELD_CORRUPTION");
  assert.equal(loaded.contentApi.ppFacebookDescriptionCrossFieldGuard("Product title", "Product title", { title: "Product title" }).ok, false);
  assert.equal(loaded.contentApi.ppFacebookDescriptionCrossFieldGuard("29.99", "29.99", { price: 29.99 }).ok, false);
});

test("Facebook wizard stage is derived from mounted semantic controls, not the shared create URL", () => {
  const { contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const details = contentApi.ppFacebookWorkflowStage({ headings: ["Details"], field_labels: [{ label: "Description" }], details_controls: { title: { mounted: true }, price: { mounted: true }, description: { mounted: true } }, semantic_controls: [], final_publish_boundary: false });
  const shipping = contentApi.ppFacebookWorkflowStage({ headings: ["Location and delivery"], field_labels: [{ label: "Location" }, { label: "Shipping" }], semantic_controls: [], final_publish_boundary: false });
  assert.equal(details, "DETAILS");
  assert.equal(shipping, "LOCATION_SHIPPING");
});

test("Facebook persistent Details preview and minimum-offer input do not misclassify later wizard stage", () => {
  const { contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const later = contentApi.ppFacebookWorkflowStage({
    headings: ["Allow offers", "Preview", "Bluetooth Temperature Humidity Data Logger with APP", "Details", "Seller information", "Shipping & returns"],
    field_labels: [{ label: "Search Facebook" }, { label: "Minimum price you'll consider" }, { label: "$35" }],
    details_controls: { title: { mounted: false }, price: { mounted: false }, description: { mounted: false } },
    semantic_controls: [{ role: "switch", label: "on" }], final_publish_boundary: false,
  });
  assert.equal(later, "SHIPPING");
});

test("Facebook title and asking-price resolvers require their own semantic labels and exclude offer floor", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const { fields, contentApi } = loaded;
  fields.title.labels = [{ innerText: "Title", textContent: "Title" }];
  fields.price.labels = [{ innerText: "Price", textContent: "Price" }];
  fields.location.labels = [{ innerText: "Location", textContent: "Location" }];
  const minimum = new loaded.context.HTMLInputElement("INPUT", { "aria-label": "Minimum price you'll consider" });
  minimum.value = "$35";
  loaded.document.querySelectorAll = (selector) => {
    if (selector === 'input,textarea,[role="textbox"],[contenteditable="true"],[contenteditable="plaintext-only"]') return [fields.title, fields.price, fields.location, minimum];
    if (selector === "label") return [];
    return [];
  };
  assert.equal(contentApi.ppFacebookDetailsControl("title"), fields.title);
  assert.equal(contentApi.ppFacebookDetailsControl("price"), fields.price);
  assert.equal(contentApi.ppFacebookDetailsControl("title"), fields.title);
  const title = "Bluetooth Temperature Humidity Data Logger with APP, 500,000 Points";
  const titleResult = await contentApi.ppFacebookSetAndVerifyDetailsField("title", title);
  const priceResult = await contentApi.ppFacebookSetAndVerifyDetailsField("price", 69.99);
  assert.equal(titleResult.ok, true);
  assert.equal(priceResult.ok, true);
  assert.equal(minimum.value, "$35");
  assert.equal(fields.location.value, "");
});

test("Facebook persisted Details proof allows later stages without reading Location as Title or minimum offer as Price", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const { fields, contentApi } = loaded;
  const title = "Bluetooth Temperature Humidity Data Logger with APP, 500,000 Points";
  fields.title.value = title;
  fields.price.value = "69.99";
  fields.title.labels = [{ innerText: "Title", textContent: "Title" }];
  fields.price.labels = [{ innerText: "Price", textContent: "Price" }];
  fields.location.labels = [{ innerText: "Location", textContent: "Location" }];
  const titleVerify = await contentApi.ppFacebookSetAndVerifyDetailsField("title", title);
  const priceVerify = await contentApi.ppFacebookSetAndVerifyDetailsField("price", 69.99);
  assert.equal(titleVerify.ok, true, JSON.stringify(titleVerify));
  assert.equal(priceVerify.ok, true, JSON.stringify(priceVerify));
  const minimum = new loaded.context.HTMLInputElement("INPUT", { "aria-label": "Minimum price you'll consider" });
  minimum.value = "$35";
  loaded.document.querySelectorAll = (selector) => selector === 'input,textarea,[role="textbox"],[contenteditable="true"],[contenteditable="plaintext-only"]'
    ? [fields.location, minimum] : selector === "label" ? [] : [];
  const stage = contentApi.ppFacebookWorkflowStage({ headings: ["Allow offers", "Details", "Shipping & returns"],
    field_labels: [{ label: "Location" }, { label: "Minimum price you'll consider" }],
    details_controls: { title: { mounted: false }, price: { mounted: false }, description: { mounted: false } }, semantic_controls: [] });
  assert.equal(stage, "LOCATION_SHIPPING");
  const evidence = contentApi.ppFacebookDetailsIntegrityEvidence({ title, price: 69.99, location: "Huntington Station, NY 11746" }, "BEFORE_NEXT");
  assert.equal(evidence.title.matches, true);
  assert.equal(evidence.title.evidence_source, "durable_details_stage_verification");
  assert.equal(evidence.price.matches, true);
  assert.equal(evidence.price.evidence_source, "durable_details_stage_verification");
});

test("Facebook description-only repair edits the verified exact listing, changes no other fields, and activates Save once", async () => {
  const title = "Exact existing listing title";
  const remoteId = "FB-55";
  const remoteUrl = `https://www.facebook.com/marketplace/item/${remoteId}/`;
  const options = {
    facebookEditPage: true, editFormReady: false,
    fieldValues: { title, price: "29.99", description: "Old/blank description" },
    bodyText: `${title} $29.99`,
    reconciliationBodyNodes: [{ innerText: title, textContent: title }],
    facebookButtons: [], facebookForm: null,
  };
  const loaded = loadContentFixture("www.facebook.com", `/marketplace/item/${remoteId}`, options);
  const saveListeners = new Map();
  const makeButton = (label, onClick) => ({
    tagName: "BUTTON", type: "button", innerText: label, textContent: label,
    value: "", disabled: false, isConnected: true, offsetParent: {}, attrs: {},
    getAttribute(name) { return this.attrs[name] ?? null; },
    getClientRects() { return [{}]; },
    getBoundingClientRect() { return { left: 40, top: 20, right: 160, bottom: 56, width: 120, height: 36 }; },
    addEventListener(type, fn) { if (!saveListeners.has(type)) saveListeners.set(type, new Set()); saveListeners.get(type).add(fn); },
    removeEventListener(type, fn) { saveListeners.get(type)?.delete(fn); },
    contains(node) { return node === this; },
    closest(selector) { return selector.includes("form") ? options.facebookForm : this; },
    click() { onClick?.(); },
  });
  const save = makeButton("Save");
  const edit = makeButton("Edit listing", () => {
    options.editFormReady = true;
    loaded.context.location.pathname = `/marketplace/item/${remoteId}/edit/`;
  });
  const form = {
    isConnected: true, offsetParent: {}, innerText: "Edit listing",
    getClientRects() { return [{}]; },
    querySelector(selector) { return /textarea|Description|contenteditable/i.test(selector) ? loaded.fields.description : null; },
    querySelectorAll(selector) {
      if (/Description|textarea|contenteditable/i.test(selector)) return [loaded.fields.description];
      if (/button|role="button"|input\[type="submit"\]/i.test(selector)) return [save];
      return [];
    },
    contains(node) { return node === save || node === edit || node === loaded.fields.description; },
    addEventListener() {}, removeEventListener() {},
  };
  options.facebookForm = form;
  options.facebookButtons = [edit, save];
  save.attrs.form = form;
  loaded.fields.title.value = title;
  loaded.fields.price.value = "29.99";
  loaded.fields.description.value = "Old/blank description";
  loaded.document.elementFromPoint = () => save;
  let saveActivations = 0;
  options.onBrowserInput = async (message, context) => {
    if (message.action === "posterpro_begin_facebook_description_repair_save") return { ok: true };
    if (message.action === "posterpro_marketplace_control_activation") {
      saveActivations += 1;
      const coordinate = { x: message.target.x, y: message.target.y };
      for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
        const event = { type, isTrusted: true, target: save, clientX: coordinate.x, clientY: coordinate.y, defaultPrevented: false };
        for (const listeners of [context.__windowEventListeners, context.__documentEventListeners, saveListeners]) {
          for (const listener of listeners.get(type) || []) listener(event);
        }
      }
      return { ok: true, invocation_count: 1, activation_coordinate: coordinate };
    }
    return { ok: false, error_code: "UNEXPECTED_ACTION" };
  };
  const result = await loaded.contentApi.ppFacebookDescriptionOnlyRepair({
    marketplace: "facebook", description_repair_only: true,
    browser_input_nonce: "repair-one-use",
    external_listing_id: remoteId, expected_external_url: remoteUrl,
    payload: {
      repair_scope: "facebook_description_only", repair_expected_remote_id: remoteId,
      repair_expected_remote_url: remoteUrl, repair_expected_title: title,
      repair_expected_price: 29.99, title, price: 29.99,
      description: "Canonical description requiring exact in-place repair.",
    },
  });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.identity_verified, true);
  assert.equal(result.description_only, true);
  assert.equal(result.external_listing_id, remoteId);
  assert.equal(result.external_url, remoteUrl);
  assert.equal(result.save_activation_count, 1);
  assert.equal(saveActivations, 1);
  assert.equal(result.description_verification.matches, true);
  assert.equal(loaded.fields.description.value, "Canonical description requiring exact in-place repair.");
  assert.equal(loaded.fields.title.value, title);
  assert.equal(loaded.fields.price.value, "29.99");
});

test("ambiguous Facebook repair identity blocks Edit and Save without falling back to CREATE", async () => {
  const remoteId = "FB-ambiguous";
  const options = {
    facebookEditPage: true, editFormReady: false,
    bodyText: "Different listing title $29.99",
    reconciliationBodyNodes: [{ innerText: "Different listing title", textContent: "Different listing title" }],
    facebookButtons: [],
  };
  const loaded = loadContentFixture("www.facebook.com", `/marketplace/item/${remoteId}`, options);
  let editCount = 0;
  let saveCount = 0;
  const edit = { innerText: "Edit listing", textContent: "Edit listing", disabled: false, getAttribute: () => null, click() { editCount += 1; } };
  options.facebookButtons = [edit];
  const result = await loaded.contentApi.ppFacebookDescriptionOnlyRepair({
    marketplace: "facebook", browser_input_nonce: "nonce", external_listing_id: remoteId,
    expected_external_url: `https://www.facebook.com/marketplace/item/${remoteId}/`,
    payload: { repair_scope: "facebook_description_only", repair_expected_remote_id: remoteId,
      repair_expected_remote_url: `https://www.facebook.com/marketplace/item/${remoteId}/`,
      repair_expected_title: "Expected exact title", repair_expected_price: 29.99,
      title: "Expected exact title", price: 29.99, description: "Canonical description" },
  });
  assert.equal(result.ok, false);
  assert.equal(result.error_code, "FACEBOOK_DESCRIPTION_REPAIR_IDENTITY_AMBIGUOUS");
  assert.equal(editCount, 0);
  assert.equal(saveCount, 0);
  assert.equal(result.submission_performed, false);
});

test("Facebook post-submit identity rejects a pre-existing Marketplace link", () => {
  const title = "Current CREATE title";
  const stale = { href: "https://www.facebook.com/marketplace/item/old-id/", innerText: "Different listing $20", textContent: "Different listing $20" };
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", { links: [stale] });
  assert.equal(loaded.contentApi.ppFacebookSubmittedListingIdentity({ title, price: 45 }, new Set(["old-id"])), null);
});

test("Facebook post-submit identity requires direct page title and asking price", () => {
  const title = "Current CREATE title";
  const heading = { innerText: title, textContent: title, querySelectorAll: () => [] };
  const matching = loadContentFixture("www.facebook.com", "/marketplace/item/new-id/", {
    bodyText: `${title} $45.00`, reconciliationBodyNodes: [heading],
  });
  const wrongPrice = loadContentFixture("www.facebook.com", "/marketplace/item/wrong-id/", {
    bodyText: `${title} $44.00`, reconciliationBodyNodes: [heading],
  });
  assert.equal(matching.contentApi.ppFacebookSubmittedListingIdentity({ title, price: 45 }).external_listing_id, "new-id");
  assert.equal(wrongPrice.contentApi.ppFacebookSubmittedListingIdentity({ title, price: 45 }), null);
});

test("Facebook post-submit identity accepts one exact new link and rejects ambiguous candidates", () => {
  const title = "Current CREATE title";
  const card = (id) => ({ href: `https://www.facebook.com/marketplace/item/${id}/`, innerText: `${title} $45.00`, textContent: `${title} $45.00`, querySelectorAll: () => [{ innerText: title, textContent: title }] });
  const one = loadContentFixture("www.facebook.com", "/marketplace/create/item", { links: [card("new-id")] });
  const two = loadContentFixture("www.facebook.com", "/marketplace/create/item", { links: [card("new-id"), card("another-id")] });
  assert.equal(one.contentApi.ppFacebookSubmittedListingIdentity({ title, price: 45 }, new Set()).external_listing_id, "new-id");
  assert.equal(two.contentApi.ppFacebookSubmittedListingIdentity({ title, price: 45 }, new Set()), null);
});

test("authenticated Facebook item redirect reconciles exact rendered title and price without CREATE", async () => {
  const title = "Cyberpunk Reflex Drop Sticks Challenge Game - 3 Adjustable Speed Modes";
  const heading = { innerText: title, textContent: title, querySelectorAll: () => [] };
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/item/99887766/", {
    bodyText: `${title} $29.99`, reconciliationBodyNodes: [heading],
  });
  const result = await loaded.contentApi.ppFacebookReconcileSellerListing({ expected_title: title, expected_price: 29.99 });
  assert.equal(result.state, "FOUND");
  assert.equal(result.external_listing_id, "99887766");
  assert.equal(result.external_url, "https://www.facebook.com/marketplace/item/99887766/");
  assert.equal(result.observed_title, title);
  assert.equal(result.observed_price, "29.99");
  assert.equal(result.submission_performed, undefined);
});

test("Facebook accepts only the observed whole-dollar price normalization within one cent", () => {
  const { contentApi } = loadContentFixture();
  assert.equal(contentApi.ppFacebookPriceEquivalent(69.99, 70), true);
  assert.equal(contentApi.ppFacebookPriceEquivalent(69.99, "69.99"), true);
  assert.equal(contentApi.ppFacebookPriceEquivalent(69.99, 70.01), false);
  assert.equal(contentApi.ppFacebookPriceEquivalent(69.99, 71), false);
  assert.equal(contentApi.ppFacebookObservedPrice("Listing $70", 69.99), "70");
  assert.equal(contentApi.ppFacebookObservedPrice("Listing $70.50", 69.99), null);
  assert.equal(contentApi.ppFacebookObservedPrice("Listing $71", 69.99), null);
});

test("authenticated Facebook redirect records bounded normalized-price evidence", async () => {
  const title = "Bluetooth Temperature Humidity Data Logger with APP, 500,000 Points";
  const heading = { innerText: title, textContent: title, querySelectorAll: () => [] };
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/item/22070001/", {
    bodyText: `${title} $70`, reconciliationBodyNodes: [heading],
  });
  const result = await loaded.contentApi.ppFacebookReconcileSellerListing({ expected_title: title, expected_price: 69.99 });
  assert.equal(result.state, "FOUND");
  assert.equal(result.external_listing_id, "22070001");
  assert.equal(result.observed_price, "70");
  assert.equal(result.price_match_mode, "FACEBOOK_WHOLE_DOLLAR_WITHIN_ONE_CENT");
});

test("authenticated seller listings reconcile one exact title-and-price card", async () => {
  const title = "Cyberpunk Reflex Drop Sticks Challenge Game - 3 Adjustable Speed Modes";
  const card = {
    innerText: `${title} $29.99`, textContent: `${title} $29.99`,
    getAttribute: (name) => name === "role" ? "article" : null,
    querySelectorAll: () => [{ innerText: title, textContent: title }],
    parentElement: null,
  };
  const anchor = { href: "https://www.facebook.com/marketplace/item/99887766/", innerText: title, textContent: title, parentElement: card };
  const search = new (class extends Object { constructor() { super(); this.value = ""; this.type = "search"; this.offsetParent = {}; this.disabled = false; } getAttribute(name) { return name === "placeholder" ? "Search your listings" : null; } getClientRects() { return [{}]; } dispatchEvent() { return true; } })();
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/you/selling/", {
    bodyText: `${title} $29.99`, links: [anchor], reconciliationSearch: search,
  });
  const result = await loaded.contentApi.ppFacebookReconcileSellerListing({ expected_title: title, expected_price: 29.99 });
  assert.equal(result.state, "FOUND");
  assert.equal(result.source, "authenticated_facebook_seller_listings");
  assert.equal(result.external_listing_id, "99887766");
  assert.equal(result.exact_title_match, true);
  assert.equal(result.exact_price_match, true);
});

test("multiple exact seller-listing matches remain ambiguous and are never selected arbitrarily", async () => {
  const title = "Cyberpunk Reflex Drop Sticks Challenge Game - 3 Adjustable Speed Modes";
  const links = ["99887766", "88776655"].map((id) => {
    const card = {
      innerText: `${title} $29.99`, textContent: `${title} $29.99`,
      getAttribute: (name) => name === "role" ? "article" : null,
      querySelectorAll: () => [{ innerText: title, textContent: title }], parentElement: null,
    };
    return { href: `https://www.facebook.com/marketplace/item/${id}/`, innerText: title, textContent: title, parentElement: card };
  });
  const search = new (class extends Object { constructor() { super(); this.value = ""; this.type = "search"; this.offsetParent = {}; this.disabled = false; } getAttribute(name) { return name === "placeholder" ? "Search your listings" : null; } getClientRects() { return [{}]; } dispatchEvent() { return true; } })();
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/you/selling/", {
    bodyText: `${title} $29.99`, links, reconciliationSearch: search,
  });
  const result = await loaded.contentApi.ppFacebookReconcileSellerListing({ expected_title: title, expected_price: 29.99 });
  assert.equal(result.state, "AMBIGUOUS");
  assert.equal(result.reason, "MULTIPLE_EXACT_TITLE_PRICE_MATCHES");
  assert.equal(result.candidate_count, 2);
});

test("seller-listing absence is reported only after exact search and explicit empty state", async () => {
  const title = "Cyberpunk Reflex Drop Sticks Challenge Game - 3 Adjustable Speed Modes";
  const search = new (class extends Object { constructor() { super(); this.value = ""; this.type = "search"; this.offsetParent = {}; this.disabled = false; } getAttribute(name) { return name === "placeholder" ? "Search your listings" : null; } getClientRects() { return [{}]; } dispatchEvent() { return true; } })();
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/you/selling/", {
    bodyText: "No listings found", reconciliationSearch: search,
  });
  const result = await loaded.contentApi.ppFacebookReconcileSellerListing({ expected_title: title, expected_price: 29.99 });
  assert.equal(result.state, "NOT_FOUND");
  assert.equal(result.exact_title_search, true);
  assert.equal(result.account_search_complete, true);
  assert.equal(result.result_count, 0);
});

test("known operator-confirmed Facebook presence can never be downgraded to NOT_FOUND", async () => {
  const title = "Cyberpunk Reflex Drop Sticks Challenge Game - 3 Adjustable Speed Modes";
  const search = new (class extends Object { constructor() { super(); this.value = ""; this.type = "search"; this.offsetParent = {}; this.disabled = false; } getAttribute(name) { return name === "placeholder" ? "Search your listings" : null; } getClientRects() { return [{}]; } dispatchEvent() { return true; } })();
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/you/selling/", {
    bodyText: "No listings found", reconciliationSearch: search,
  });
  const result = await loaded.contentApi.ppFacebookReconcileSellerListing({ expected_title: title, expected_price: 29.99, known_remote_presence: true });
  assert.equal(result.state, "AMBIGUOUS");
  assert.equal(result.reason, "OPERATOR_CONFIRMED_REMOTE_PRESENCE_NOT_LOCATED");
});

test("Facebook offers ON are explicitly switched OFF exactly once", async () => {
  const loaded = loadContentFixture();
  let activations = 0;
  const toggle = new loaded.context.HTMLInputElement("INPUT", { type: "checkbox", "aria-label": "Accept offers" });
  toggle.checked = true;
  toggle.attrs.onClick = () => { activations += 1; toggle.checked = false; };
  loaded.context.document.querySelectorAll = (selector) => selector.includes("checkbox") ? [toggle] : [];
  const result = await loaded.contentApi.ppFacebookDisableOffers({ facebook_offers_enabled: false });
  assert.equal(result.resolved, true);
  assert.equal(result.offers_enabled, false);
  assert.equal(result.action, "SWITCHED_OFF");
  assert.equal(activations, 1);
  assert.equal(toggle.checked, false);
});

test("Facebook Marketplace negotiation sentence is recognized as the offers switch", async () => {
  const loaded = loadContentFixture();
  let activations = 0;
  const toggle = new loaded.context.HTMLInputElement("INPUT", { type: "checkbox", "aria-label": "Let buyers negotiate a price equal to or above the minimum price you set." });
  toggle.checked = true;
  toggle.attrs.onClick = () => { activations += 1; toggle.checked = false; };
  loaded.context.document.querySelectorAll = (selector) => selector.includes("checkbox") ? [toggle] : [];
  const result = await loaded.contentApi.ppFacebookDisableOffers({ facebook_offers_enabled: false });
  assert.equal(result.resolved, true);
  assert.equal(result.offers_enabled, false);
  assert.equal(result.action, "SWITCHED_OFF");
  assert.equal(activations, 1);
});

test("Facebook leaves an offers toggle that is already OFF untouched", async () => {
  const loaded = loadContentFixture();
  let activations = 0;
  const toggle = new loaded.context.HTMLInputElement("INPUT", { type: "checkbox", "aria-label": "Allow offers" });
  toggle.checked = false;
  toggle.attrs.onClick = () => { activations += 1; };
  loaded.context.document.querySelectorAll = (selector) => selector.includes("checkbox") ? [toggle] : [];
  const result = await loaded.contentApi.ppFacebookDisableOffers({});
  assert.equal(result.resolved, true);
  assert.equal(result.action, "ALREADY_OFF");
  assert.equal(activations, 0);
});

test("Facebook default policy is offers OFF unless destination data explicitly opts in", async () => {
  const loaded = loadContentFixture();
  const result = await loaded.contentApi.ppFacebookDisableOffers({});
  assert.equal(result.resolved, true);
  assert.equal(result.offers_enabled, false);
  assert.equal(result.action, "OFFERS_NOT_EXPOSED_DEFAULT_OFF");
});

test("offers OFF never writes the asking price into minimum price and leaves asking price unchanged", () => {
  const loaded = loadContentFixture();
  const floor = new loaded.context.HTMLInputElement("INPUT", { "aria-label": "Minimum price you'll consider", "aria-invalid": "true" });
  const ask = new loaded.context.HTMLInputElement("INPUT", { "aria-label": "Price" });
  ask.value = "29.99";
  loaded.context.document.querySelectorAll = (selector) => selector.includes("input,textarea") ? [floor] : [];
  const result = loaded.contentApi.ppFacebookResolveMinimumPriceControl({ price: 29.99, facebook_offers_enabled: false });
  assert.equal(result.resolved, true);
  assert.equal(result.action, "SKIPPED_OFFERS_DISABLED");
  assert.equal(floor.value, "");
  assert.equal(ask.value, "29.99");
});

test("inactive minimum-price validation is ignored only while offers are OFF", () => {
  const loaded = loadContentFixture();
  const inactive = new loaded.context.HTMLInputElement("INPUT", { "aria-label": "Minimum price you'll consider", "aria-invalid": "true", "aria-disabled": "true" });
  inactive.disabled = true;
  loaded.context.document.querySelectorAll = (selector) => selector.includes("input,textarea") ? [inactive] : [];
  assert.equal(loaded.contentApi.ppFacebookIsInactiveMinimumPriceAlert("Input Minimum price you'll consider is invalid.", { facebook_offers_enabled: false }), true);
  assert.equal(loaded.contentApi.ppFacebookIsInactiveMinimumPriceAlert("Input Minimum price you'll consider is invalid.", { facebook_offers_enabled: true }), false);
  inactive.disabled = false;
  inactive.attrs["aria-disabled"] = null;
  assert.equal(loaded.contentApi.ppFacebookIsInactiveMinimumPriceAlert("Input Minimum price you'll consider is invalid.", { facebook_offers_enabled: false }), false);
});

test("Facebook shipping completion is distinct from Next form readiness", () => {
  const { contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const disabledNext = {
    tagName: "BUTTON", disabled: true, isConnected: true,
    getAttribute(name) { return name === "aria-disabled" ? "true" : name === "role" ? null : null; },
  };
  assert.deepEqual(JSON.parse(JSON.stringify(contentApi.ppFacebookPostShippingReadiness(false, disabledNext))), {
    ready: false, error_code: "FACEBOOK_FORM_NOT_READY_FOR_NEXT", shipping_configuration_complete: true,
  });
  assert.deepEqual(JSON.parse(JSON.stringify(contentApi.ppFacebookPostShippingReadiness(true, disabledNext))), {
    ready: false, error_code: "FACEBOOK_SHIPPING_CONFIGURATION_NOT_COMMITTED", shipping_configuration_complete: false,
  });
  assert.deepEqual(JSON.parse(JSON.stringify(contentApi.ppFacebookPostShippingReadiness(false, { disabled: false, getAttribute: () => null }))), {
    ready: true, error_code: null, shipping_configuration_complete: true,
  });
});

test("Facebook readiness telemetry separates requested, filled, accepted fields and captures raw Next state without interaction", () => {
  const { context, document, contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  let sideEffects = 0;
  const makeNode = (tag, text, attrs = {}, props = {}) => ({
    tagName: tag.toUpperCase(), innerText: text, textContent: text, attrs, offsetParent: {}, isConnected: true,
    parentElement: null, value: props.value, required: Boolean(props.required), disabled: Boolean(props.disabled),
    type: props.type || null, checked: props.checked, selectedOptions: props.selectedOptions || [],
    getAttribute(name) { return this.attrs[name] ?? null; },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); },
    getClientRects() { return [{}]; },
    getBoundingClientRect() { return { x: 10, y: 20, left: 10, top: 20, width: 100, height: 30 }; },
    querySelectorAll() { return []; },
    click() { sideEffects += 1; },
  });
  const title = makeNode("input", "", { "aria-label": "Title" }, { value: "Requested title", required: true });
  const category = makeNode("div", "Games", { role: "combobox", "aria-label": "Category", "aria-required": "true", "aria-selected": "true" }, { required: true });
  const extra = makeNode("input", "", { "aria-label": "Audience", "aria-required": "true", placeholder: "Select audience" }, { required: true, disabled: true });
  const next = makeNode("button", "Next", { "aria-disabled": "true", disabled: "", tabindex: "-1", "aria-describedby": "next-help" }, { disabled: true });
  const next2 = makeNode("button", "Next", { "aria-disabled": "true", disabled: "", tabindex: "-1" }, { disabled: true });
  const main = makeNode("main", "Create listing");
  main.querySelectorAll = (selector) => selector === "img,[role='img']" ? [image] : [title, category, extra, next];
  main.contains = (node) => [title, category, extra, next, next2].includes(node);
  const image = makeNode("img", "", { alt: "Photo 1" });
  document.getElementById = (id) => id === "next-help" ? makeNode("div", "Complete required fields") : null;
  document.querySelectorAll = (selector) => {
    if (selector === 'form,[role="main"],main,[role="dialog"]') return [main];
    if (selector === 'button,[role="button"],input[type="button"]') return [next];
    if (selector === "img,[role='img']") return [image];
    if (selector === '[role="status"],[role="alert"],[aria-live], [data-testid*="upload" i],[data-testid*="media" i]') return [];
    if (selector.includes("role=\"alert\"") || selector.includes("aria-invalid") || selector.includes("aria-required")) return [extra];
    return [];
  };
  context.innerWidth = 1280;
  const payload = { title: "Requested title", category: "Games", location: "Huntington Station, NY 11746" };
  const first = contentApi.ppFacebookFormReadinessSnapshot(payload, "A_AFTER_INITIAL_FILL_BEFORE_SHIPPING");
  const categoryState = first.fields.find((row) => row.field === "category").controls[0];
  const titleState = first.fields.find((row) => row.field === "title").controls[0];
  assert.equal(categoryState.requested_value_matches_observed, true);
  assert.equal(categoryState.facebook_accepted_state, "committed_selection_observable");
  assert.equal(titleState.value, "Requested title");
  assert.equal(titleState.facebook_accepted_state, "value_present_acceptance_unproven");
  const unknownRequired = first.unknown_required_controls.find((control) => control.label.includes("Audience"));
  assert.ok(unknownRequired);
  assert.equal(unknownRequired.disabled, true);
  assert.equal(unknownRequired.visible, true, "rendered disabled required controls remain visible evidence");
  assert.equal(first.next.disabled, true);
  assert.equal(first.next.disabled_attribute, true);
  assert.equal(first.next.aria_disabled, "true");
  assert.equal(first.next.tabindex, "-1");
  assert.equal(first.next.enabled_by_posterpro, false);
  assert.equal(first.photos.media_tile_count, 1);
  assert.equal(sideEffects, 0, "snapshot telemetry must not activate controls");
  document.querySelectorAll = (selector) => selector === 'button,[role="button"],input[type="button"]' ? [next2]
    : selector === 'form,[role="main"],main,[role="dialog"]' ? [main]
      : selector === "img,[role='img']" ? [image] : [];
  const second = contentApi.ppFacebookFormReadinessSnapshot(payload, "B_BEFORE_SHIPPING_UPDATE");
  assert.notEqual(first.next.render_identity, second.next.render_identity);
  assert.equal(second.next.replaced_since_previous_sample, true);
  assert.equal(sideEffects, 0);
});

test("post-foreground resolver samples fresh Update geometry until stable and binds listeners to the fresh target", async () => {
  const { context, listener } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  let targetReads = 0;
  let freshAttached = 0;
  const dialog = {
    tagName: "DIV", innerText: "Change shipping method", textContent: "Change shipping method", isConnected: true,
    getAttribute: (name) => name === "role" ? "dialog" : null,
    getClientRects: () => [{}],
    contains: (node) => node === target || node === hit,
    querySelectorAll(selector) { return selector.includes("button") || selector.includes("role=\"button\"") ? [target] : []; },
  };
  const hit = { tagName: "SPAN", innerText: "Update", textContent: "Update", getAttribute: () => null, getClientRects: () => [{}] };
  const target = {
    tagName: "DIV", innerText: "Update", textContent: "Update", disabled: false, isConnected: true,
    getClientRects: () => [{}],
    getAttribute(name) { return name === "role" ? "button" : name === "aria-label" ? "Update" : null; },
    closest(selector) { return selector === '[role="dialog"]' ? dialog : selector === '[role="button"]' ? target : null; },
    contains(node) { return node === hit; },
    getBoundingClientRect() { targetReads += 1; const left = targetReads === 1 ? 100 : 104; return { left, top: 40, right: left + 80, bottom: 70, width: 80, height: 30, x: left, y: 40 }; },
  };
  context.document.contains = (node) => node === dialog || node === target || node === hit;
  context.document.elementFromPoint = () => hit;
  context.document.querySelectorAll = (selector) => selector === '[role="dialog"]' ? [dialog]
    : selector === '[role="button"],button,input[type="button"],input[type="submit"]' ? [target]
      : selector === '*' ? [target] : [];
  context.__POSTERPRO_CDP_UPDATE_CAPTURE = { attachFreshTarget(foundDialog, foundTarget) { assert.equal(foundDialog, dialog); assert.equal(foundTarget, target); freshAttached += 1; } };
  const response = await new Promise((resolve) => listener({
    action: "posterpro_resolve_facebook_update_target_after_foreground",
    authorization_nonce: "authorized-once", expected_stage: "FACEBOOK_SHIPPING_UPDATE",
    expected_content_build_id: TEST_BUILD_ID,
  }, {}, resolve));
  assert.equal(response.ok, true, JSON.stringify(response));
  assert.equal(response.target.x, 144);
  assert.equal(response.target.hit_target_inside, true);
  assert.equal(response.target.current_dialog_match, true);
  assert.ok(response.stability_samples.length >= 3);
  assert.equal(freshAttached, 1);
  delete context.__POSTERPRO_CDP_UPDATE_CAPTURE;
});

test("Facebook final integer hit test re-resolves Update and tests the exact activation coordinate", () => {
  const { context, contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const dialog = {
    tagName: "DIV", innerText: "Change shipping method", textContent: "Change shipping method", isConnected: true,
    parentElement: null, getAttribute: (name) => name === "role" ? "dialog" : null, getClientRects: () => [{}],
    contains: (node) => node === target || node === hit,
    querySelectorAll(selector) { return selector === '*' ? [target, hit] : selector.includes("button") || selector.includes("role=\"button\"") ? [target] : []; },
  };
  const hit = { tagName: "SPAN", innerText: "Update", textContent: "Update", isConnected: true,
    parentElement: null, getAttribute: () => null, getClientRects: () => [{}] };
  const target = {
    tagName: "DIV", innerText: "Update", textContent: "Update", disabled: false, isConnected: true,
    parentElement: dialog, getClientRects: () => [{}],
    getBoundingClientRect: () => ({ left: 10.2, top: 20.2, right: 90.2, bottom: 50.2, width: 80, height: 30, x: 10.2, y: 20.2 }),
    getAttribute(name) { return name === "role" ? "button" : name === "aria-label" ? "Update" : null; },
    closest(selector) { return selector === '[role="dialog"]' ? dialog : selector === '[role="button"]' ? target : null; },
    contains(node) { return node === hit; },
  };
  hit.parentElement = target;
  context.document.querySelectorAll = (selector) => selector === '[role="dialog"]' ? [dialog] : [];
  context.document.contains = (node) => node === dialog || node === target || node === hit;
  const sampled = [];
  context.document.elementFromPoint = (x, y) => { sampled.push([x, y]); return hit; };
  const evidence = contentApi.ppFacebookValidateUpdateActivationPoint({
    expected_content_build_id: TEST_BUILD_ID,
    expected_stage: "FACEBOOK_SHIPPING_UPDATE", authorization_nonce: "one-use-token",
    activation_coordinate: { x: 50, y: 35, rounding_method: "Math.round_stabilized_center" },
  });
  assert.equal(evidence.ok, true, JSON.stringify(evidence));
  assert.deepEqual([evidence.activation_coordinate.x, evidence.activation_coordinate.y], [50, 35]);
  assert.deepEqual(Array.from(sampled.at(-1)), [50, 35]);
  assert.equal(evidence.content_integer_hit_test.target_inside_update, true);
  assert.equal(evidence.current_dialog.heading, "Change shipping method");
  const outside = contentApi.ppFacebookValidateUpdateActivationPoint({
    expected_content_build_id: TEST_BUILD_ID,
    expected_stage: "FACEBOOK_SHIPPING_UPDATE", authorization_nonce: "one-use-token",
    activation_coordinate: { x: 91, y: 35, rounding_method: "test" },
  });
  assert.equal(outside.ok, false);
  assert.equal(outside.error_code, "FACEBOOK_INTEGER_HIT_TEST_OUTSIDE_UPDATE_RECT");
});

test("Facebook create form fixture stays blocked before submit when required fields are unresolved", async () => {
  const { fields, listener } = loadContentFixture("www.facebook.com", "/marketplace/create/item", { facebookPhotoPreviewOnUpload: true });
  const response = await new Promise((resolve) => {
    listener({
      action: "posterpro_fill_listing",
      marketplace: "facebook",
      payload: { title: "Test listing", price: 20, description: "A useful description", category_hint: "Home", condition: "New", location: "02139" },
      images: [{ data_url: "data:image/png;base64,aGk=", file_name: "posterpro-1.png" }],
    }, {}, resolve);
  });
  assert.equal(response.ok, true);
  assert.equal(response.stage, "BLOCKED_BEFORE_SUBMIT");
  assert.equal(response.submission_performed, false);
  assert.equal(fields.title.value, "Test listing");
  assert.equal(fields.price.value, "20");
  assert.equal(fields.description.value, "A useful description");
  assert.equal(response.uploaded_image_count, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(response.photo_upload)), { source_image_count: 1, intended_upload_count: 1, pre_upload_preview_count: 0, data_transfer_file_count: 1, post_upload_preview_count: 1, preview_delta: 1, accepted: true });
  assert.deepEqual(Array.from(response.missing_required_fields), ["category", "condition", "location"]);
  assert.equal(response.capability_ready, false);
  assert.equal(response.field_results.find((field) => field.field === "category").error_code, "FIELD_OR_MAPPED_VALUE_MISSING");
  assert.equal(response.selector_strategy, "central_marketplace_map_then_semantic_labels");
});

test("Facebook clean eight-photo baseline accepts exactly eight new previews (zero to eight)", async () => {
  const { listener } = loadContentFixture("www.facebook.com", "/marketplace/create/item", { facebookPhotoPreviewOnUpload: true });
  const images = Array.from({ length: 8 }, (_, index) => ({ data_url: "data:image/png;base64,aGk=", file_name: `photo-${index + 1}.png` }));
  const response = await new Promise((resolve) => listener({
    action: "posterpro_fill_listing", marketplace: "facebook",
    payload: { title: "Eight photo test", price: 20, description: "Photo baseline test", image_urls: images.map((_, i) => `https://img.example/${i}.jpg`) },
    images,
  }, {}, resolve));
  assert.equal(response.clean_create_form.initial_photo_preview_count, 0);
  assert.equal(response.photo_upload.source_image_count, 8);
  assert.equal(response.photo_upload.intended_upload_count, 8);
  assert.equal(response.photo_upload.pre_upload_preview_count, 0);
  assert.equal(response.photo_upload.data_transfer_file_count, 8);
  assert.equal(response.photo_upload.post_upload_preview_count, 8);
  assert.equal(response.photo_upload.preview_delta, 8);
  assert.equal(response.photo_upload.accepted, true);
});

test("Facebook description mutation trace stops at the first operation that clears the live control", async () => {
  const { listener } = loadContentFixture("www.facebook.com", "/marketplace/create/item", {
    facebookPhotoPreviewOnUpload: true,
    facebookClearDescriptionOnPhotoUpload: "Changed",
  });
  const images = [{ data_url: "data:image/png;base64,aGk=", file_name: "photo.png" }];
  const response = await new Promise((resolve) => listener({
    action: "posterpro_fill_listing", marketplace: "facebook",
    payload: { title: "Trace test", price: 20, description: "Canonical description expected to persist", image_urls: ["https://img.example/1.jpg"], location: "Huntington Station, NY 11746" },
    images,
  }, {}, resolve));
  assert.equal(response.submission_performed, false);
  assert.equal(response.stage, "FACEBOOK_DESCRIPTION_MUTATION_TRACE");
  assert.equal(response.error_code, "FACEBOOK_DESCRIPTION_NOT_PERSISTED");
  assert.equal(response.first_bad_operation, "AFTER_IMAGE_UPLOAD_SETTLED");
  assert.equal(response.description_mutation_trace[0].matches, true);
  assert.equal(response.description_mutation_trace.at(-1).normalized_length, 7);
  assert.equal(response.description_mutation_trace.at(-1).hash, "fnv1a32:89c5203b");
  assert.equal(response.description_mutation_trace.at(-1).control_fingerprint, response.description_mutation_trace[0].control_fingerprint);
});

test("Facebook safe diagnostic waits for hydrated anchors and requires a rendered photo preview", async () => {
  const { listener } = loadContentFixture("www.facebook.com", "/marketplace/create/item", { facebookPhotoPreviewOnUpload: true });
  const response = await new Promise((resolve) => listener({
    action: "posterpro_diagnose_listing", marketplace: "facebook",
    payload: { title: "PosterPro Safe Form Test Garment - Do Not Publish", price: 1,
      description: "Temporary diagnostic only", category: "Clothing", condition: "Used - good", location: "operator-configured-location" },
    images: [],
  }, {}, resolve));
  assert.equal(response.form_detected, true, JSON.stringify(response));
  assert.equal(response.form_readiness.form_ready_wait_started, true);
  assert.equal(response.form_readiness.form_ready_anchors.title, true);
  const photos = response.field_results.find((field) => field.field === "photos");
  assert.equal(photos.filled, true, JSON.stringify(photos));
  assert.equal(photos.interaction_diagnostic.accepted_preview.source_kind, "blob");
  assert.equal(response.submission_performed, false);
});

test("Facebook safe diagnostic reports missing configured location instead of inventing a ZIP", async () => {
  const { listener } = loadContentFixture("www.facebook.com", "/marketplace/create/item", { facebookPhotoPreviewOnUpload: true });
  const response = await new Promise((resolve) => listener({
    action: "posterpro_diagnose_listing", marketplace: "facebook",
    payload: { title: "PosterPro Safe Form Test Garment - Do Not Publish", price: 1, description: "Temporary diagnostic only" },
    images: [],
  }, {}, resolve));
  const location = response.field_results.find((field) => field.field === "location");
  assert.equal(location.required, true);
  assert.equal(location.filled, false);
  assert.equal(location.error_code, "MARKETPLACE_LOCATION_NOT_CONFIGURED");
  assert.equal(response.submission_performed, false);
});

test("Facebook safe location inspection captures each stage and stops before final Publish", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", { bodyText: "Create a listing", facebookLocationAbsent: true });
  let buttonLabels = ["Next"];
  const buttons = {
    Next: { tagName: "BUTTON", innerText: "Next", disabled: false, offsetParent: {}, getClientRects: () => [{}], getAttribute: (name) => name === "aria-disabled" ? "false" : null },
    Continue: { tagName: "BUTTON", innerText: "Continue", disabled: false, offsetParent: {}, getClientRects: () => [{}], getAttribute: (name) => name === "aria-disabled" ? "false" : null },
    Publish: { tagName: "BUTTON", innerText: "Publish", disabled: false, offsetParent: {}, getClientRects: () => [{}], getAttribute: () => null, click() { throw new Error("safe diagnostic must not publish"); } },
  };
  buttons.Next.click = () => { loaded.context.setBodyText("Delivery details"); buttonLabels = ["Continue"]; };
  buttons.Continue.click = () => { loaded.context.setBodyText("Review your listing · Huntington Station, NY 11746"); buttonLabels = ["Publish"]; };
  loaded.context.document.querySelectorAll = (selector) => selector === 'button,[role="button"],input[type="button"]' || selector === 'button,[role="button"],input[type="button"],input[type="submit"]' ? buttonLabels.map((label) => buttons[label]) : [];
  const result = await loaded.contentApi.ppVerifyFacebookLocationAtReview("Huntington Station, NY 11746");
  assert.equal(result.filled, true, JSON.stringify(result));
  assert.equal(result.verified_value, "Huntington Station, NY 11746");
  assert.equal(result.diagnostic.stage, "LOCATION_VERIFIED_AFTER_SAFE_CONTINUE");
  assert.equal(result.diagnostic.next_clicked, true);
  assert.deepEqual(Array.from(result.diagnostic.stages, (stage) => stage.phase).filter((phase) => phase === "AFTER_SAFE_CONTINUE").length, 2);
  assert.equal(result.diagnostic.stages.at(-1).final_publish_boundary, true);
  assert.equal(buttonLabels[0], "Publish");
  assert.equal(loaded.contentApi.ppFacebookVisibleLocationMatch("Huntington Station, NY 11746", "Review Huntington Station, New York 11746"), true);
  assert.equal(loaded.contentApi.ppFacebookVisibleLocationMatch("Huntington Station, NY 11746", "Review Huntington Station, NY 11747"), false);
});

test("Facebook location text alone is not accepted until the exact autocomplete suggestion is selected", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", { bodyText: "Create a listing" });
  loaded.fields.location.attrs["aria-expanded"] = "true";
  let optionVisible = true;
  const suggestion = {
    tagName: "BUTTON", innerText: "Huntington Station, New York", textContent: "Huntington Station, New York",
    attrs: { role: "button", "aria-selected": "false" }, getAttribute(name) { return this.attrs[name] ?? null; },
    getClientRects() { return optionVisible ? [{}] : []; }, closest() { return this; },
    click() { optionVisible = false; suggestion.attrs["aria-selected"] = "true"; loaded.fields.location.value = "Huntington Station"; loaded.fields.location.attrs["aria-expanded"] = "false"; },
  };
  const originalQuerySelectorAll = loaded.document.querySelectorAll.bind(loaded.document);
  loaded.document.querySelectorAll = (selector) => selector === '[role="option"],[role="radio"],[role="menuitem"],input[type="radio"],label,button,[role="button"],li,[tabindex="0"]'
    ? (optionVisible ? [suggestion] : [])
    : originalQuerySelectorAll(selector);
  const result = await loaded.contentApi.ppVerifyFacebookLocationAtReview("Huntington Station, NY 11746");
  assert.equal(result.filled, true, JSON.stringify(result));
  assert.equal(result.diagnostic.stage, "LOCATION_VERIFIED_BY_SELECTED_SUGGESTION");
  assert.equal(result.diagnostic.location_option_selection.option_dismissed, true);
  assert.equal(result.diagnostic.location_option_selection.input_value_matches, false);
  assert.equal(result.diagnostic.location_option_selection.selected_state, true);
});

test("Facebook preserves a prefilled collapsed Marketplace locality instead of opening a ZIP edit suggestion", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", { bodyText: "Create a listing" });
  loaded.fields.location.value = "Huntington Station";
  loaded.fields.location.attrs["aria-expanded"] = "false";
  let inputEvents = 0;
  loaded.fields.location.attrs.onDispatch = () => { inputEvents += 1; };
  const result = await loaded.contentApi.ppVerifyFacebookLocationAtReview("Huntington Station, NY 11746");
  assert.equal(result.filled, true, JSON.stringify(result));
  assert.equal(result.verified_value, "Huntington Station");
  assert.equal(result.diagnostic.stage, "LOCATION_VERIFIED_EXISTING_MARKETPLACE_LOCALITY");
  assert.equal(inputEvents, 0);
  assert.equal(loaded.fields.location.value, "Huntington Station");
});

test("Facebook location verifier does not treat a typed but unselected location as accepted", async () => {
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", { bodyText: "Create a listing" });
  loaded.document.querySelectorAll = (selector) => selector === '[role="option"],[role="radio"],[role="menuitem"],input[type="radio"],label,button,[role="button"],li,[tabindex="0"]' ? [] : [];
  const result = await loaded.contentApi.ppVerifyFacebookLocationAtReview("Huntington Station, NY 11746");
  assert.equal(result.filled, false, JSON.stringify(result));
  assert.equal(loaded.fields.location.value, "");
  assert.notEqual(result.diagnostic.stage, "LOCATION_VERIFIED_EXPLICIT_CONTROL");
});

test("Facebook authorized final submit clicks Publish and requires the resulting remote identity", async () => {
  let clicked = false;
  let loaded;
  const title = "Bound post-submit listing";
  const publish = {
    tagName: "BUTTON", innerText: "Publish", textContent: "Publish", value: "",
    disabled: false, offsetParent: {}, attrs: {},
    getAttribute(name) { return this.attrs[name] ?? null; },
    getClientRects() { return [{}]; },
    click() {
      clicked = true;
      loaded.context.location.pathname = "/marketplace/item/987654321/";
      loaded.context.location.href = "https://www.facebook.com/marketplace/item/987654321/";
      loaded.context.setBodyText(`${title} $29.99`);
    },
  };
  loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", { facebookButtons: [publish], bodyText: "Review your listing", reconciliationBodyNodes: [{ innerText: title, textContent: title, querySelectorAll: () => [] }] });
  loaded.fields.description.value = "Canonical test description.";
  const response = await new Promise((resolve) => loaded.listener({
    action: "posterpro_submit_operator_authorized_listing", marketplace: "facebook", payload: { title, price: 29.99, description: "Canonical test description." },
  }, {}, resolve));
  assert.equal(clicked, true);
  assert.equal(response.ok, true, JSON.stringify(response));
  assert.equal(response.submission_performed, true);
  assert.equal(response.external_listing_id, "987654321");
  assert.equal(response.external_url, "https://www.facebook.com/marketplace/item/987654321/");
});

test("Facebook production build may submit only through the operator-authorized final Publish path", async () => {
  let clicked = false;
  const title = "Bound production listing";
  let loaded;
  const publish = { tagName: "BUTTON", innerText: "Publish", textContent: "Publish", disabled: false, offsetParent: {}, getClientRects: () => [{}], getAttribute: () => null, click() { clicked = true; loaded.context.location.pathname = "/marketplace/item/987654321/"; loaded.context.location.href = "https://www.facebook.com/marketplace/item/987654321/"; loaded.context.setBodyText(`${title} $29.99`); } };
  loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", { facebookButtons: [publish], bodyText: "Review your listing", reconciliationBodyNodes: [{ innerText: title, textContent: title, querySelectorAll: () => [] }] });
  loaded.fields.description.value = "Canonical test description.";
  const response = await new Promise((resolve) => loaded.listener({
    action: "posterpro_submit_operator_authorized_listing", marketplace: "facebook", payload: { title, price: 29.99, description: "Canonical test description." },
  }, {}, resolve));
  assert.equal(response.ok, true, JSON.stringify(response));
  assert.equal(response.submission_performed, true);
  assert.equal(response.external_listing_id, "987654321");
  assert.equal(clicked, true, "the production build must honor the existing authorized CREATE");
});

test("Facebook offers OFF ignores an inactive minimum-price error and preserves authorized Publish", async () => {
  let clicked = false;
  const publish = { tagName: "BUTTON", innerText: "Publish", textContent: "Publish", disabled: false, offsetParent: {}, getClientRects: () => [{}], getAttribute: () => null, click() { clicked = true; } };
  const minimum = new (loadContentFixture().context.HTMLInputElement)("INPUT", { "aria-label": "Minimum price you'll consider", "aria-invalid": "true", "aria-disabled": "true" });
  minimum.disabled = true;
  const alert = { tagName: "DIV", innerText: "Input Minimum price you'll consider is invalid.", textContent: "Input Minimum price you'll consider is invalid.", offsetParent: {}, getClientRects: () => [{}], getAttribute(name) { return name === "role" ? "alert" : null; } };
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", {
    facebookButtons: [publish], bodyText: "Review your listing",
    links: [{ href: "https://www.facebook.com/marketplace/item/987654321/" }],
  });
  loaded.fields.description.value = "Canonical test description.";
  const originalQuerySelectorAll = loaded.document.querySelectorAll.bind(loaded.document);
  loaded.document.querySelectorAll = (selector) => {
    if (selector === 'input,textarea,select,[role="textbox"],[role="spinbutton"]') return [minimum];
    if (selector === '[aria-invalid="true"], [aria-required="true"]') return [minimum];
    if (selector === '[role="alert"]') return [alert];
    return originalQuerySelectorAll(selector);
  };
  const response = await new Promise((resolve) => loaded.listener({
    action: "posterpro_submit_operator_authorized_listing", marketplace: "facebook",
    payload: { description: "Canonical test description.", price: 29.99, facebook_offers_enabled: false },
  }, {}, resolve));
  assert.equal(clicked, true, JSON.stringify(response));
  assert.equal(response.submission_performed, true);
  assert.equal(minimum.value, "");
});

test("Facebook authorized workflow selects the configured rendered shipping service, advances safe steps, and stops before Publish", async () => {
  const { context, contentApi, fields } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  fields.description.value = "Canonical test description.";
  await contentApi.ppFacebookSetAndVerifyDescription(null, fields.description.value, "DESCRIPTION_AFTER_FILL");
  let dialogOpen = false;
  let serviceLoaded = false;
  let phase = "delivery";
  let publishClicked = false;
  let finalPublishEnabledAt = null;
  const makeControl = (label, { disabled = false, role = null, click = () => {} } = {}) => ({
    tagName: "BUTTON", innerText: label, textContent: label, disabled, offsetParent: {},
    getClientRects: () => [{}], getAttribute(name) { return name === "role" ? role : name === "aria-hidden" ? "false" : name === "aria-disabled" && this.disabled ? "true" : null; },
    click,
  });
  const publish = makeControl("Publish", { disabled: true, click: () => { publishClicked = true; } });
  const next = makeControl("Next", { disabled: true, click: () => { phase = "review"; } });
  const service = makeControl("USPS Ground Advantage", { role: "option", click: () => {
    dialogOpen = false; next.disabled = false; phase = "review";
    finalPublishEnabledAt = Date.now() + 1500;
    setTimeout(() => { publish.disabled = false; }, 1500);
  } });
  const selectLabel = makeControl("Select shipping label", { click: () => { dialogOpen = true; setTimeout(() => { serviceLoaded = true; }, 450); } });
  const dialog = { tagName: "DIV", get innerText() { return serviceLoaded ? "Shipping services" : "Loading..."; }, get textContent() { return this.innerText; }, offsetParent: {}, getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "dialog" : null, querySelectorAll: () => serviceLoaded ? [service] : [] };
  context.document.querySelectorAll = (selector) => {
    if (selector === '[role="dialog"], [role="listbox"], [role="menu"]') return dialogOpen ? [dialog] : [];
    if (/button/.test(selector)) return phase === "review" ? [publish] : [selectLabel, next];
    if (/button|role="button"|role="option"|role="radio"|role="menuitem"|data-testid/.test(selector)) return dialogOpen ? [service] : [];
    if (/h1,h2,h3/.test(selector) || /label,input,textarea,select/.test(selector) || /role="switch"/.test(selector) || /aria-invalid|role="alert"/.test(selector)) return [];
    return [];
  };
  const result = await contentApi.ppAdvanceFacebookToPublishBoundary({
    description: "Canonical test description.",
    delivery_method: "shipping", shipping: { domestic_service: "usps_ground_advantage", estimated: false },
  });
  assert.equal(result.ready, true, JSON.stringify(result));
  assert.equal(result.stage, "FINAL_PUBLISH_BOUNDARY");
  assert.ok(result.stages.some((stage) => stage.shipping_option_wait_ms >= 250), JSON.stringify(result.stages));
  const finalWait = result.stages.find((stage) => stage.phase === "FINAL_PUBLISH_STABILIZATION");
  assert.ok(finalWait, "a visible disabled final Publish control must enter bounded readiness stabilization");
  assert.ok(finalWait.wait_ms >= 600, `the state machine should wait for the delayed React enablement; waited ${finalWait.wait_ms}ms`);
  assert.equal(finalWait.ready, true);
  assert.ok(finalWait.samples.some((sample) => sample.final_publish_control_present && !sample.final_publish_boundary));
  assert.ok(finalWait.samples.at(-1).final_publish_boundary);
  assert.ok(Date.now() >= finalPublishEnabledAt);
  assert.equal(phase, "review");
  assert.equal(publishClicked, false);
});

test("Facebook final Publish readiness distinguishes a persistent validation blocker from a missing safe continuation", async () => {
  const { context, document, contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item", { bodyText: "Review your listing" });
  const publish = {
    tagName: "BUTTON", innerText: "Publish", textContent: "Publish", disabled: true, offsetParent: {},
    getClientRects: () => [{}], getAttribute(name) { return name === "aria-disabled" ? "true" : name === "aria-hidden" ? "false" : null; },
    click() { assert.fail("disabled Publish must never be activated"); },
  };
  const alert = { tagName: "DIV", innerText: "Choose a category before publishing", textContent: "Choose a category before publishing", offsetParent: {}, getClientRects: () => [{}], getAttribute(name) { return name === "role" ? "alert" : null; } };
  document.querySelectorAll = (selector) => {
    if (selector === 'button,[role="button"],input[type="button"],input[type="submit"]') return [publish];
    if (selector === '[role="alert"],[aria-live="assertive"]') return [alert];
    if (/h1,h2,h3|role="heading"/.test(selector)) return [];
    return [];
  };
  const initial = contentApi.ppFacebookStageSnapshot(2);
  assert.equal(initial.final_publish_control_present, true);
  assert.equal(initial.final_publish_boundary, false);
  const waited = await contentApi.ppWaitForFacebookFinalPublishBoundary(initial, 2, 500);
  assert.equal(waited.ready, false);
  assert.equal(waited.error_code, "FACEBOOK_FINAL_PUBLISH_VALIDATION_BLOCKED");
  assert.equal(waited.snapshot.validation_evidence.messages[0], "Choose a category before publishing");
  assert.ok(waited.samples.length >= 2);
});

test("Facebook shipping picker waits for its delayed portal to mount before deciding the service is absent", async () => {
  const { context, contentApi, fields } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  fields.description.value = "Canonical test description.";
  await contentApi.ppFacebookSetAndVerifyDescription(null, fields.description.value, "DESCRIPTION_AFTER_FILL");
  let dialogOpen = false;
  let serviceLoaded = false;
  let phase = "delivery";
  const makeControl = (label, { disabled = false, role = null, click = () => {} } = {}) => ({
    tagName: "BUTTON", innerText: label, textContent: label, disabled, offsetParent: {},
    getClientRects: () => [{}], getAttribute(name) { return name === "role" ? role : name === "aria-hidden" ? "false" : name === "aria-disabled" && disabled ? "true" : null; },
    click,
  });
  const publish = makeControl("Publish");
  const next = makeControl("Next", { disabled: true });
  const service = makeControl("USPS Ground Advantage", { role: "option", click: () => { dialogOpen = false; next.disabled = false; phase = "review"; } });
  const selectLabel = makeControl("Select shipping label", { click: () => {
    setTimeout(() => { dialogOpen = true; }, 300);
    setTimeout(() => { serviceLoaded = true; }, 650);
  } });
  const dialog = { tagName: "DIV", get innerText() { return serviceLoaded ? "Shipping services" : "Loading..."; }, get textContent() { return this.innerText; }, offsetParent: {}, getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "dialog" : null, querySelectorAll: () => serviceLoaded ? [service] : [] };
  context.document.querySelectorAll = (selector) => {
    if (selector === '[role="dialog"], [role="listbox"], [role="menu"]') return dialogOpen ? [dialog] : [];
    if (/button/.test(selector)) return phase === "review" ? [publish] : [selectLabel, next];
    if (/button|role="button"|role="option"|role="radio"|role="menuitem"|data-testid/.test(selector)) return dialogOpen && serviceLoaded ? [service] : [];
    if (/h1,h2,h3/.test(selector) || /label,input,textarea,select/.test(selector) || /role="switch"/.test(selector) || /aria-invalid|role="alert"/.test(selector)) return [];
    return [];
  };
  const result = await contentApi.ppAdvanceFacebookToPublishBoundary({
    description: "Canonical test description.",
    delivery_method: "shipping", shipping: { domestic_service: "usps_ground_advantage", estimated: false },
  });
  assert.equal(result.ready, true, JSON.stringify(result));
  assert.equal(result.stage, "FINAL_PUBLISH_BOUNDARY");
  assert.ok(result.stages.some((stage) => stage.shipping_option_wait_ms >= 600), JSON.stringify(result.stages));
  assert.equal(phase, "review");
});

test("Facebook shipping dialog selects only the exact rendered carrier and updates the delivery stage", async () => {
  const { context, contentApi, fields } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  fields.description.value = "Canonical test description.";
  await contentApi.ppFacebookSetAndVerifyDescription(null, fields.description.value, "DESCRIPTION_AFTER_FILL");
  let dialogOpen = false;
  let carrierMenuOpen = true;
  let phase = "delivery";
  let updateClicked = false;
  let carrierClickCount = 0;
  const makeControl = (label, { role = null, required = false, expanded = false, click = () => {} } = {}) => ({
    tagName: "LABEL", innerText: label, textContent: label, value: "", disabled: false, offsetParent: {},
    getClientRects: () => [{}], getAttribute(name) { return name === "role" ? role : name === "aria-required" && required ? "true" : name === "aria-expanded" && expanded ? "true" : name === "aria-hidden" ? "false" : null; },
    click,
  });
  const next = { tagName: "BUTTON", innerText: "Next", textContent: "Next", disabled: true, offsetParent: {}, getClientRects: () => [{}], getAttribute: () => null, click() { phase = "review"; } };
  const prepaid = makeControl("Shipping option Prepaid shipping label", { role: "combobox" });
  const weight = makeControl("Package weight", { role: "combobox" });
  const carrier = makeControl("Shipping carrier", { role: "combobox", expanded: true, click: () => { carrierClickCount += 1; carrierMenuOpen = true; } });
  const service = { tagName: "DIV", innerText: "USPS Ground Advantage", textContent: "USPS Ground Advantage", offsetParent: {}, getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "option" : null, click() { carrier.innerText = "Shipping carrier USPS Ground Advantage"; carrierMenuOpen = false; } };
  let dialog;
  const updateListeners = new Map();
  const update = { tagName: "BUTTON", innerText: "Update", textContent: "Update", disabled: false, isConnected: true, offsetParent: {}, getClientRects: () => [{}], getBoundingClientRect: () => ({ left: 20, top: 30, right: 100, bottom: 60, width: 80, height: 30 }), getAttribute: (name) => name === "role" ? "button" : null, closest: (selector) => selector === '[role="dialog"]' ? dialog : selector === '[role="button"]' ? update : null, contains: (node) => node === update, addEventListener(type, fn) { if (!updateListeners.has(type)) updateListeners.set(type, new Set()); updateListeners.get(type).add(fn); }, removeEventListener(type, fn) { updateListeners.get(type)?.delete(fn); }, click() { throw new Error("HTMLElement.click must not be called after synthetic sequence"); }, dispatchEvent(event) { event.target = update; for (const fn of updateListeners.get(event.type) || []) fn(event); dialog?._listeners?.get(event.type)?.forEach((fn) => fn(event)); if (event.type === "click") { updateClicked = true; dialogOpen = false; next.disabled = false; } return !event.defaultPrevented; } };
  dialog = { tagName: "DIV", innerText: "Change shipping method", textContent: "Change shipping method", isConnected: true, offsetParent: {}, getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "dialog" : null, _listeners: new Map(), addEventListener(type, fn) { if (!this._listeners.has(type)) this._listeners.set(type, new Set()); this._listeners.get(type).add(fn); }, removeEventListener(type, fn) { this._listeners.get(type)?.delete(fn); }, querySelectorAll: (selector) => /input,select,textarea|role="combobox"/.test(selector) ? [prepaid, weight, carrier] : /button|role="button"/.test(selector) ? [update] : [] };
  const listbox = { tagName: "DIV", innerText: "USPS Ground Advantage", offsetParent: {}, getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "listbox" : null, querySelectorAll: () => carrierMenuOpen ? [service] : [] };
  const selectLabel = { tagName: "BUTTON", innerText: "Select shipping label", textContent: "Select shipping label", disabled: false, offsetParent: {}, getClientRects: () => [{}], getAttribute: () => null, click() { setTimeout(() => { dialogOpen = true; }, 4200); } };
  context.document.querySelectorAll = (selector) => {
    if ([ '[role="dialog"]', '[role="listbox"]', '[role="dialog"],[role="listbox"],[role="menu"]', '[role="dialog"], [role="listbox"], [role="menu"]' ].includes(selector)) return [...(dialogOpen ? [dialog] : []), ...(carrierMenuOpen ? [listbox] : [])];
    if (/\[role="heading"\]/.test(selector) || /h1,h2,h3/.test(selector)) return [];
    if (/label,input,textarea,select|role="textbox"/.test(selector)) return dialogOpen ? [prepaid, weight, carrier] : [];
    if (/button|role="button"|input\[type="button"\]|input\[type="submit"\]/.test(selector)) return phase === "review" ? [{ ...update, innerText: "Publish", textContent: "Publish", disabled: false, click() { throw new Error("Publish must remain untouched in this stage test"); } }] : [selectLabel, next, ...(dialogOpen ? [update] : [])];
    if (/role="combobox"|role="option"|role="radio"|role="checkbox"|role="switch"/.test(selector)) return dialogOpen ? [dialog, prepaid, weight, carrier, ...(carrierMenuOpen ? [service] : [])] : [];
    if (selector === '[role="option"],[role="menuitem"],[role="radio"],[role="button"],button,li,[data-testid],[tabindex="0"]') return carrierMenuOpen ? [service] : [];
    if (/input\[type="radio"\],input\[type="checkbox"\]/.test(selector)) return [];
    return [];
  };
  context.document.contains = (node) => node === dialog || (node === update && dialogOpen);
  context.document.elementFromPoint = () => update;
  context.chrome.runtime.sendMessage = async (request) => {
    assert.equal(request.expected_transition, "SHIPPING_DIALOG_CLOSED");
    updateClicked = true; dialogOpen = false; next.disabled = false;
    return { ok: true, activation_method: "chrome_debugger_cdp_input", invocation_count: 1, commands: ["mouseMoved", "mousePressed", "mouseReleased"], attached: true, detached: true };
  };
  const result = await contentApi.ppAdvanceFacebookToPublishBoundary({ description: "Canonical test description.", delivery_method: "shipping", shipping: { domestic_service: "usps_ground_advantage", estimated: false } }, "test-one-use-token");
  assert.equal(result.ready, true, JSON.stringify(result));
  assert.equal(updateClicked, true);
  assert.equal(carrierClickCount, 0, "an already-expanded carrier chooser must not be toggled closed");
  assert.equal(phase, "review");
  assert.ok(result.stages.some((stage) => stage.shipping_dialog?.carrier_diagnostic?.selected?.includes("USPS Ground Advantage")), JSON.stringify(result.stages));
  assert.ok(result.stages.some((stage) => stage.shipping_dialog?.trace?.some((event) => event.phase === "CARRIER_STATE_REQUERIED" && event.selected.includes("USPS Ground Advantage"))), JSON.stringify(result.stages));
  assert.ok(result.stages.some((stage) => stage.shipping_dialog_wait_ms >= 4000), JSON.stringify(result.stages));
});

test("Facebook uses an estimated canonical package weight when the shipping dialog requires one", async () => {
  const { context, contentApi, fields } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  fields.description.value = "Canonical test description.";
  await contentApi.ppFacebookSetAndVerifyDescription(null, fields.description.value, "DESCRIPTION_AFTER_FILL");
  let dialogOpen = false;
  let carrierMenuOpen = true;
  let carrierOptionsLoaded = false;
  let weightMenuOpen = false;
  let weightDialogOpen = false;
  let updateClicked = false;
  let phase = "delivery";
  let weightChosen = false;
  let weightChooserCollapsed = false;
  let weightStateSurvivedChooserClose = false;
  let carrierStateSurvivedChooserClose = false;
  let freeShippingStateSurvivedChooserClose = false;
  let parentDialogSurvivedChooserClose = false;
  let updateSawPersistedState = false;
  let updateClickedAt = null;
  let updateNextWaitMs = null;
  let dialogGeneration = 0;
  let updateDialogGeneration = null;
  let stateSurvivedNext = false;
  let stateSurvivedContinue = false;
  let publishClicked = false;
  let freeShippingChecked = false;
  const makeEventTarget = () => {
    const listeners = new Map();
    return {
      addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(listener); },
      removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
      dispatch(type, target, existingEvent = null) { const event = existingEvent || { type, target, isTrusted: false, defaultPrevented: false }; for (const listener of listeners.get(type) || []) listener(event); return event; },
    };
  };
  const makeControl = (label, { role = null, required = false, click = () => {} } = {}) => ({ tagName: "LABEL", innerText: label, textContent: label, value: "", disabled: false, offsetParent: {}, getClientRects: () => [{}], getAttribute(name) { return name === "role" ? role : name === "aria-required" && required ? "true" : name === "aria-hidden" ? "false" : null; }, click });
  const stateIsPersisted = () => weightRadio.checked && /1-2 lbs/.test(weight.innerText)
    && /USPS Ground Advantage/.test(carrier.innerText) && freeShippingRadio.checked;
  const next = { tagName: "BUTTON", innerText: "Next", textContent: "Next", disabled: true, offsetParent: {}, getClientRects: () => [{}], getAttribute: () => null, click() { updateNextWaitMs = updateClickedAt === null ? null : Date.now() - updateClickedAt; stateSurvivedNext = stateIsPersisted(); phase = "delivery-options"; } };
  const continueButton = { tagName: "BUTTON", innerText: "Continue", textContent: "Continue", disabled: false, offsetParent: {}, getClientRects: () => [{}], getAttribute: () => null, click() { stateSurvivedContinue = stateIsPersisted(); phase = "review"; } };
  const prepaid = makeControl("Shipping option Prepaid shipping label", { role: "combobox" });
  const weight = makeControl("Package weight", { role: "combobox", required: true, click: () => {
    if (weightChosen && weightDialogOpen) {
      weightMenuOpen = false;
      weightDialogOpen = false;
      weightChooserCollapsed = true;
      weightStateSurvivedChooserClose = weightRadio.checked && /1-2 lbs/.test(weight.innerText);
      carrierStateSurvivedChooserClose = /USPS Ground Advantage/.test(carrier.innerText);
      freeShippingStateSurvivedChooserClose = freeShippingRadio.checked;
      parentDialogSurvivedChooserClose = dialogOpen;
      dialog = makeShippingDialog();
      setTimeout(() => { carrierOptionsLoaded = true; }, 300);
    } else {
      weightMenuOpen = true;
      weightDialogOpen = true;
    }
  } });
  const originalWeightGetAttribute = weight.getAttribute.bind(weight);
  weight.getAttribute = (name) => name === "aria-expanded" ? (weightDialogOpen ? "true" : "false") : originalWeightGetAttribute(name);
  const carrier = makeControl("Shipping carrier", { role: "combobox", click: () => { carrierMenuOpen = true; } });
  carrier.getAttribute = (name) => name === "role" ? "combobox" : name === "aria-expanded" ? "true" : null;
  const service = { tagName: "DIV", innerText: "USPS Ground Advantage", textContent: "USPS Ground Advantage", offsetParent: {}, getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "option" : null, click() { carrier.innerText = "Shipping carrier USPS Ground Advantage"; carrierMenuOpen = false; } };
  const weightLabel = { tagName: "LABEL", innerText: "1-2 lbs", textContent: "1-2 lbs", offsetParent: {}, getClientRects: () => [{}], getAttribute: () => null, click() { weightRadio.checked = true; weight.innerText = "Package weight 1-2 lbs"; carrier.innerText = "Shipping carrier USPS Ground Advantage ($9.81)"; carrierMenuOpen = false; weightChosen = true; dialog = makeShippingDialog(); } };
  const weightRadio = { tagName: "INPUT", type: "radio", checked: false, labels: [weightLabel], offsetParent: {}, getClientRects: () => [{}], getAttribute(name) { return name === "type" ? "radio" : null; }, closest(selector) { return selector.includes("label") ? weightLabel : null; }, click() { weightLabel.click(); } };
  const freeShippingLabel = { tagName: "LABEL", innerText: "Cover the shipping cost so buyers see free shipping.", textContent: "Cover the shipping cost so buyers see free shipping.", offsetParent: {}, getClientRects: () => [{}], getAttribute: () => null, click() { freeShippingRadio.checked = !freeShippingRadio.checked; freeShippingChecked = freeShippingRadio.checked; dialog = makeShippingDialog(); } };
  const freeShippingRadio = { tagName: "INPUT", type: "checkbox", checked: false, labels: [freeShippingLabel], offsetParent: {}, getClientRects: () => [{}], getAttribute(name) { return name === "type" ? "checkbox" : null; }, closest(selector) { return selector.includes("label") ? freeShippingLabel : null; } };
  const updateEvents = makeEventTarget();
  let hitSpan;
  let updateActivationSequence = [];
  const commitUpdate = () => {
    if (updateClicked) return;
    updateSawPersistedState = dialogOpen && weightDialogOpen === false && stateIsPersisted();
    updateDialogGeneration = dialog.generation;
    updateClicked = true;
    updateClickedAt = Date.now();
    dialogOpen = false;
    setTimeout(() => { next.disabled = false; }, 300);
  };
  const update = { ...updateEvents, tagName: "DIV", innerText: "Update", textContent: "Update", disabled: false, isConnected: true, className: "shipping-update", offsetParent: {}, getClientRects: () => [{}], getBoundingClientRect: () => ({ x: 20, y: 30, left: 20, top: 30, right: 100, bottom: 60, width: 80, height: 30 }), getAttribute: (name) => name === "role" ? "button" : null, closest(selector) { return selector.includes("[role=\"dialog\"]") || selector.includes("[role='dialog']") ? dialog : selector === "button" ? null : selector.includes("[role=\"button\"]") || selector.includes("[role='button']") ? update : null; }, contains(node) { return node === update || node === hitSpan; }, click() { throw new Error("Do not call HTMLElement.click() after the pointer/mouse sequence"); } };
  hitSpan = { tagName: "SPAN", innerText: "Update", textContent: "Update", parentElement: update, isConnected: true, getAttribute: () => null, dispatchEvent(event) { event.target = hitSpan; updateActivationSequence.push(event.type); updateEvents.dispatch(event.type, hitSpan, event); dialog?._eventTarget?.dispatch(event.type, hitSpan, event); if (event.type === "click" && updateActivationSequence.join(",") === "pointerdown,mousedown,pointerup,mouseup,click") commitUpdate(); return true; } };
  const makeShippingDialog = () => {
    const events = makeEventTarget();
    return { ...events, _eventTarget: events, generation: ++dialogGeneration, tagName: "DIV", innerText: "Change shipping method", textContent: "Change shipping method", offsetParent: {}, childElementCount: 6, getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "dialog" : null, querySelectorAll: (selector) => /input\[type="checkbox"\]|role="checkbox"/.test(selector) ? [freeShippingRadio] : /input,select,textarea|role="combobox"/.test(selector) ? [prepaid, weight, carrier] : /button|role="button"/.test(selector) ? [update] : [] };
  };
  let dialog = makeShippingDialog();
  const weightDialog = { tagName: "DIV", innerText: "Package weight and shipping options", offsetParent: {}, getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "dialog" : name === "aria-label" ? "Package weight and shipping options" : null, querySelectorAll: (selector) => /input\[type="radio"\]|role="radio"/.test(selector) ? [weightRadio] : /label/.test(selector) ? [weightLabel] : [] };
  const listbox = { tagName: "DIV", innerText: "USPS Ground Advantage", offsetParent: {}, getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "listbox" : null, querySelectorAll: () => carrierMenuOpen && carrierOptionsLoaded ? [service] : weightMenuOpen ? [weightLabel, weightRadio] : [] };
  const selectLabel = { tagName: "BUTTON", innerText: "Select shipping label", disabled: false, offsetParent: {}, getClientRects: () => [{}], getAttribute: () => null, click() { dialogOpen = true; } };
  context.document.querySelectorAll = (selector) => {
    if ([ '[role="dialog"]', '[role="listbox"]', '[role="dialog"],[role="listbox"],[role="menu"]', '[role="dialog"], [role="listbox"], [role="menu"]' ].includes(selector)) return [...(dialogOpen ? [dialog, ...(weightDialogOpen ? [weightDialog] : [])] : []), ...(carrierMenuOpen || weightMenuOpen ? [listbox] : [])];
    if (/h1,h2,h3|role="heading"/.test(selector)) return [];
    if (/label,input,textarea,select|role="textbox"/.test(selector)) return dialogOpen ? [prepaid, weight, carrier, ...(weightDialogOpen ? [weightLabel, weightRadio] : []), freeShippingLabel, freeShippingRadio] : [];
    if (/button|role="button"|input\[type="button"\]|input\[type="submit"\]/.test(selector)) return phase === "review" ? [{ ...update, innerText: "Publish", textContent: "Publish", disabled: false, click() { publishClicked = true; throw new Error("Safe flow must not click Publish"); } }] : phase === "delivery-options" ? [continueButton] : [selectLabel, next, ...(dialogOpen ? [update] : [])];
    if (/role="combobox"|role="option"|role="radio"|role="checkbox"|role="switch"/.test(selector)) return dialogOpen ? [dialog, prepaid, weight, carrier, ...(weightDialogOpen ? [weightDialog, weightRadio] : []), freeShippingRadio, ...(carrierMenuOpen && carrierOptionsLoaded ? [service] : []), ...(weightMenuOpen ? [weightLabel, weightRadio] : [])] : [];
    if (selector === '[role="option"],[role="menuitem"],[role="radio"],input[type="radio"],label,[role="button"],button,li,[data-testid],[tabindex="0"]') return carrierMenuOpen && carrierOptionsLoaded ? [service] : weightMenuOpen ? [weightLabel, weightRadio] : [];
    if (/input\[type="radio"\],input\[type="checkbox"\]/.test(selector)) return dialogOpen ? [weightRadio, freeShippingRadio] : [];
    return [];
  };
  context.document.contains = (node) => node === dialog || ((node === update || node === hitSpan) && dialogOpen);
  context.document.elementFromPoint = () => hitSpan;
  context.chrome.runtime.sendMessage = async (request) => {
    assert.equal(request.action, "posterpro_marketplace_control_activation");
    assert.equal(request.marketplace, "facebook");
    assert.equal(request.expected_stage, "FACEBOOK_SHIPPING_UPDATE");
    assert.equal(request.expected_transition, "SHIPPING_DIALOG_CLOSED");
    assert.equal(request.target.label, "Update");
    assert.equal(request.target.role, "button");
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      updateActivationSequence.push(type);
      const event = { type, target: hitSpan, isTrusted: true, defaultPrevented: false };
      updateEvents.dispatch(type, hitSpan, event);
      for (const fn of dialog?._listeners?.get(type) || []) fn(event);
    }
    commitUpdate();
    return { ok: true, activation_method: "chrome_debugger_cdp_input", invocation_count: 1, commands: ["mouseMoved", "mousePressed", "mouseReleased"], attached: true, detached: true, coordinate_space: "main_frame_viewport_css_pixels" };
  };
  const priorFormReadiness = { after_initial_fill: { checkpoint: "A_AFTER_INITIAL_FILL_BEFORE_SHIPPING", next: { disabled_property: true } }, field_transition_timeline: [{ field: "category", next: { disabled_property: true } }] };
  const result = await contentApi.ppAdvanceFacebookToPublishBoundary({ description: "Canonical test description.", delivery_method: "shipping", shipping: { domestic_service: "usps_ground_advantage", estimated: true, free_shipping: true, buyer_pays_shipping: false, package_weight: 2, package_dimensions: { length: 12, width: 10, height: 6 }, estimated_fields: ["package_weight", "package_dimensions"] } }, "test-operation-token", priorFormReadiness);
  assert.equal(result.ready, true, JSON.stringify(result));
  assert.equal(updateClicked, true);
  assert.equal(weightChosen, true, "the canonical estimated weight must be selected before service options are queried");
  assert.equal(weightChooserCollapsed, true, "the still-expanded Facebook weight chooser must be explicitly dismissed before Update");
  assert.equal(weightStateSurvivedChooserClose, true, "the checked 1-2 lb native radio must stay checked after closing the nested chooser");
  assert.equal(carrierStateSurvivedChooserClose, true, "USPS Ground Advantage must remain selected after closing the nested chooser");
  assert.equal(freeShippingStateSurvivedChooserClose, false, "closing the nested chooser must not toggle the free-shipping checkbox");
  assert.equal(parentDialogSurvivedChooserClose, true, "closing the nested chooser must leave Change shipping method open");
  assert.equal(carrierOptionsLoaded, true);
  assert.equal(freeShippingChecked, true, "the existing listing shipping preference is applied and verified");
  assert.equal(updateSawPersistedState, true, "Update must only commit after all desired state is present in the still-open parent dialog");
  assert.equal(updateDialogGeneration, dialogGeneration, "Update must be found on the latest React-rendered parent dialog, not a detached snapshot");
  assert.equal(dialogOpen, false, "Update must close the parent Change shipping method dialog");
  assert.equal(next.disabled, false, "successful Update must enable Next");
  assert.ok(updateNextWaitMs >= 250, `the state machine must wait for React to enable Next after modal close; waited ${updateNextWaitMs}ms`);
  assert.equal(stateSurvivedNext, true, "weight, carrier, and free shipping must remain selected after Next");
  assert.equal(stateSurvivedContinue, true, "weight, carrier, and free shipping must remain selected through Continue");
  assert.equal(publishClicked, false);
  assert.equal(result.stages[0].phase, "FORM_READINESS_A");
  assert.equal(result.stages[0].form_readiness.checkpoint, "A_AFTER_INITIAL_FILL_BEFORE_SHIPPING");
  assert.ok(result.stages.some((stage) => stage.shipping_dialog?.trace?.some((event) => event.phase === "FORM_READINESS_B" && event.form_readiness.checkpoint === "B_IMMEDIATELY_BEFORE_SHIPPING_UPDATE_ACTIVATION")), "checkpoint B must precede Update activation");
  assert.ok(result.stages.some((stage) => stage.shipping_dialog?.trace?.some((event) => event.phase === "UPDATE_REACT_RESPONSE" && event.form_readiness_after_shipping_commit?.checkpoint === "C_AFTER_SHIPPING_COMMIT_BEFORE_NEXT_STABILIZATION")), "checkpoint C must follow commit and precede Next stabilization");
  assert.match(weight.innerText, /1-2 lbs/);
  assert.ok(result.stages.some((stage) => stage.shipping_dialog?.weight_diagnostic?.selected_option === "1-2 lbs" && stage.shipping_dialog.weight_diagnostic.actionable_element?.type === "radio"), JSON.stringify(result.stages));
  assert.ok(result.stages.some((stage) => stage.shipping_dialog?.carrier_diagnostic?.selected?.includes("USPS Ground Advantage")), JSON.stringify(result.stages));
  assert.ok(result.stages.some((stage) => stage.shipping_dialog?.trace?.some((event) => event.phase === "CARRIER_STATE_REQUERIED" && event.selected.includes("USPS Ground Advantage"))), JSON.stringify(result.stages));
  assert.ok(result.stages.some((stage) => stage.shipping_dialog?.trace?.some((event) => event.phase === "WEIGHT_STATE_REQUERIED" && /1-2 lbs/.test(event.selected))), JSON.stringify(result.stages));
  assert.ok(result.stages.some((stage) => stage.shipping_dialog?.trace?.some((event) => event.phase === "PRE_UPDATE_STATE_VERIFIED" && event.parent_dialog_open && /1-2 lbs/.test(event.weight) && /USPS Ground Advantage/.test(event.carrier) && event.free_shipping_checked)), JSON.stringify(result.stages));
  assert.ok(result.stages.some((stage) => stage.shipping_dialog?.trace?.some((event) => event.phase === "AFTER_UPDATE_VERIFY" && event.dialog_open === false && event.next_enabled)), JSON.stringify(result.stages));
  const updateResolution = result.stages.find((stage) => stage.shipping_dialog?.trace?.some((event) => event.phase === "UPDATE_TARGET_RESOLVED"))?.shipping_dialog.trace.find((event) => event.phase === "UPDATE_TARGET_RESOLVED");
  const updateResponse = result.stages.find((stage) => stage.shipping_dialog?.trace?.some((event) => event.phase === "UPDATE_REACT_RESPONSE"))?.shipping_dialog.trace.find((event) => event.phase === "UPDATE_REACT_RESPONSE");
  assert.equal(updateResolution.update_match_count, 1);
  assert.equal(updateResolution.selected_target.tag, "div");
  assert.equal(updateResolution.selected_target.role, "button");
  assert.equal(updateResolution.selected_target.is_connected, true);
  assert.equal(updateResolution.selected_target.document_contains, true);
  assert.equal(updateResolution.selected_target.closest_dialog_is_current, true);
  assert.equal(updateResolution.selected_target.hit_is_target_or_descendant, true);
  assert.equal(JSON.stringify(updateResponse.event_counts), JSON.stringify({ pointermove: 0, mousemove: 0, pointerdown: 1, mousedown: 1, pointerup: 1, mouseup: 1, click: 1 }));
  assert.deepEqual(updateActivationSequence, ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]);
  assert.equal(updateResponse.activation_method, "chrome_debugger_cdp_input");
  assert.equal(updateResponse.events.every((event) => event.trusted === true), true, "fixture models events observed after browser-level activation");
  assert.equal(updateResponse.events.every((event) => event.target_is_update_descendant), true, "physical hit descendant events bubble through the actionable Update control");
  assert.deepEqual(Array.from(updateResponse.browser_input.commands), ["mouseMoved", "mousePressed", "mouseReleased"]);
  assert.equal(updateResponse.browser_input.attached, true);
  assert.equal(updateResponse.browser_input.detached, true);
  assert.equal(updateResponse.dialog_open, false);
  assert.equal(updateResponse.next_enabled, false, "the immediate response is recorded before the delayed Next enablement");
  assert.equal(updateResponse.activation_invocation_count, 1);
  assert.ok(result.stages.some((stage) => stage.shipping_dialog?.trace?.some((event) => event.phase === "WEIGHT_CHOOSER_CLOSED" && event.dialog?.heading === "Change shipping method")), JSON.stringify(result.stages));
  assert.equal(result.stages.at(-1).final_publish_boundary, true, "the safe state machine must reach the real final Publish boundary");
  assert.deepEqual(Array.from(result.stages.filter((stage) => stage.transition).map((stage) => stage.transition.control)), ["Next", "Continue"]);
  assert.equal(result.stages.at(-1).final_publish_labels.includes("Publish"), true);
});

test("Facebook Update diagnostics distinguish current, stale, duplicate, covered, and validation-blocked targets", async () => {
  const { context, contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  let hitTarget = null;
  context.document.contains = (node) => node.isConnected === true;
  context.document.elementFromPoint = () => hitTarget;
  const makeNode = ({ tag = "DIV", role = "button", label = "Update", connected = true, rect = { left: 10, top: 20, right: 90, bottom: 50, width: 80, height: 30 }, click = () => {} } = {}) => {
    const listeners = new Map();
    return {
      tagName: tag, innerText: label, textContent: label, className: "fb-update-target", isConnected: connected,
      disabled: false, offsetParent: connected ? {} : null, getClientRects: () => connected ? [{}] : [], getBoundingClientRect: () => rect,
      getAttribute(name) { return name === "role" ? role : name === "type" ? "button" : name === "aria-label" ? label : null; },
      closest(selector) { return selector === '[role="dialog"]' ? (this.isConnected ? currentDialog : null) : selector === '[role="button"]' ? this : selector === "button" && tag === "BUTTON" ? this : null; },
      contains(node) { return node === this; },
      addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(listener); },
      removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
      click() { click(this, listeners); },
      dispatchEvent(event) {
        event.target = this;
        for (const listener of listeners.get(event.type) || []) listener(event);
        for (const listener of currentDialog?._listeners?.get(event.type) || []) listener(event);
        click(this, listeners, event);
        return !event.defaultPrevented;
      },
      fire(type, target = this, event = { type, target, isTrusted: false, defaultPrevented: false }) { for (const listener of listeners.get(type) || []) listener(event); return event; },
    };
  };
  let currentDialog;
  const makeDialog = (candidates, nodes = []) => ({
    tagName: "DIV", innerText: "Change shipping method", textContent: "Change shipping method", isConnected: true, offsetParent: {}, getClientRects: () => [{}],
    getAttribute: (name) => name === "role" ? "dialog" : null,
    _listeners: new Map(),
    addEventListener(type, listener) { if (!this._listeners.has(type)) this._listeners.set(type, new Set()); this._listeners.get(type).add(listener); },
    removeEventListener(type, listener) { this._listeners.get(type)?.delete(listener); },
    querySelectorAll(selector) {
      if (/button|role="button"/.test(selector)) return candidates;
      if (selector === "*") return nodes;
      if (/input,textarea,select|role="combobox"/.test(selector)) return [];
      return [];
    },
  });
  const one = makeNode();
  currentDialog = makeDialog([one]);
  hitTarget = one;
  const connected = contentApi.ppFacebookUpdateTargetEvidence(one, currentDialog);
  assert.equal(connected.is_connected, true);
  assert.equal(connected.document_contains, true);
  assert.equal(connected.closest_dialog_is_current, true);
  assert.equal(connected.nonzero_rect, true);
  assert.equal(connected.hit_is_target_or_descendant, true);
  assert.equal(contentApi.ppFacebookUpdateCandidates(currentDialog).length, 1);

  const staleBeforeRerender = makeNode({ connected: false });
  const staleDialog = makeDialog([staleBeforeRerender]);
  const freshAfterRerender = makeNode();
  currentDialog = makeDialog([freshAfterRerender]);
  const rerenderResolution = contentApi.ppFacebookResolveUpdateTarget(currentDialog);
  assert.equal(rerenderResolution.candidates[0], freshAfterRerender);
  assert.notEqual(rerenderResolution.candidates[0], staleBeforeRerender, "Update resolution uses the current React dialog, not the detached prior node");
  assert.notEqual(staleDialog, currentDialog);

  const duplicate = makeNode({ label: "Update" });
  currentDialog = makeDialog([one, duplicate]);
  const duplicateResolution = contentApi.ppFacebookResolveUpdateTarget(currentDialog);
  assert.equal(duplicateResolution.match_count, 2, "duplicate exact Update actions are observable before activation");
  assert.equal(duplicateResolution.error_code, "FACEBOOK_UPDATE_TARGET_AMBIGUOUS");

  const overlay = makeNode({ label: "Loading overlay" });
  currentDialog = makeDialog([one]);
  hitTarget = overlay;
  const covered = contentApi.ppFacebookUpdateTargetEvidence(one, currentDialog);
  assert.equal(covered.hit_is_target_or_descendant, false);
  assert.equal(covered.covering_element.label, "Loading overlay");

  const stale = makeNode({ connected: false, click: () => {} });
  currentDialog = makeDialog([stale]);
  const staleEvidence = contentApi.ppFacebookUpdateTargetEvidence(stale, currentDialog);
  assert.equal(staleEvidence.is_connected, false);
  assert.equal(staleEvidence.document_contains, false);
  assert.equal(staleEvidence.closest_dialog_is_current, false, "a detached candidate cannot prove current dialog ancestry");
  const detachedActivation = await contentApi.ppFacebookActivateUpdateOnce(currentDialog, stale, "test-token");
  assert.equal(detachedActivation.invocation_count, 0);
  assert.equal(detachedActivation.error, "FACEBOOK_UPDATE_TARGET_NOT_CURRENT");

  const error = { tagName: "SPAN", innerText: "Choose a package weight", textContent: "Choose a package weight", offsetParent: {}, getClientRects: () => [{}], getAttribute: (name) => name === "role" ? "alert" : null, children: [] };
  let dialogOpen = true;
  let validationVisible = false;
  const validationTarget = makeNode({ click: (target, listeners, event) => {
    if (event.type !== "click") return;
    validationVisible = true;
    dialogOpen = true; // Facebook can receive the click but keep the modal open on validation.
  } });
  currentDialog = makeDialog([validationTarget], [error]);
  currentDialog.querySelectorAll = (selector) => selector === "*" ? (validationVisible ? [error] : []) : /button|role="button"/.test(selector) ? [validationTarget] : [];
  hitTarget = validationTarget;
  const beforeValidation = contentApi.ppFacebookShippingValidationSnapshot(currentDialog);
  const activation = await contentApi.ppFacebookActivateUpdateOnce(currentDialog, validationTarget, null);
  validationVisible = true;
  const afterValidation = contentApi.ppFacebookShippingValidationSnapshot(currentDialog);
  assert.equal(activation.invocation_count, 0, "no CDP input is sent without the active job's one-use authorization");
  assert.equal(activation.events.length, 0);
  assert.equal(dialogOpen, true, "the simulated validation refusal leaves the parent dialog open");
  assert.match(afterValidation.messages[0].text, /package weight/i);
  assert.notEqual(JSON.stringify(beforeValidation), JSON.stringify(afterValidation));
});

test("Facebook adapter requests one scoped browser activation and captures trusted page delivery", async () => {
  const { context, document, contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const eventCounts = Object.fromEntries(["pointerdown", "mousedown", "pointerup", "mouseup", "click"].map((type) => [type, 0]));
  const listenerScopes = [];
  const dialogListeners = new Map();
  const targetListeners = new Map();
  const dialog = {
    tagName: "DIV", innerText: "Change shipping method", textContent: "Change shipping method", isConnected: true,
    getAttribute: (name) => name === "role" ? "dialog" : null,
    addEventListener(type, fn) { if (!dialogListeners.has(type)) dialogListeners.set(type, new Set()); dialogListeners.get(type).add(fn); },
    removeEventListener(type, fn) { dialogListeners.get(type)?.delete(fn); },
  };
  let sequence = [];
  let commitCount = 0;
  const update = {
    tagName: "DIV", innerText: "Update", textContent: "Update", isConnected: true, disabled: false,
    getAttribute(name) { return name === "role" ? "button" : name === "aria-label" ? "Update" : null; },
    closest(selector) { return selector === '[role="dialog"]' ? dialog : selector === '[role="button"]' ? this : null; },
    contains(node) { return node === this || node === hitSpan; },
    getBoundingClientRect: () => ({ left: 10, top: 20, width: 80, height: 30, right: 90, bottom: 50 }),
    addEventListener(type, fn) { if (!targetListeners.has(type)) targetListeners.set(type, new Set()); targetListeners.get(type).add(fn); },
    removeEventListener(type, fn) { targetListeners.get(type)?.delete(fn); },
    click() { eventCounts.click += 1; },
  };
  const hitSpan = {
    tagName: "SPAN", innerText: "Update", textContent: "Update", isConnected: true,
    getAttribute: () => null,
    dispatchEvent(event) {
      event.target = this;
      sequence.push(event.type);
      eventCounts[event.type] += 1;
      for (const fn of targetListeners.get(event.type) || []) { fn(event); listenerScopes.push("target"); }
      for (const fn of dialogListeners.get(event.type) || []) { fn(event); listenerScopes.push("dialog"); }
      if (event.type === "mousedown") commitCount += 1; // controlled fixture: handler is bound to mousedown
      return !event.defaultPrevented;
    },
  };
  context.document.contains = (node) => node === dialog || node === update || node === hitSpan;
  context.document.elementFromPoint = () => hitSpan;

  let request = null;
  context.chrome.runtime.sendMessage = async (message) => {
    request = message;
    for (const type of ["pointermove", "mousemove", "pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      const event = { type, isTrusted: true, clientX: 50, clientY: 35, pageX: 50, pageY: 35, screenX: 70, screenY: 90, button: type.includes("down") || type === "click" ? 0 : -1, buttons: type.includes("down") ? 1 : 0, target: hitSpan, defaultPrevented: false, composedPath: () => [hitSpan, update, dialog, document, context] };
      for (const fn of context.__windowEventListeners.get(type) || []) fn(event);
      for (const fn of context.__documentEventListeners.get(type) || []) fn(event);
      for (const fn of dialogListeners.get(type) || []) fn(event);
      for (const fn of targetListeners.get(type) || []) fn(event);
    }
    return { ok: true, activation_method: "chrome_debugger_cdp_input", invocation_count: 1,
      activation_coordinate: { x: 50, y: 35, rounding_method: "Math.round_stabilized_center" },
      content_integer_hit_test: { target_inside_update: true, target: { tag: "span", label: "Update" } },
      commands: ["mouseMoved", "mousePressed", "mouseReleased"], command_results: [{ label: "mouseMoved", status: "resolved" }, { label: "mousePressed", status: "resolved" }, { label: "mouseReleased", status: "resolved" }], attached: true, detached: true, attach_result: { status: "resolved" }, browser_context: { focused_for_input: true }, protocol_layout_metrics: { pageScaleFactor: 1 } };
  };

  const activation = await contentApi.ppFacebookActivateUpdateOnce(dialog, update, "authorized-one-use-token");
  assert.equal(activation.error, null);
  assert.equal(activation.invocation_count, 1);
  assert.equal(activation.activation_method, "chrome_debugger_cdp_input");
  assert.equal(JSON.stringify(activation.event_counts), JSON.stringify({ pointermove: 1, mousemove: 1, pointerdown: 1, mousedown: 1, pointerup: 1, mouseup: 1, click: 1 }));
  assert.ok(activation.events.every((event) => event.trusted && event.client_x === 50 && event.client_y === 35));
  assert.ok(activation.events.every((event) => event.listener_scopes.includes("window") && event.listener_scopes.includes("document") && event.listener_scopes.includes("dialog") && event.listener_scopes.includes("target")));
  assert.equal(activation.delivery_status, "CDP_INPUT_OBSERVED_AT_VALIDATED_CONTROL");
  assert.equal(sequence.length, 0);
  assert.equal(commitCount, 0, "content script sends no synthetic event sequence itself");
  assert.equal(request.expected_transition, "SHIPPING_DIALOG_CLOSED");
  assert.equal(request.target.x, 50);
  assert.equal(request.target.y, 35);
  assert.equal(request.target.hit_target_inside, true);
  assert.equal(activation.actionable_target.role, "button");
  assert.equal(JSON.stringify(activation.physical_hit_target), JSON.stringify({ tag: "span", role: null, label: "Update" }));
  assert.deepEqual(Array.from(activation.browser_input.commands), ["mouseMoved", "mousePressed", "mouseReleased"]);
  assert.equal(activation.browser_input.attach_result.status, "resolved");
  assert.equal(activation.browser_context.focused_for_input, true);
  assert.equal(activation.protocol_layout_metrics.pageScaleFactor, 1);
  assert.equal(activation.viewport_environment.visual_viewport.scale, 1);
});

test("Facebook marketplace-control request refuses an unrelated hit target before CDP input", async () => {
  const { context, contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const dialog = { isConnected: true, getAttribute: (name) => name === "role" ? "dialog" : null };
  const target = {
    tagName: "DIV", innerText: "Update", textContent: "Update", isConnected: true,
    getAttribute: (name) => name === "role" ? "button" : null,
    closest: (selector) => selector === '[role="dialog"]' ? dialog : null,
    contains: () => false,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 20, height: 20 }),
  };
  const overlay = { tagName: "DIV", innerText: "Loading", getAttribute: () => null };
  context.document.contains = (node) => node === dialog || node === target;
  context.document.elementFromPoint = () => overlay;
  let sent = false;
  context.chrome.runtime.sendMessage = async () => { sent = true; return { ok: true }; };
  const result = await contentApi.ppFacebookActivateUpdateOnce(dialog, target, "authorized-one-use-token");
  assert.equal(result.error, "FACEBOOK_UPDATE_HIT_TARGET_OUTSIDE_ACTION");
  assert.equal(sent, false);
  assert.equal(Object.values(result.event_counts).reduce((sum, count) => sum + count, 0), 0);
});

test("Facebook weight range resolver handles shared band boundaries without selecting a heavier band", () => {
  const { contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  assert.equal(contentApi.ppFacebookWeightOptionMatches("1-2 lbs", 1.5), true);
  assert.equal(contentApi.ppFacebookWeightOptionMatches("1-2 lbs", 2), true);
  assert.equal(contentApi.ppFacebookWeightOptionMatches("2-5 lbs", 2), false);
  assert.equal(contentApi.ppFacebookWeightOptionMatches("2-5 lbs", 2.1), true);
  assert.equal(contentApi.ppFacebookWeightOptionMatches("Under 0.5 lbs", 2), false);
});

test("Facebook safe location verifier never treats Publish as a safe navigation control", async () => {
  const publish = { tagName: "BUTTON", innerText: "Publish", disabled: false, offsetParent: {}, getClientRects: () => [{}], getAttribute: () => null, click() { throw new Error("publish must not be clicked"); } };
  const loaded = loadContentFixture("www.facebook.com", "/marketplace/create/item", { bodyText: "Create a listing", facebookLocationAbsent: true });
  loaded.context.document.querySelectorAll = () => [publish];
  const result = await loaded.contentApi.ppVerifyFacebookLocationAtReview("Huntington Station, NY 11746");
  assert.equal(result.filled, true);
  assert.equal(result.diagnostic.stage, "LOCATION_INHERITED_BY_FACEBOOK");
  assert.equal(result.diagnostic.location_requirement_satisfied, true);
  assert.equal(result.diagnostic.location_value_verified, false);
  assert.equal(result.diagnostic.stages[0].final_publish_boundary, true);
});

test("Facebook category searches portal options after opening and clicks the actionable rendered option", async () => {
  const { context, contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  let opened = false;
  const trigger = {
    tagName: "LABEL", innerText: "Category", textContent: "Category", disabled: false,
    parentElement: { innerText: "Category" }, getClientRects: () => [{}],
    getAttribute: (name) => name === "role" ? "combobox" : null,
    matches: () => false,
    click: () => { opened = true; },
  };
  const option = {
    tagName: "BUTTON", innerText: "Clothing & Accessories", textContent: "Clothing & Accessories", disabled: false,
    parentElement: null, getClientRects: () => [{}],
    getAttribute: (name) => name === "role" ? "option" : null,
    matches: (selector) => selector.includes("role=\"option\"") || selector.includes("button"),
    closest: () => option,
    click: () => { trigger.innerText = "Clothing & Accessories"; trigger.textContent = trigger.innerText; },
  };
  const originalQueryAll = context.document.querySelectorAll.bind(context.document);
  context.document.querySelectorAll = (selector) => {
    if (selector === '[role="dialog"], [role="listbox"], [role="menu"]') return [];
    if (selector === '[role="option"], [role="menuitem"], [role="treeitem"]') return opened ? [option] : [];
    if (selector.startsWith('[role="option"], [role="menuitem"')) return opened ? [option] : [];
    return originalQueryAll(selector);
  };
  const result = await contentApi.ppSelectFacebookCategory(trigger, "Clothing");
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.value, "Clothing & Accessories");
  assert.equal(result.diagnostic.stage, "SELECTION_VERIFIED");
  assert.equal(result.diagnostic.selected_option, "Clothing & Accessories");
  assert.equal(result.diagnostic.facebook_category_family, "apparel");
  assert.deepEqual(Array.from(result.diagnostic.facebook_candidate_labels), ["Clothing", "Clothing & Accessories", "Apparel"]);
  assert.equal(result.diagnostic.clicked_element_structure.element.tag, "button");
  assert.equal(result.diagnostic.post_selection_field_text, "Clothing & Accessories");
});

test("Facebook category maps a household test item only to the rendered Household destination", async () => {
  const { context, contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  let opened = false;
  const trigger = {
    tagName: "LABEL", innerText: "Category", textContent: "Category", disabled: false,
    parentElement: { innerText: "Category" }, getClientRects: () => [{}],
    getAttribute: (name) => name === "role" ? "combobox" : null,
    matches: () => false,
    click: () => { opened = true; },
  };
  const option = {
    tagName: "BUTTON", innerText: "Household Shipping available", textContent: "Household Shipping available", disabled: false,
    parentElement: null, getClientRects: () => [{}],
    getAttribute: (name) => name === "role" ? "option" : null,
    matches: (selector) => selector.includes("role=\"option\"") || selector.includes("button"),
    closest: () => option,
    click: () => { trigger.innerText = "Household"; trigger.textContent = "Household"; },
  };
  const originalQueryAll = context.document.querySelectorAll.bind(context.document);
  context.document.querySelectorAll = (selector) => {
    if (selector === '[role="dialog"], [role="listbox"], [role="menu"]') return [];
    if (selector === '[role="option"], [role="menuitem"], [role="treeitem"]') return opened ? [option] : [];
    if (selector.startsWith('[role="option"], [role="menuitem"')) return opened ? [option] : [];
    return originalQueryAll(selector);
  };
  const result = await contentApi.ppSelectFacebookCategory(trigger, "Household", { title: "PosterPro Safe Form Test Household Item - Do Not Publish" });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.value, "Household");
  assert.equal(result.diagnostic.facebook_category_family, "household");
  assert.deepEqual(Array.from(result.diagnostic.facebook_candidate_labels), ["Household"]);
  assert.deepEqual(Array.from(result.diagnostic.selected_hierarchy_path), ["Household Shipping available"]);
});

test("Facebook accepts a newly rendered CSS background media preview without requiring an img", async () => {
  const { context, fields, contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item");
  const preview = {
    tagName: "DIV", innerText: "", textContent: "", attrs: { backgroundImage: 'url("blob:posterpro-preview")' },
    getAttribute: () => null, getClientRects: () => [{}],
  };
  const parent = fields.photos.parentElement;
  const originalQueryAll = parent.querySelectorAll.bind(parent);
  parent.querySelectorAll = (selector) => selector.startsWith("[style]") ? [preview] : originalQueryAll(selector);
  context.getComputedStyle = (node) => ({ visibility: "visible", display: "block", backgroundImage: node?.attrs?.backgroundImage || "none" });
  const before = contentApi.ppFacebookMediaSnapshot(fields.photos);
  preview.attrs.backgroundImage = 'url("blob:posterpro-preview-after-upload")';
  const evidence = contentApi.ppFacebookImagePreviewEvidence(fields.photos, before);
  assert.equal(evidence.accepted, true, JSON.stringify(evidence));
  assert.equal(evidence.accepted_preview.representation, "background");
  assert.equal(evidence.accepted_preview.source_kind, "blob");
});

test("Facebook form readiness distinguishes an unhydrated SPA from a changed form", async () => {
  const { contentApi } = loadContentFixture("www.facebook.com", "/marketplace/create/item", { formNeverReady: true });
  const ready = await contentApi.ppWaitForFacebookCreateFormReady(globalThis.POSTERPRO_MARKETPLACE_ADAPTERS?.facebook || {
    title: ['input[aria-label*="Title" i]'], price: ['input[aria-label*="Price" i]'], description: ['textarea'], photos: 'input[type="file"]',
  }, { timeoutMs: 15, pollMs: 1 });
  assert.equal(ready.ready, false);
  assert.equal(ready.anchors.title, false);
});

test("Mercari /sell/ route is treated as authenticated form context", async () => {
  const { listener } = loadContentFixture("www.mercari.com", "/sell/");
  const response = await new Promise((resolve) => {
    listener({
      action: "posterpro_fill_listing",
      marketplace: "mercari",
      payload: { title: "Test", price: 20, description: "Description", category: "Clothing", condition: "Used - good" },
    }, {}, resolve);
  });
  assert.equal(response.login_state, "LOGGED_IN", JSON.stringify(response));
  assert.notEqual(response.error_code, "UNKNOWN");
});

test("Mercari readiness waits for hydrated title, price, description, and photo anchors", async () => {
  const { listener } = loadContentFixture("www.mercari.com", "/sell/", { formReadyAfterMs: 350 });
  const response = await new Promise((resolve) => listener({
    action: "posterpro_diagnose_listing",
    marketplace: "mercari",
    payload: { title: "PosterPro Safe Form Test Garment - Do Not Publish", price: 1, description: "Temporary test; do not submit.", department: "Women", subcategory: "Tops", condition: "Used - good", delivery_method: "shipping" },
    images: [],
  }, {}, resolve));
  assert.equal(response.login_state, "LOGGED_IN", JSON.stringify(response));
  assert.equal(response.form_detected, true);
  assert.equal(response.form_readiness.form_ready_wait_started, true);
  assert.ok(response.form_readiness.form_ready_wait_ms >= 300);
  assert.deepEqual(JSON.parse(JSON.stringify(response.form_readiness.form_ready_anchors)), {
    title: true, price: true, description: true, photos: true, visible_form: false, core_anchor_count: 3,
  });
  assert.equal(response.field_results.some((field) => field.field === "title"), true);
  assert.equal(response.submission_performed, false);
});

test("Mercari no-form evidence distinguishes a hydration timeout from a changed form", async () => {
  const { context, contentApi } = loadContentFixture("www.mercari.com", "/sell/", {
    formNeverReady: true,
    bodyText: "List an item · Loading your form",
  });
  const adapter = context.POSTERPRO_MARKETPLACE_ADAPTERS.mercari;
  const readiness = await contentApi.ppWaitForMercariSellFormReady(adapter, { timeoutMs: 30, pollMs: 10 });
  assert.equal(readiness.ready, false);
  const page = contentApi.ppMercariPageEvidence(adapter, readiness.anchors);
  assert.equal(page.classification, "FORM_HYDRATION_TIMEOUT");
  assert.equal(page.evidence.login_state, "LOGGED_IN");
  assert.equal(page.evidence.sell_shell_visible, true);
  assert.equal(page.evidence.loading_indicators, true);
  assert.equal(page.evidence.document_ready_state, "complete");
  assert.equal(page.evidence.form_anchors.core_anchor_count, 0, JSON.stringify(page.evidence.form_anchors));
});

test("Mercari create cannot pass required-field gate when seller-required brand is absent", async () => {
  const { listener } = loadContentFixture("www.mercari.com", "/sell/");
  const response = await new Promise((resolve) => listener({
    action: "posterpro_fill_listing",
    marketplace: "mercari",
    payload: { title: "Specific item", price: 20, description: "A factual item description." },
    images: [{ data_url: "data:image/png;base64,aGk=", file_name: "photo.png" }],
  }, {}, resolve));
  assert.equal(response.capability_ready, false);
  assert.ok(Array.from(response.missing_required_fields).includes("brand"));
  assert.equal(response.field_results.find((field) => field.field === "brand").error_code, "FIELD_OR_MAPPED_VALUE_MISSING");
});

test("Mercari authorized submit requires success evidence and captures the resulting listing identity", async () => {
  const link = { href: "https://www.mercari.com/item/m123456789/" };
  const submit = { tagName: "BUTTON", innerText: "List", disabled: false, getAttribute: () => null, click() {} };
  const { listener } = loadContentFixture("www.mercari.com", "/sell/complete", {
    submitControl: submit,
    bodyText: "Your item is listed",
    links: [link],
  });
  const result = await new Promise((resolve) => listener({ action: "posterpro_submit_operator_authorized_listing", marketplace: "mercari" }, {}, resolve));
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.submission_performed, true);
  assert.equal(result.external_listing_id, "m123456789");
  assert.equal(result.external_url, "https://www.mercari.com/item/m123456789/");
});

test("Mercari does not click an ambiguous button or call an unverified result published", async () => {
  const submit = { tagName: "BUTTON", innerText: "List another item", disabled: false, getAttribute: () => null, click() { throw new Error("must not click"); } };
  const { listener } = loadContentFixture("www.mercari.com", "/sell/", { submitControl: submit });
  const result = await new Promise((resolve) => listener({ action: "posterpro_submit_operator_authorized_listing", marketplace: "mercari" }, {}, resolve));
  assert.equal(result.ok, false);
  assert.equal(result.submission_performed, false);
  assert.equal(result.error_code, "FINAL_SUBMIT_CONTROL_NOT_FOUND");
});

test("Mercari current /sell controls satisfy diagnostic without demanding hidden shipping fields", async () => {
  const option = (text) => new FakeOption(text);
  function FakeOption(text) {
    this.tagName = "BUTTON"; this.innerText = text; this.textContent = text; this.offsetParent = {}; this.selected = false;
    this.getAttribute = (name) => name === "aria-checked" && this.selected ? "true" : null;
    this.closest = () => this; this.click = () => { this.selected = true; };
  }
  const category = { tagName: "BUTTON", innerText: "Choose category", textContent: "Choose category", offsetParent: {}, disabled: false, parentElement: { innerText: "Category" }, getAttribute(name) { return name === "aria-hidden" ? "false" : null; }, click() {} };
  const condition = { tagName: "INPUT", attrs: { name: "sellCondition" }, disabled: false, offsetParent: {}, value: "", getAttribute(name) { return this.attrs[name] ?? (name === "aria-hidden" ? "false" : null); }, click() {} };
  const shipping = { tagName: "INPUT", attrs: { name: "deliveryOptions", "aria-label": "mercariShipping" }, disabled: false, offsetParent: {}, value: "", getAttribute(name) { return this.attrs[name] ?? (name === "aria-hidden" ? "false" : null); }, click() {} };
  const { listener } = loadContentFixture("www.mercari.com", "/sell/", {
    mercariControls: { category, condition, shipping },
    mercariOptions: [option("Clothing"), option("New"), option("Mercari prepaid label")],
  });
  // The category is found through the visible form label/wrapper just as it
  // is on Mercari's current unlabeled category button.
  const response = await new Promise((resolve) => listener({
    action: "posterpro_diagnose_listing", marketplace: "mercari",
    payload: { title: "PosterPro Safe Form Test Garment - Do Not Publish", price: 1, description: "Temporary diagnostic only", category_hint: "Clothing", condition: "New", delivery_method: "shipping", shipping: { mode: "shipping", shipping_payer: "buyer", shipping_method: "standard", parcel_size: "small", parcel_weight: "1 lb" } },
    images: [{ data_url: "data:image/png;base64,aGk=", file_name: "posterpro-safe-diagnostic.png" }],
  }, {}, resolve));
  assert.equal(response.login_state, "LOGGED_IN", JSON.stringify(response));
  assert.equal(response.capability_ready, true, JSON.stringify(response.field_results));
  assert.deepEqual(Array.from(response.missing_required_fields), []);
  assert.equal(response.field_results.find((field) => field.field === "condition").detected, true);
  assert.equal(response.field_results.find((field) => field.field === "shipping").detected, true);
  assert.equal(response.field_results.some((field) => ["shipping_payer", "shipping_method", "package"].includes(field.field)), false);
  assert.equal(response.submission_performed, false);
});

test("Mercari category drills through department and verifies the leaf; shipping selects prepaid label", async () => {
  const selected = (text) => {
    const option = {
      tagName: "BUTTON", innerText: text, textContent: text, offsetParent: {}, selected: false,
      getAttribute(name) { return name === "aria-checked" && this.selected ? "true" : null; },
      closest() { return this; }, click() { this.selected = true; },
    };
    return option;
  };
  const category = { tagName: "BUTTON", innerText: "Select category", textContent: "Select category", offsetParent: {}, disabled: false, parentElement: { innerText: "Category" }, getAttribute(name) { return name === "aria-hidden" ? "false" : null; }, click() {} };
  const condition = { tagName: "INPUT", attrs: { name: "sellCondition" }, disabled: false, offsetParent: {}, value: "", getAttribute(name) { return this.attrs[name] ?? (name === "aria-hidden" ? "false" : null); }, click() {} };
  const shipping = { tagName: "INPUT", attrs: { name: "deliveryOptions", "aria-label": "mercariShipping" }, disabled: false, offsetParent: {}, value: "", getAttribute(name) { return this.attrs[name] ?? (name === "aria-hidden" ? "false" : null); }, click() {} };
  const options = { mercariOptions: [] };
  const women = selected("Women");
  women.click = () => { women.selected = true; options.mercariOptions = [tops, selected("Dresses")]; };
  const good = selected("Good Gently used. One / few minor flaws. Functional.");
  good.click = () => { good.selected = true; options.mercariOptions = [selected("Prepaid label We'll email you a label and you'll ship the item.")]; };
  const tops = selected("Tops");
  tops.click = () => { tops.selected = true; options.mercariOptions = [good, selected("Fair Used, functional, multiple flaws / defects.")]; };
  condition.click = () => { options.mercariOptions = [good, selected("Fair Used, functional, multiple flaws / defects.")]; };
  const { listener } = loadContentFixture("www.mercari.com", "/sell/", {
    mercariControls: { category, condition, shipping },
    get mercariOptions() { return options.mercariOptions; },
  });
  options.mercariOptions = [women, selected("Men")];
  const response = await new Promise((resolve) => listener({
    action: "posterpro_diagnose_listing", marketplace: "mercari",
    payload: { title: "PosterPro Safe Form Test Garment - Do Not Publish", price: 1, description: "Temporary diagnostic only", category: "Clothing", department: "Women", condition: "Used - good", delivery_method: "shipping" },
    images: [{ data_url: "data:image/png;base64,aGk=", file_name: "posterpro-safe-diagnostic.png" }],
  }, {}, resolve));
  assert.equal(response.field_results.find((field) => field.field === "category").filled, true, JSON.stringify(response.field_results));
  assert.deepEqual(Array.from(response.field_results.find((field) => field.field === "category").interaction_diagnostic.category_path), ["Women", "Tops"]);
  assert.equal(response.field_results.find((field) => field.field === "shipping").filled, true, JSON.stringify(response.field_results));
  assert.equal(response.submission_performed, false);
});

test("Mercari condition interaction targets the requested Good card, not the first New radio", async () => {
  const selected = (label) => ({
    tagName: "INPUT", attrs: { name: "sellCondition" }, disabled: false, offsetParent: {}, value: "on", checked: false,
    parentElement: { innerText: label, textContent: label },
    getAttribute(name) { return this.attrs[name] ?? (name === "aria-hidden" ? "false" : null); },
    click() { this.checked = true; },
  });
  const newCondition = selected("New New with tags (NWT). Unopened packaging. Unused.");
  const goodCondition = selected("Good Gently used. One / few minor flaws. Functional.");
  const conditionGroup = { tagName: "BUTTON", innerText: "Condition", textContent: "Condition", offsetParent: {}, disabled: false, getAttribute(name) { return name === "aria-hidden" ? "false" : null; }, click() {} };
  const { listener } = loadContentFixture("www.mercari.com", "/sell/", {
    mercariControls: { condition: conditionGroup }, conditionInputs: [newCondition, goodCondition],
    mercariOptions: [newCondition, goodCondition],
  });
  const response = await new Promise((resolve) => listener({
    action: "posterpro_diagnose_listing", marketplace: "mercari",
    payload: { title: "Safe test", price: 1, description: "Diagnostic", category: "Clothing", condition: "Used - good", delivery_method: "shipping" },
    images: [{ data_url: "data:image/png;base64,aGk=", file_name: "posterpro-safe-diagnostic.png" }],
  }, {}, resolve));
  assert.ok(response.field_results, JSON.stringify(response));
  const result = response.field_results.find((field) => field.field === "condition");
  assert.equal(result.filled, true, JSON.stringify(result));
  assert.equal(newCondition.checked, false, JSON.stringify(result));
  assert.equal(goodCondition.checked, true);
  assert.ok(Array.isArray(result.interaction_diagnostic.condition_after?.choices), JSON.stringify(result));
});

test("Mercari category clicks the actionable card ancestor and verifies picker completion", async () => {
  const category = new (class extends Object {})();
  Object.assign(category, {
    tagName: "BUTTON", innerText: "Select category", textContent: "Select category", offsetParent: {}, disabled: false,
    getAttribute(name) { return name === "aria-hidden" ? "false" : null; }, click() {},
  });
  const condition = { tagName: "INPUT", attrs: { name: "sellCondition" }, checked: true, value: "GOOD", disabled: false, offsetParent: {}, parentElement: { innerText: "Good Gently used" }, getAttribute(name) { return this.attrs[name] ?? (name === "aria-hidden" ? "false" : null); }, click() {} };
  const shipping = { tagName: "INPUT", attrs: { name: "deliveryOptions", "aria-label": "mercariShipping" }, value: "MERCARI", checked: true, disabled: false, offsetParent: {}, getAttribute(name) { return this.attrs[name] ?? (name === "aria-hidden" ? "false" : null); }, click() {} };
  const options = { mercariOptions: [], mercariDialogs: [{ tagName: "DIV", offsetParent: {}, getClientRects: () => [{}], getAttribute: () => "dialog", querySelectorAll: () => options.mercariOptions }] };
  const menuOption = (text, onClick = () => {}) => ({ tagName: "BUTTON", innerText: text, textContent: text, offsetParent: {}, disabled: false, getAttribute: (name) => name === "aria-hidden" ? "false" : null, matches: () => true, closest() { return this; }, click: onClick });
  const women = menuOption("Women", () => { options.mercariOptions = [tops]; });
  const tops = menuOption("Tops", () => { options.mercariOptions = [allTopsText]; });
  const allTopsButton = menuOption("All Tops & blouses", () => {
    category.innerText = "All Tops & blouses";
    category.textContent = category.innerText;
    options.mercariDialogs = [];
  });
  const allTopsText = {
    tagName: "SPAN", innerText: "All Tops & blouses", textContent: "All Tops & blouses", offsetParent: {},
    getAttribute: () => null,
    matches: () => false,
    closest(selector) { return selector.includes('[role="button"]') ? allTopsButton : null; },
    parentElement: allTopsButton,
  };
  options.mercariOptions = [women];
  const { contentApi } = loadContentFixture("www.mercari.com", "/sell/", {
    mercariControls: { category, condition, shipping },
    get mercariOptions() { return options.mercariOptions; },
    get mercariDialogs() { return options.mercariDialogs; },
  });
  const result = await contentApi.ppSelectOption(category, "Clothing", "mercari", "category", {
    diagnostic: true, payload: { department: "Women", subcategory: "Tops" },
  });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.value, "All Tops & blouses");
  assert.deepEqual(Array.from(result.diagnostic.category_path), ["Women", "Tops", "All Tops & blouses"]);
  assert.equal(result.diagnostic.clicked_element_structure.actionable_ancestor.tag, "button");
  assert.equal(result.diagnostic.dialog_visible_after, false);
});

test("Mercari game taxonomy traverses live aliases and re-queries intermediate branches before mapped fallback", async () => {
  const category = { tagName: "BUTTON", innerText: "Select category", textContent: "Select category", offsetParent: {}, disabled: false, parentElement: { innerText: "Category" }, getAttribute(name) { return name === "aria-hidden" ? "false" : null; }, click() { options.mercariDialogs = [dialog]; options.mercariOptions = [toys]; } };
  const options = { mercariOptions: [], mercariDialogs: [] };
  const dialog = { tagName: "DIV", offsetParent: {}, getClientRects: () => [{}], getAttribute: () => "dialog", querySelectorAll: () => options.mercariOptions };
  const makeRow = (label, onClick) => ({ tagName: "BUTTON", innerText: label, textContent: label, offsetParent: {}, disabled: false, getAttribute: (name) => name === "aria-hidden" ? "false" : name === "data-testid" ? "CategoryRow" : null, matches: () => true, closest() { return this; }, click: onClick });
  const toys = makeRow("Toys & Collectibles", () => { options.mercariOptions = [games]; });
  const games = makeRow("Games & Puzzles", () => { options.mercariOptions = [allGames]; });
  const allGames = makeRow("All games & puzzles", () => { options.mercariOptions = [plasticActionGames]; });
  const plasticActionGames = makeRow("Other", () => {
    category.innerText = "Other";
    category.textContent = category.innerText;
    options.mercariOptions = [];
    options.mercariDialogs = [];
  });
  const condition = { tagName: "INPUT", attrs: { name: "sellCondition" }, checked: true, value: "NEW", disabled: false, offsetParent: {}, parentElement: { innerText: "New New with tags" }, getAttribute(name) { return this.attrs[name] ?? (name === "aria-hidden" ? "false" : null); }, click() {} };
  const shipping = { tagName: "INPUT", name: "deliveryOptions", value: "MERCARI", checked: true, disabled: false, offsetParent: {}, getAttribute(name) { return name === "name" ? this.name : name === "value" ? this.value : name === "aria-label" ? "mercariShipping" : name === "aria-hidden" ? "false" : null; }, click() {} };
  const { contentApi } = loadContentFixture("www.mercari.com", "/sell/", {
    mercariControls: { category, condition, shipping },
    get mercariOptions() { return options.mercariOptions; },
    get mercariDialogs() { return options.mercariDialogs; },
  });
  const result = await contentApi.ppSelectOption(category, "Root > Toys & Hobbies > Games > Plastic Action Games", "mercari", "category", { payload: {} });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.value, "Other");
  assert.deepEqual(Array.from(result.diagnostic.category_path), ["Toys & Collectibles", "Games & Puzzles", "All games & puzzles", "Other"]);
  assert.deepEqual(Array.from(result.diagnostic.requested_category_path), ["Root", "Toys & Hobbies", "Games", "Plastic Action Games"]);
  assert.equal(result.diagnostic.category_resolution_stage, "SELECTION_VERIFIED_MAPPED_FALLBACK_LEAF");
  assert.equal(result.diagnostic.mapped_leaf_fallback, "Other");
});

test("Mercari New condition falls back to the matching radio click when its visible label does not update React state", async () => {
  const newRadio = { tagName: "INPUT", type: "radio", attrs: { name: "sellCondition" }, name: "sellCondition", value: "on", checked: false, disabled: false, offsetParent: {}, parentElement: { innerText: "New New with tags (NWT). Unopened packaging. Unused.", textContent: "New New with tags (NWT). Unopened packaging. Unused." }, getAttribute(name) { return name === "name" ? this.name : this.attrs[name] ?? (name === "aria-hidden" ? "false" : null); }, closest(selector) { return selector === "label" ? null : this; }, click() { this.checked = true; } };
  const newLabel = { tagName: "LABEL", innerText: "New New with tags (NWT). Unopened packaging. Unused.", textContent: "New New with tags (NWT). Unopened packaging. Unused.", offsetParent: {}, getAttribute: () => null, closest() { return this; }, click() {} };
  const { contentApi, context } = loadContentFixture("www.mercari.com", "/sell/", {
    mercariControls: { condition: newRadio }, conditionInputs: [newRadio], mercariOptions: [newLabel],
  });
  assert.equal(context.document.querySelectorAll('input[name="sellCondition"]').length, 1);
  assert.match(contentApi.ppMercariInputLabel(newRadio), /New/);
  const result = await contentApi.ppSelectOption(newRadio, "New", "mercari", "condition", { payload: {} });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(newRadio.checked, true);
  assert.equal(result.diagnostic.condition_input_click_fallback, true);
});

test("Mercari recognizes an already-checked MERCARI delivery radio from its local prepaid-label card", async () => {
  const prepaidCard = { tagName: "DIV", innerText: "Prepaid label We'll email you a label and you'll ship the item.", textContent: "Prepaid label We'll email you a label and you'll ship the item." };
  const shippingWrapper = { tagName: "DIV", innerText: "", textContent: "", previousElementSibling: prepaidCard };
  const shipping = new (class extends Object {})();
  Object.assign(shipping, {
    tagName: "INPUT", attrs: { name: "deliveryOptions", "aria-label": "mercariShipping", value: "MERCARI" },
    name: "deliveryOptions", value: "MERCARI", checked: true, disabled: false, offsetParent: {}, parentElement: shippingWrapper,
    getAttribute(name) { return this.attrs[name] ?? (name === "aria-hidden" ? "false" : null); }, click() { throw new Error("already selected shipping must not be clicked"); },
  });
  const { contentApi } = loadContentFixture("www.mercari.com", "/sell/", { mercariControls: { shipping } });
  const label = contentApi.ppMercariInputLabel(shipping);
  assert.match(label, /Prepaid label/i);
  const result = await contentApi.ppSelectOption(shipping, "shipping", "mercari", "shipping", { diagnostic: true });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.value, "MERCARI");
  assert.match(result.diagnostic.selected_input.card_text, /Prepaid label/i);
  assert.equal(result.diagnostic.stage, "SELECTION_VERIFIED_ALREADY_SELECTED");
  assert.equal(result.diagnostic.delivery_before[0].checked, true);
  assert.equal(result.diagnostic.delivery_before[0].value, "MERCARI");
});

test("Mercari trusts the selected deliveryOptions control without decorative label text and never clicks it", async () => {
  let clicked = false;
  const shipping = {
    tagName: "INPUT", name: "deliveryOptions", value: "MERCARI", checked: true,
    disabled: false, offsetParent: {}, parentElement: null,
    getAttribute(name) {
      if (name === "name") return "deliveryOptions";
      if (name === "aria-label") return "mercariShipping";
      if (name === "value") return "MERCARI";
      return name === "aria-hidden" ? "false" : null;
    },
    click() { clicked = true; },
  };
  const { contentApi } = loadContentFixture("www.mercari.com", "/sell/", { mercariControls: { shipping } });
  const result = await contentApi.ppSelectOption(shipping, "shipping", "mercari", "shipping", { diagnostic: true });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.value, "MERCARI");
  assert.equal(result.diagnostic.stage, "SELECTION_VERIFIED_ALREADY_SELECTED");
  assert.deepEqual(JSON.parse(JSON.stringify(result.diagnostic.selected_input)), {
    value: "MERCARI", checked: true, aria_label: "mercariShipping", card_text: null,
  });
  assert.equal(clicked, false);
});

test("assisted destination fixtures fill core fields and never submit", async (t) => {
  const destinations = [
    ["mercari", "www.mercari.com"],
    ["poshmark", "poshmark.com"],
    ["vinted", "www.vinted.com"],
    ["etsy", "www.etsy.com"],
    ["offerup", "www.offerup.com"],
  ];
  for (const [marketplace, hostname] of destinations) {
    await t.test(marketplace, async () => {
      const { fields, listener } = loadContentFixture(hostname);
      const response = await new Promise((resolve) => {
        listener({
          action: "posterpro_fill_listing",
          marketplace,
          payload: { title: "Fixture item", price: 24.5, description: "Supported source-backed detail." },
          images: [{ data_url: "data:image/png;base64,aGk=", file_name: "posterpro-1.png" }],
        }, {}, resolve);
      });
      assert.equal(response.ok, true, JSON.stringify(response));
      assert.equal(response.stage, "AWAITING_OPERATOR_REVIEW");
      assert.equal(response.submission_performed, false);
      assert.equal(fields.title.value, "Fixture item");
      assert.equal(fields.price.value, "24.5");
      assert.equal(fields.description.value, "Supported source-backed detail.");
      assert.equal(response.uploaded_image_count, 1);
      assert.equal(response.capability_ready, false);
      assert.ok(response.field_results.every((field) => "detected" in field && "filled" in field && "error_code" in field));
    });
  }
});

test("real-form diagnostic reports per-field capability and never submits", async () => {
  const { fields, listener } = loadContentFixture("www.facebook.com", "/marketplace/create/item", { facebookPhotoPreviewOnUpload: true });
  const response = await new Promise((resolve) => {
    listener({
      action: "posterpro_diagnose_listing",
      marketplace: "facebook",
      payload: { title: "PosterPro Safe Form Test - Do Not Publish", price: 1, description: "Temporary diagnostic only", category: "Other", condition: "Used - good" },
      images: [{ data_url: "data:image/png;base64,aGk=", file_name: "posterpro-safe-diagnostic.png" }],
    }, {}, resolve);
  });
  assert.equal(response.action, "DIAGNOSTIC");
  assert.equal(response.submission_performed, false);
  assert.equal(fields.title.value, "PosterPro Safe Form Test - Do Not Publish");
  assert.ok(response.field_results.some((field) => field.field === "category"));
  assert.ok(response.field_results.some((field) => field.field === "condition"));
  assert.equal(response.field_results.find((field) => field.field === "photos").filled, true);
  const failedCategory = response.field_results.find((field) => field.field === "category");
  assert.equal(failedCategory.filled, false);
  assert.equal(failedCategory.selector_diagnostic.field, "category");
  assert.equal(failedCategory.selector_diagnostic.page_path, "/marketplace/create/item");
  assert.ok(response.field_results.filter((field) => field.filled).every((field) => !field.selector_diagnostic));
  assert.equal(response.submission_performed, false);
});

test("UPDATE requires the exact stored URL and preserves it for operator-reviewed edits", async () => {
  const { fields, listener } = loadContentFixture();
  const result = await new Promise((resolve) => listener({
    action: "posterpro_update_exact_listing",
    marketplace: "facebook",
    expected_external_url: "https://www.facebook.com/marketplace/create/item",
    external_listing_id: "FB-55",
    payload: { title: "Revised item", price: 31, description: "Canonical revised details", condition: "New", category: "Other" },
    images: [],
  }, {}, resolve));
  assert.equal(result.action, "UPDATE");
  assert.equal(result.identity_verified, true);
  assert.equal(result.external_listing_id, "FB-55");
  assert.equal(result.external_url, "https://www.facebook.com/marketplace/create/item");
  assert.equal(result.submission_performed, false);
  assert.equal(fields.title.value, "Revised item");
  const wrong = await new Promise((resolve) => listener({
    action: "posterpro_update_exact_listing", marketplace: "facebook",
    expected_external_url: "https://www.facebook.com/marketplace/item/OTHER",
    payload: {}, images: [],
  }, {}, resolve));
  assert.equal(wrong.identity_verified, false);
  assert.equal(wrong.error_code, "EXTERNAL_IDENTITY_MISMATCH");
});

test("END inspection verifies exact URL and never clicks a destructive control", () => {
  const { listener } = loadContentFixture();
  let result;
  listener({
    action: "posterpro_inspect_exact_end", marketplace: "facebook",
    expected_external_url: "https://www.facebook.com/marketplace/create/item", external_listing_id: "FB-55", payload: {},
  }, {}, (value) => { result = value; });
  assert.equal(result.identity_verified, true);
  assert.equal(result.end_control_detected, false);
  assert.equal(result.submission_performed, false);
  assert.equal(result.required_operator_action, "FIND_MARKETPLACE_END_CONTROL_MANUALLY_THEN_CONFIRM_IN_POSTERPRO");
});

test("adapter refuses to fill a marketplace form on the wrong domain", async () => {
  const { listener } = loadContentFixture("www.mercari.com");
  const response = await new Promise((resolve) => {
    listener({ action: "posterpro_fill_listing", marketplace: "facebook", payload: {}, images: [] }, {}, resolve);
  });
  assert.equal(response.ok, false);
  assert.equal(response.error_code, "UNEXPECTED_MARKETPLACE_DOMAIN");
});

test("Facebook adapter transfers source-backed optional attributes when the form exposes them", async () => {
  const { fields, listener } = loadContentFixture();
  const response = await new Promise((resolve) => {
    listener({
      action: "posterpro_fill_listing",
      marketplace: "facebook",
      payload: { brand: "Northwind", size: "M", color: "Navy", location: "02139" },
      images: [],
    }, {}, resolve);
  });
  assert.equal(fields.brand.value, "Northwind");
  assert.equal(fields.size.value, "M");
  assert.equal(fields.color.value, "Navy");
  assert.equal(fields.location.value, "");
  assert.deepEqual(Array.from(response.populated_fields), ["brand", "size", "color"]);
  assert.equal(response.submission_performed, false);
});

test("Facebook missing Material control cannot fall back to or overwrite the Description textarea", async () => {
  const { fields, listener } = loadContentFixture("www.facebook.com", "/marketplace/create/item", { facebookMaterialControlAbsent: true });
  const canonical = "Canonical PosterPro description remains owned by Description.";
  const response = await new Promise((resolve) => listener({
    action: "posterpro_fill_listing", marketplace: "facebook",
    payload: { title: "Owned fields", price: 19.99, description: canonical, material: "Plastic" }, images: [],
  }, {}, resolve));
  assert.equal(fields.description.value, canonical);
  const material = response.field_results.find((field) => field.field === "material");
  assert.equal(material.detected, false);
  assert.equal(material.filled, false);
  assert.equal(response.description_mutation_trace.every((checkpoint) => checkpoint.matches), true);
  assert.equal(response.submission_performed, false);
});
