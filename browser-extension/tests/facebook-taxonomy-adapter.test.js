const test = require("node:test");
const assert = require("node:assert/strict");
require("../facebook-taxonomy-adapter.js");
const adapterApi = globalThis.PosterProFacebookTaxonomyAdapter;

function row(name, id = null, role = "option", index = 0) {
  return { isConnected: true, tagName: "DIV", id: "", innerText: name, textContent: name,
    getClientRects: () => [{}], getAttribute(key) { return ({ role, "data-category-id": id,
      "aria-posinset": index ? String(index) : null })[key] ?? null; },
    querySelector: () => null, querySelectorAll: () => [] };
}
function listFixture({ rows, count = null, scroll = false, loading = null, next = null, unrelated = [] }) {
  let scrollTop = 0; let polls = 0;
  const list = { isConnected: true, tagName: "DIV", clientHeight: scroll ? 100 : 0,
    scrollHeight: scroll ? 260 : 0,
    get scrollTop() { return scrollTop; }, set scrollTop(v) { scrollTop = v; },
    getAttribute(key) { return key === "role" ? "listbox" : key === "aria-setsize" && count != null ? String(count) : null; },
    matches: (selector) => selector.includes("listbox"),
    querySelector: () => null,
    querySelectorAll(selector) {
      if (selector.includes("role='option'") || selector.includes('role="option"')) {
        if (!scroll) return rows;
        const start = Math.floor(scrollTop / 20);
        return rows.slice(start, Math.min(rows.length, start + 5));
      }
      if (selector === "*") return [];
      return [];
    } };
  const container = { isConnected: true, tagName: "DIV", getAttribute: () => "dialog", matches: () => false,
    querySelector: (selector) => selector.includes("listbox") ? list : null,
    querySelectorAll(selector) {
      if (selector.includes("button") && next) return [next];
      if (selector.includes("aria-busy") || selector.includes("role='status'") || selector.includes("role='progressbar'")) {
        polls += 1; return loading && polls < 3 ? [loading] : [];
      }
      if (selector === "*") return [];
      return [];
    } };
  const doc = { body: {}, querySelectorAll: () => unrelated };
  return { doc, container, list };
}
function installVisibility() { globalThis.getComputedStyle = () => ({ visibility: "visible", display: "block" }); }

test("Facebook enumeration retains more than 100 options and duplicate labels with distinct IDs", async () => {
  installVisibility();
  const rows = Array.from({ length: 105 }, (_, i) => row(i === 4 || i === 90 ? "Same label" : `Category ${i}`, `id-${i}`, "option", i + 1));
  const { doc, container } = listFixture({ rows, count: 105, unrelated: [row("Publish", "publish", "button")] });
  const result = await adapterApi.enumerateVisible(doc, { container });
  assert.equal(result.complete, true, JSON.stringify(result, null, 2));
  assert.equal(result.items.length, 105);
  assert.equal(result.items.filter((r) => r.label === "Same label").length, 2);
});

test("Facebook opens the diagnostic #226 portal of DIV role=button category rows", async () => {
  installVisibility();
  const priorDocument = globalThis.document;
  const priorLocation = globalThis.location;
  const priorResolver = globalThis.ppFindField;
  const priorOptionFinder = globalThis.ppFacebookVisibleCategoryOptionCandidates;
  const priorAdapters = globalThis.POSTERPRO_MARKETPLACE_ADAPTERS;
  let opened = false;
  const trigger = { isConnected: true, tagName: "LABEL", innerText: "Category", textContent: "Category",
    disabled: false, getClientRects: () => [{}], getAttribute: (key) => key === "role" ? "combobox" : null,
    click() { opened = true; }, querySelector() { return null; } };
  const optionLabels = ["Household Shipping available", "Tools", "Furniture", "Garden", "Appliances",
    "Video Games", "Books Movies & Music", "Bags & Luggage"];
  let rows = [];
  const portal = { isConnected: true, tagName: "DIV", parentElement: null, clientHeight: 0, scrollHeight: 0,
    getClientRects: () => [{}], getAttribute: () => null, matches: () => false,
    contains(node) { return rows.includes(node); }, querySelector: () => null,
    querySelectorAll(selector) {
      if (selector.includes("next page") || selector.includes("data-pagination")) return [];
      if (selector === "*") return [];
      return selector.includes("role='button'") || selector.includes('role="button"') ? rows : [];
    } };
  const doc = { body: { isConnected: true }, documentElement: { isConnected: true }, readyState: "complete",
    querySelectorAll(selector) {
      if (selector === "[role='combobox'],button,[role='button'],label") return [trigger];
      if (selector.includes("next page") || selector.includes("data-pagination") || selector === "*") return [];
      if (selector.includes("role='option'") || selector.includes('role="option"') || selector === "[role='button'],button") return rows;
      if (selector.includes("[role='listbox'][aria-labelledby]")) return [];
      if (selector.includes("[role='dialog'],[aria-modal='true']")) return [];
      return [];
    }, getElementById() { return null; } };
  globalThis.document = doc;
  globalThis.location = { href: "https://www.facebook.com/marketplace/create/item" };
  globalThis.POSTERPRO_MARKETPLACE_ADAPTERS = { facebook: { category: ['label[role="combobox"]'] } };
  globalThis.ppFindField = (selectors, labels) => {
    assert.deepEqual(Array.from(selectors), ['label[role="combobox"]']);
    assert.deepEqual(Array.from(labels), ["category"]);
    return trigger;
  };
  globalThis.ppFacebookVisibleCategoryOptionCandidates = () => rows;
  trigger.click = () => {
    opened = true;
    setTimeout(() => {
      rows = optionLabels.map((text) => ({ isConnected: true, tagName: "DIV", role: "button", innerText: text, textContent: text,
        parentElement: portal, getClientRects: () => [{}], getAttribute: (key) => key === "role" ? "button" : null,
        querySelector: () => null, querySelectorAll: () => [], click() {} }));
    }, 120);
  };
  doc.querySelectorAll = function querySelectorAll(selector) {
    if (selector === "[role='combobox'],button,[role='button'],label") return [trigger];
    if (selector.includes("next page") || selector.includes("data-pagination") || selector === "*") return [];
    if (selector.includes("role='option'") || selector.includes('role="option"') || selector === "[role='button'],button") return rows;
    if (selector.includes("[role='listbox'][aria-labelledby]")) return [];
    if (selector.includes("[role='dialog'],[aria-modal='true']")) return [];
    return [];
  };
  try {
    const control = await adapterApi.openPicker();
    assert.equal(opened, true);
    assert.equal(control, trigger);
    const result = await adapterApi.enumerateVisible(doc);
    assert.equal(result.complete, true, JSON.stringify(result, null, 2));
    assert.equal(result.items.length, 8);
    assert.deepEqual(result.items.map((item) => item.label), ["Household", "Tools", "Furniture", "Garden", "Appliances",
      "Video Games", "Books Movies & Music", "Bags & Luggage"]);
    assert.equal(result.evidence.container_role, null);
  } finally {
    globalThis.document = priorDocument;
    globalThis.location = priorLocation;
    globalThis.ppFindField = priorResolver;
    globalThis.ppFacebookVisibleCategoryOptionCandidates = priorOptionFinder;
    globalThis.POSTERPRO_MARKETPLACE_ADAPTERS = priorAdapters;
  }
});

test("expanded Category combobox accepts pre-existing portal rows with 24 visible candidates and zero new rows", async () => {
  installVisibility();
  const priorDocument = globalThis.document;
  const priorLocation = globalThis.location;
  const priorResolver = globalThis.ppFindField;
  const priorOptionFinder = globalThis.ppFacebookVisibleCategoryOptionCandidates;
  const priorAdapters = globalThis.POSTERPRO_MARKETPLACE_ADAPTERS;
  let expanded = false;
  const trigger = { isConnected: true, tagName: "LABEL", innerText: "Category", textContent: "Category",
    disabled: false, getClientRects: () => [{}],
    getAttribute(key) { return key === "role" ? "combobox" : key === "aria-expanded" ? String(expanded) : null; },
    getBoundingClientRect: () => ({ left: 300, right: 500, top: 440, bottom: 480 }),
    click() { expanded = true; }, querySelector() { return null; } };
  const labels = ["Tools", "Furniture", "Household Shipping available", "Garden", "Appliances",
    "Video Games", "Books, Movies & Music Shipping available", "Bags & Luggage Shipping available"];
  let categoryRows = [];
  const portal = { isConnected: true, tagName: "DIV", parentElement: null, clientHeight: 0, scrollHeight: 0,
    getClientRects: () => [{}], getBoundingClientRect: () => ({ left: 300, right: 700, top: 480, bottom: 780 }),
    getAttribute: () => null, matches: () => false,
    contains(node) { return categoryRows.includes(node); }, querySelector: () => null,
    querySelectorAll(selector) {
      if (selector === "*" || selector.includes("aria-busy") || selector.includes("role='status'")
        || selector.includes("role='progressbar'") || selector.includes("next page") || selector.includes("data-pagination")) return [];
      return categoryRows;
    } };
  const body = { isConnected: true, tagName: "BODY", parentElement: null };
  portal.parentElement = body;
  categoryRows = labels.map((text, index) => ({ isConnected: true, tagName: "DIV", parentElement: portal,
    innerText: text, textContent: text, disabled: false, getClientRects: () => [{}],
    getBoundingClientRect: () => ({ left: 300, right: 700, top: 485 + index * 30, bottom: 515 + index * 30 }),
    getAttribute(key) { return key === "role" ? "button" : null; }, closest() { return this; },
    querySelector: () => null, querySelectorAll: () => [], click() {} }));
  const unrelated = Array.from({ length: 16 }, (_, index) => ({ isConnected: true, tagName: "BUTTON",
    innerText: `Page action ${index}`, textContent: `Page action ${index}`, disabled: false,
    getClientRects: () => [{}], getAttribute: (key) => key === "role" ? null : null,
    closest() { return this; }, querySelector: () => null, querySelectorAll: () => [],
    getBoundingClientRect: () => ({ left: 0, right: 80, top: index * 4, bottom: index * 4 + 20 }) }));
  const doc = { body, documentElement: { isConnected: true }, readyState: "complete",
    querySelectorAll(selector) {
      if (selector === "[role='combobox'],button,[role='button'],label") return [trigger];
      if (selector.includes("[role='listbox'][aria-labelledby]")) return [];
      if (selector.includes("[role='dialog'],[aria-modal='true']")) return [];
      return [];
    }, getElementById() { return null; } };
  globalThis.document = doc;
  globalThis.location = { href: "https://www.facebook.com/marketplace/create/item" };
  globalThis.POSTERPRO_MARKETPLACE_ADAPTERS = { facebook: { category: ['label[role="combobox"]'] } };
  globalThis.ppFindField = () => trigger;
  // All 24 candidates exist and are visible before the interaction. The eight
  // category rows are a coherent nearby portal cluster; the other sixteen are
  // unrelated native page buttons. The click changes aria-expanded only.
  globalThis.ppFacebookVisibleCategoryOptionCandidates = () => [...categoryRows, ...unrelated];
  try {
    let control;
    try { control = await adapterApi.openPicker(); }
    catch (error) { assert.fail(JSON.stringify(error.evidence || { code: error.code, message: error.message })); }
    assert.equal(control, trigger);
    assert.equal(expanded, true);
    const result = await adapterApi.enumerateVisible(doc);
    assert.equal(result.complete, true, JSON.stringify(result, null, 2));
    assert.equal(result.items.length, 8);
    assert.deepEqual(result.items.map((item) => item.label), ["Tools", "Furniture", "Household", "Garden",
      "Appliances", "Video Games", "Books, Movies & Music", "Bags & Luggage"]);
  } finally {
    globalThis.document = priorDocument;
    globalThis.location = priorLocation;
    globalThis.ppFindField = priorResolver;
    globalThis.ppFacebookVisibleCategoryOptionCandidates = priorOptionFinder;
    globalThis.POSTERPRO_MARKETPLACE_ADAPTERS = priorAdapters;
  }
});

test("visible Facebook rows survive unrelated persistent shell loading and group headings are excluded", async () => {
  installVisibility();
  const names = ["Tools", "Furniture", "Household", "Garden", "Appliances", "Video Games", "Books, Movies & Music", "Bags & Luggage"];
  const rows = names.map((name, index) => ({ ...row(name, `fb-${index}`, "button"), parentElement: null }));
  const heading = row("Home & Garden", null, "heading");
  const scroll = { scrollTop: 0, scrollHeight: 500, clientHeight: 240, parentElement: null,
    getAttribute: () => null, querySelectorAll(selector) { return selector.includes("role='button'") ? rows : []; },
    contains(node) { return rows.includes(node); } };
  rows.forEach((node) => { node.parentElement = scroll; });
  const list = { isConnected: true, tagName: "DIV", parentElement: null, clientHeight: 0, scrollHeight: 0,
    getAttribute: (name) => name === "role" ? "listbox" : null, matches: (selector) => selector.includes("listbox"),
    contains(node) { return node === scroll || rows.includes(node) || node === heading; },
    querySelector: () => null,
    querySelectorAll(selector) {
      if (selector.includes("next page") || selector.includes("data-pagination")) return [];
      if (selector.includes("role='button'")) return rows;
      if (selector === "*") return [scroll, ...rows, heading];
      if (selector.includes("role='status'")) return [shellStatus];
      return [];
    } };
  scroll.parentElement = list;
  const shellStatus = row("Loading your Marketplace feed", null, "status");
  const container = list;
  const result = await adapterApi.enumerateVisible({ body: {} }, { container });
  assert.equal(result.complete, true, JSON.stringify(result, null, 2));
  assert.deepEqual(result.items.map((item) => item.label), names);
  assert.equal(result.evidence.loading, false);
  assert.equal(result.evidence.scroll_container_found, true);
});

test("unrelated page buttons are outside the category list and cannot become category options", async () => {
  installVisibility();
  const { doc, container } = listFixture({ rows: [row("Household", "h-1", "button")], unrelated: [row("Publish", "p", "button"), row("Next", "n", "button")] });
  const result = await adapterApi.enumerateVisible(doc, { container });
  assert.deepEqual(result.items.map((r) => r.label), ["Household"]);
});

test("enumeration waits for asynchronous loading to settle before declaring completeness", async () => {
  installVisibility();
  const busy = row("Loading", null, "status");
  const { doc, container } = listFixture({ rows: [row("Furniture", "f-1")], loading: busy });
  const result = await adapterApi.enumerateVisible(doc, { container });
  assert.equal(result.complete, true);
  assert.equal(result.evidence.loading, false);
});

test("pagination remains unresolved when next-page evidence is present", async () => {
  installVisibility();
  const next = { isConnected: true, disabled: false, getClientRects: () => [{}],
    getAttribute: (name) => name === "aria-label" ? "Next page" : null, textContent: "", click() {} };
  const { doc, container } = listFixture({ rows: [row("Home", "home")], next });
  const result = await adapterApi.enumerateVisible(doc, { container });
  assert.equal(result.complete, false);
  assert.equal(result.reason, "PAGINATION_DID_NOT_ADVANCE");
});

test("explicit category pagination is traversed page by page and both pages are retained", async () => {
  installVisibility();
  let page = 0;
  const pages = [[row("Clothing", "clothing")], [row("Home", "home")]];
  const list = { isConnected: true, tagName: "DIV", clientHeight: 0, scrollHeight: 0,
    getAttribute: (name) => name === "role" ? "listbox" : null,
    matches: (selector) => selector.includes("listbox"),
    querySelector: () => null,
    querySelectorAll(selector) { return selector.includes("role='option'") || selector.includes('role="option"') ? pages[page] : []; } };
  const next = { isConnected: true, disabled: false, getClientRects: () => [{}], textContent: "",
    getAttribute: (name) => name === "aria-label" ? "Next page" : null, click() { page = 1; this.disabled = true; } };
  const container = { isConnected: true, getAttribute: () => "dialog", matches: () => false,
    querySelector: (selector) => selector.includes("listbox") ? list : null,
    querySelectorAll(selector) {
      if (selector.includes("next page") || selector.includes("data-pagination")) return this.page === 0 && !next.disabled ? [next] : [];
      if (selector === "*" || selector.includes("aria-busy") || selector.includes("role='status'") || selector.includes("role='progressbar'")) return [];
      return [];
    }, get page() { return page; } };
  const result = await adapterApi.enumerateVisible({ body: {} }, { container });
  assert.equal(result.complete, true);
  assert.equal(page, 1);
  assert.deepEqual(result.items.map((r) => r.label), ["Clothing", "Home"]);
});

test("virtualized options are collected while scrolling and declared counts are checked", async () => {
  installVisibility();
  const rows = Array.from({ length: 13 }, (_, i) => row(`Node ${i}`, `n-${i}`, "option", i + 1));
  const { doc, container } = listFixture({ rows, count: 13, scroll: true });
  const result = await adapterApi.enumerateVisible(doc, { container });
  assert.equal(result.evidence.virtualized, true);
  assert.equal(result.evidence.end_reached, true);
  assert.equal(result.items.length, 13);
  assert.equal(result.complete, true);
});

test("a declared option count larger than the inspected set remains incomplete", async () => {
  installVisibility();
  const { doc, container } = listFixture({ rows: [row("One", "one")], count: 4 });
  const result = await adapterApi.enumerateVisible(doc, { container });
  assert.equal(result.complete, false);
  assert.equal(result.reason, "OBSERVED_COUNT_BELOW_DECLARED_COUNT");
});

test("blank create-form check ignores Facebook navigation, saved locality and unrelated page imagery", () => {
  const priorDocument = globalThis.document;
  const priorLocation = globalThis.location;
  const priorStyle = globalThis.getComputedStyle;
  const control = (attrs, value = "") => ({ value, innerText: "", textContent: "", id: "", labels: [],
    getAttribute: (key) => attrs[key] || null, getClientRects: () => [{}] });
  const search = control({ "aria-label": "Search Facebook" }, "Marketplace");
  const locality = control({ "aria-label": "Location" }, "Huntington Station");
  const condition = control({ "aria-label": "Condition" }, "New");
  const emptyTitle = control({ "aria-label": "Title" });
  const emptyPrice = control({ placeholder: "Price" });
  const emptyDescription = control({ "aria-label": "Description" });
  const category = control({ "aria-label": "Category" });
  globalThis.location = { href: "https://www.facebook.com/marketplace/create/item" };
  globalThis.getComputedStyle = () => ({ visibility: "visible", display: "block" });
  globalThis.document = { querySelectorAll(selector) {
    if (selector === "[role='combobox'],button,[role='button'],label") return [category];
    if (selector.includes("input:not") || selector.includes("textarea") || selector.includes("contenteditable")) return [search, locality, condition, emptyTitle, emptyPrice, emptyDescription];
    return [];
  } };
  assert.equal(adapterApi.blankCreateForm(), true);
  assert.equal(adapterApi.blankCreateFormEvidence().form_ready, true);
  const titleWithDraft = control({ "aria-label": "Title" }, "Existing diagnostic title");
  globalThis.document.querySelectorAll = (selector) => selector === "[role='combobox'],button,[role='button'],label" ? [category]
    : selector.includes("input:not") || selector.includes("textarea") || selector.includes("contenteditable")
      ? [search, locality, condition, titleWithDraft, emptyPrice, emptyDescription] : [];
  assert.equal(adapterApi.blankCreateForm(), false);
  assert.deepEqual(adapterApi.blankCreateFormEvidence().populated_fields.map((field) => field.field), ["Title"]);
  globalThis.document = priorDocument;
  globalThis.location = priorLocation;
  globalThis.getComputedStyle = priorStyle;
});

test("taxonomy field evidence excludes Facebook shell controls outside seller listing fields", () => {
  const priorDocument = globalThis.document;
  const priorLocation = globalThis.location;
  const priorStyle = globalThis.getComputedStyle;
  const make = (name, tag = "INPUT") => ({ isConnected: true, tagName: tag, required: false, options: [], labels: [],
    getAttribute(key) { return key === "aria-label" ? name : null; }, getClientRects: () => [{}] });
  const title = make("Title"); const search = make("Search Facebook"); const location = make("Location"); const size = make("Size");
  const form = { querySelectorAll: () => [title, search, location, size] };
  const category = { isConnected: true, innerText: "Category", getAttribute: (key) => key === "aria-label" ? "Category" : null,
    getClientRects: () => [{}], closest: () => form };
  globalThis.location = { href: "https://www.facebook.com/marketplace/create/item", pathname: "/marketplace/create/item" };
  globalThis.getComputedStyle = () => ({ visibility: "visible", display: "block" });
  globalThis.document = { querySelectorAll: (selector) => selector === "[role='combobox'],button,[role='button'],label" ? [category] : [],
    querySelector: () => null };
  const result = adapterApi.schemaSnapshot();
  assert.deepEqual(result.fields.map((field) => field.name), ["Title", "Size"]);
  globalThis.document = priorDocument; globalThis.location = priorLocation; globalThis.getComputedStyle = priorStyle;
});
