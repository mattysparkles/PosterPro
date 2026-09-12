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
    photos: new FakeFileInput(),
  };
  const document = {
    querySelector(selector) {
      if (/Title/i.test(selector)) return fields.title;
      if (/Price/i.test(selector) || selector.includes("inputmode")) return fields.price;
      if (/Description/i.test(selector)) return fields.description;
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
  assert.deepEqual(Array.from(response.missing_required_fields), ["category_operator_selection_required", "condition_operator_selection_required"]);
  assert.equal(response.selector_strategy, "central_marketplace_map_then_semantic_labels");
});

test("adapter refuses to fill a marketplace form on the wrong domain", async () => {
  const { listener } = loadContentFixture("www.mercari.com");
  const response = await new Promise((resolve) => {
    listener({ action: "posterpro_fill_listing", marketplace: "facebook", payload: {}, images: [] }, {}, resolve);
  });
  assert.equal(response.ok, false);
  assert.equal(response.error_code, "UNEXPECTED_MARKETPLACE_DOMAIN");
});
