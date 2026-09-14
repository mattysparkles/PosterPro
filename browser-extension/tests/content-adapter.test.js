const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");

function loadContentFixture(hostname = "www.facebook.com") {
  class FakeInput {
    constructor() { this.disabled = false; this.isContentEditable = false; this.value = ""; this.files = []; }
    focus() {}
    blur() {}
    dispatchEvent() { return true; }
    getAttribute(name) { return name === "aria-hidden" ? "false" : null; }
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
    description: new FakeTextArea(),
    brand: new FakeInput(),
    size: new FakeInput(),
    color: new FakeInput(),
    material: new FakeInput(),
    location: new FakeInput(),
    photos: new FakeFileInput(),
  };
  const document = {
    querySelector(selector) {
      if (/Title/i.test(selector)) return fields.title;
      if (/Price/i.test(selector) || selector.includes("inputmode")) return fields.price;
      if (/Description/i.test(selector)) return fields.description;
      if (/Brand/i.test(selector)) return fields.brand;
      if (/Size/i.test(selector)) return fields.size;
      if (/Color/i.test(selector)) return fields.color;
      if (/Material/i.test(selector)) return fields.material;
      if (/Location|ZIP/i.test(selector)) return fields.location;
      if (selector === 'input[type="file"]') return fields.photos;
      return null;
    },
    querySelectorAll() { return []; },
  };
  let listener;
  const context = {
    document,
    location: { hostname, origin: `https://${hostname}`, pathname: "/marketplace/create/item" },
    chrome: { runtime: { onMessage: { addListener(callback) { listener = callback; } } } },
    HTMLInputElement: FakeInput,
    HTMLTextAreaElement: FakeTextArea,
    Event: class { constructor(type, options) { this.type = type; this.options = options; } },
    DataTransfer: FakeDataTransfer,
    File: FakeFile,
    atob: (value) => Buffer.from(value, "base64").toString("binary"),
    URL,
  };
  context.globalThis = context;
  vm.runInNewContext(fs.readFileSync(path.join(root, "marketplace-adapters.js"), "utf8"), context);
  vm.runInNewContext(fs.readFileSync(path.join(root, "content.js"), "utf8"), context);
  return { context, fields, listener: (...args) => listener(...args) };
}

test("Facebook create form fixture fills supported fields and stops for operator review", async () => {
  const { fields, listener } = loadContentFixture();
  const response = await new Promise((resolve) => {
    listener({
      action: "posterpro_fill_listing",
      marketplace: "facebook",
      payload: { title: "Test listing", price: 20, description: "A useful description", category_hint: "Home", condition: "New" },
      images: [{ data_url: "data:image/png;base64,aGk=", file_name: "posterpro-1.png" }],
    }, {}, resolve);
  });
  assert.equal(response.ok, true);
  assert.equal(response.stage, "AWAITING_OPERATOR_REVIEW");
  assert.equal(response.submission_performed, false);
  assert.equal(fields.title.value, "Test listing");
  assert.equal(fields.price.value, "20");
  assert.equal(fields.description.value, "A useful description");
  assert.equal(response.uploaded_image_count, 1);
  assert.deepEqual(Array.from(response.missing_required_fields), ["category", "condition"]);
  assert.equal(response.capability_ready, false);
  assert.equal(response.field_results.find((field) => field.field === "category").error_code, "FIELD_OR_MAPPED_VALUE_MISSING");
  assert.equal(response.selector_strategy, "central_marketplace_map_then_semantic_labels");
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
  const { fields, listener } = loadContentFixture();
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
  assert.equal(fields.location.value, "02139");
  assert.deepEqual(Array.from(response.populated_fields), ["brand", "size", "color", "location"]);
  assert.equal(response.submission_performed, false);
});
