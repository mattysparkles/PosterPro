const test = require("node:test");
const assert = require("node:assert/strict");
const {
  createCheckpoint, projectCompactCheckpoint, traverse, traverseWithStorage, captureVisibleCategoryEvidence,
} = require("../category-traversal.js");

function fixtureAdapter() {
  const tree = {
    "": [{ label: "Home", category_id: "1" }, { label: "Clothing", category_id: "2" }],
    "home": [{ label: "Kitchen", category_id: "3" }],
    "home\u001fkitchen": [{ label: "Cookware", category_id: "4" }],
    "clothing": [{ label: "Tops", category_id: "5" }],
    "clothing\u001ftops": [],
    "home\u001fkitchen\u001fcookware": [],
  };
  let backtracks = 0;
  return {
    isLive: false,
    async readChildren(path) { return tree[path.map((part) => part.toLowerCase()).join("\u001f")] || []; },
    async select(option, path) {
      const children = tree[path.map((part) => part.toLowerCase()).join("\u001f")] || [];
      return {
        category_id: option.category_id,
        terminal: children.length === 0,
        terminal_evidence: "fixture selector showed no child choices",
        children,
        children_complete: true,
        selector_evidence: { role: "treeitem", selector: "[role=treeitem]" },
      };
    },
    async captureRequiredFields(option, path) {
      return { fields: path.at(-1) === "Cookware" ? [{ name: "material", required: true, options: ["Steel", "Ceramic"] }] : [], complete: true, evidence: "fixture inspected all category fields" };
    },
    async backtrack() { backtracks += 1; },
    get backtracks() { return backtracks; },
  };
}

test("26 observed Facebook roots survive compact checkpoint persistence and resume into child traversal", async () => {
  const checkpoint = createCheckpoint("facebook", "facebook-seller-form-ui-v2");
  checkpoint.session_id = "session-fixture";
  checkpoint.status = "partial_unresolved";
  checkpoint.frontier = [{ path: ["Clothing"], identities: ["id:root-clothing"] }];
  checkpoint.branch_evidence = { "": { end_reached: true, loading: false, pagination_control: false, option_count: 26 } };
  for (let index = 0; index < 26; index += 1) {
    const label = index === 0 ? "Clothing" : `Observed root ${index + 1}`;
    const identity = `id:root-${index + 1}`;
    const key = `${label.toLocaleLowerCase()}\u001e${identity}`;
    checkpoint.categories[key] = {
      marketplace: "facebook", category_id: identity.slice(3), category_name: label,
      category_identity: identity, identity_path: [identity], parent_path: [], full_path: [label], depth: 1,
      terminal_status: "unknown", required_fields_schema_status: "uncollected", source: "authorized_live_interface",
    };
  }

  const serialized = JSON.stringify(projectCompactCheckpoint(checkpoint, {
    sessionId: "session-fixture", sourceVersion: "facebook-seller-form-ui-v2",
    persistedCategoryKeys: Object.keys(checkpoint.categories),
  }));
  const database = JSON.parse(serialized); // mirrors remote save/reload across a process boundary
  const rootsDiscovered = Object.values(database.categories).filter((node) => Array.isArray(node.full_path) && node.full_path.length === 1).length;
  assert.equal(rootsDiscovered, 26);
  assert.equal(database.branch_evidence[""].option_count, 26);
  assert.equal(database.categories["clothing\u001eid:root-1"].identity_path[0], "id:root-1");

  const local = {
    value: structuredClone(database),
    async get(key) { return { [key]: JSON.parse(JSON.stringify(this.value)) }; },
    async set(value) { this.value = JSON.parse(JSON.stringify(Object.values(value)[0])); },
  };
  const adapter = {
    isLive: false,
    async readChildren(path) { return path.join(" > ") === "Clothing" ? [{ label: "T-shirts", category_id: "shirt-leaf" }] : []; },
    async select(option) { return { category_id: option.category_id, terminal: true,
      terminal_evidence: "resumed seller-form child selection", children: [], children_complete: true }; },
    async captureRequiredFields() { return { fields: [], complete: true, evidence: "resumed form schema inspection" }; },
    async backtrack() {},
  };
  const resumed = await traverseWithStorage(adapter, { marketplace: "facebook", sourceVersion: "facebook-seller-form-ui-v2" }, local, "checkpoint");
  const durable = JSON.parse(JSON.stringify(local.value));
  assert.equal(Object.values(durable.categories).filter((node) => Array.isArray(node.full_path) && node.full_path.length === 1).length, 26);
  const leaf = Object.values(resumed.checkpoint.categories).find((node) => node.category_id === "shirt-leaf");
  assert.deepEqual(leaf.full_path, ["Clothing", "T-shirts"]);
  assert.equal(leaf.terminal_status, "confirmed_leaf");
  assert.equal(durable.categories["clothing\u001eid:root-1"].source, "authorized_live_interface");
});

test("taxonomy traversal records paths, ids, leaf evidence, fields and checkpoints across resume", async () => {
  const adapter = fixtureAdapter();
  let checkpoint = createCheckpoint("facebook", "fixture-v1");
  const saved = [];
  let run = await traverse(adapter, { marketplace: "facebook", sourceVersion: "fixture-v1", checkpoint, maxNodes: 2, onCheckpoint: async (value) => saved.push(value) });
  checkpoint = run.checkpoint;
  assert.equal(checkpoint.status, "checkpointed_at_node_limit");
  assert.ok(saved.length >= 1);
  run = await traverse(adapter, { marketplace: "facebook", sourceVersion: "fixture-v1", checkpoint, maxNodes: 10, onCheckpoint: async (value) => saved.push(value) });
  checkpoint = run.checkpoint;
  assert.equal(checkpoint.status, "complete");
  const nodes = Object.values(checkpoint.categories);
  assert.ok(nodes.some((node) => node.full_path.join(" > ") === "Home > Kitchen > Cookware" && node.category_id === "4"));
  assert.ok(nodes.some((node) => node.full_path.join(" > ") === "Clothing > Tops" && node.terminal_status === "confirmed_leaf"));
  assert.deepEqual(nodes.find((node) => node.category_name === "Cookware").required_fields[0], {
    name: "material", required: true, options: ["Steel", "Ceramic"],
  });
  assert.ok(adapter.backtracks >= 5);
});

test("live traversal requires an unexpired source-specific authorization grant", async () => {
  const adapter = { ...fixtureAdapter(), isLive: true };
  await assert.rejects(traverse(adapter, { marketplace: "mercari" }), /TAXONOMY_LIVE_AUTHORIZATION_REQUIRED/);
  const grant = { authorized: true, marketplace: "mercari", source: "written_permission", expires_at: new Date(Date.now() + 60000).toISOString() };
  const result = await traverse(adapter, { marketplace: "mercari", authorization: grant, maxNodes: 1 });
  assert.equal(result.visited_this_run, 1);
});

test("extension storage checkpoint resumes after service-worker interruption", async () => {
  const adapter = fixtureAdapter();
  const state = {};
  const storage = {
    async get(key) { return state[key] ? { [key]: structuredClone(state[key]) } : {}; },
    async set(values) { Object.assign(state, structuredClone(values)); },
  };
  const key = "taxonomy_checkpoint_facebook";
  const first = await traverseWithStorage(adapter, { marketplace: "facebook", sourceVersion: "fixture-v1", maxNodes: 1 }, storage, key);
  assert.equal(first.checkpoint.status, "checkpointed_at_node_limit");
  const second = await traverseWithStorage(adapter, { marketplace: "facebook", sourceVersion: "fixture-v1", maxNodes: 20 }, storage, key);
  assert.equal(second.checkpoint.status, "complete");
  assert.equal(Object.keys(second.checkpoint.categories).length, 5);
});

test("pause stops at a durable traversal boundary and resume continues from the saved frontier", async () => {
  const adapter = fixtureAdapter();
  const state = {};
  const storage = {
    async get(key) { return state[key] ? { [key]: structuredClone(state[key]) } : {}; },
    async set(values) { Object.assign(state, structuredClone(values)); },
  };
  const key = "facebook-pause-resume";
  const first = await traverseWithStorage(adapter, { marketplace: "facebook", sourceVersion: "fixture-v1", maxNodes: 20 }, storage, key);
  assert.equal(first.checkpoint.status, "complete");
  const checkpoint = createCheckpoint("facebook", "fixture-v1");
  let reads = 0;
  let pausedCheckpointState;
  const pausingAdapter = { ...adapter, async readChildren(...args) { reads += 1; return adapter.readChildren(...args); } };
  global.__POSTERPRO_TAXONOMY_PAUSE_REQUESTED = true;
  try {
    const paused = await traverse(pausingAdapter, { marketplace: "facebook", sourceVersion: "fixture-v1", checkpoint });
    pausedCheckpointState = paused.checkpoint;
    assert.equal(paused.checkpoint.status, "stopped");
    assert.equal(paused.checkpoint.frontier.length, 1);
    assert.equal(reads, 0);
  } finally {
    global.__POSTERPRO_TAXONOMY_PAUSE_REQUESTED = false;
  }
  const resumed = await traverse(adapter, { marketplace: "facebook", sourceVersion: "fixture-v1", checkpoint: pausedCheckpointState, maxNodes: 20 });
  assert.equal(resumed.checkpoint.status, "complete");
});

test("remote tenant checkpoint is loaded and revisioned saves follow local durable saves", async () => {
  const adapter = fixtureAdapter();
  const storageState = {};
  let revision = 4;
  let remoteCheckpoint = createCheckpoint("facebook", "fixture-v1");
  const storage = {
    async get(key) { return storageState[key] ? { [key]: structuredClone(storageState[key]) } : {}; },
    async set(values) { Object.assign(storageState, structuredClone(values)); },
  };
  const result = await traverseWithStorage(adapter, {
    marketplace: "facebook", sourceVersion: "fixture-v1", maxNodes: 20,
    remoteCheckpoint: {
      async load() { return { revision, checkpoint: structuredClone(remoteCheckpoint) }; },
      async save(payload) {
        assert.equal(payload.expected_revision, revision);
        remoteCheckpoint = structuredClone(payload.checkpoint);
        revision += 1;
        return { revision };
      },
    },
  }, storage, "taxonomy_checkpoint_facebook_remote");
  assert.equal(result.checkpoint.status, "complete");
  assert.equal(remoteCheckpoint.status, "complete");
  assert.ok(revision > 4);
});

test("checkpoint projection persists taxonomy nodes before saving a compact remote checkpoint", async () => {
  const adapter = fixtureAdapter();
  const events = [];
  const persisted = new Set();
  const local = { value: null, async get(key) { return { [key]: this.value }; }, async set(value) { this.value = Object.values(value)[0]; events.push("local"); } };
  const result = await traverseWithStorage(adapter, {
    marketplace: "facebook", sourceVersion: "fixture-v1", maxNodes: 3,
    checkpointProjection(state) {
      return { ...state, persisted_category_keys: Array.from(persisted),
        categories: Object.fromEntries(Object.entries(state.categories).map(([key, node]) => [key, {
          terminal_status: node.terminal_status, required_fields_schema_status: node.required_fields_schema_status,
        }])) };
    },
    remoteCheckpoint: {
      async load() { return { checkpoint: null, revision: 0 }; },
      async save({ checkpoint }) {
        assert.ok(Object.keys(checkpoint.categories).every((key) => checkpoint.persisted_category_keys.includes(key)));
        events.push("remote");
        return { revision: events.filter((event) => event === "remote").length };
      },
    },
    async onCheckpoint(_projected, full) {
      for (const key of Object.keys(full.categories)) persisted.add(key);
      events.push("observations");
    },
  }, local, "compact");
  assert.equal(result.checkpoint.status, "checkpointed_at_node_limit");
  assert.ok(events.indexOf("observations") < events.indexOf("local"));
  assert.ok(events.indexOf("local") < events.indexOf("remote"));
});

test("taxonomy checkpoints refuse to mix observations from different source versions", async () => {
  const adapter = fixtureAdapter();
  const state = {};
  const storage = {
    async get(key) { return state[key] ? { [key]: structuredClone(state[key]) } : {}; },
    async set(values) { Object.assign(state, structuredClone(values)); },
  };
  const key = "taxonomy_checkpoint_facebook_versioned";
  await traverseWithStorage(adapter, { marketplace: "facebook", sourceVersion: "catalog-v1", maxNodes: 1 }, storage, key);
  await assert.rejects(
    traverseWithStorage(adapter, { marketplace: "facebook", sourceVersion: "catalog-v2", maxNodes: 1 }, storage, key),
    /TAXONOMY_SOURCE_VERSION_MISMATCH/,
  );
});

test("child limits and paginated branches are never marked complete", async () => {
  const adapter = fixtureAdapter();
  const result = await traverse(adapter, { marketplace: "facebook", maxChildren: 1 });
  assert.equal(result.checkpoint.status, "partial_unresolved");
  assert.equal(result.checkpoint.completed_paths.includes(""), false);
  assert.equal(result.checkpoint.unresolved_paths[""].reason, "CHILD_LIMIT_EXCEEDED");
  const paged = { ...adapter, async readChildren(path) {
    const result = await adapter.readChildren(path);
    return path.length === 0 ? { items: result.slice(0, 1), next_cursor: "more", complete: false } : result;
  } };
  const pageResult = await traverse(paged, { marketplace: "facebook" });
  assert.equal(pageResult.checkpoint.status, "partial_unresolved");
  assert.equal(pageResult.checkpoint.unresolved_paths[""].reason, "CATEGORY_PAGE_REMAINS");
  assert.ok(pageResult.checkpoint.frontier.some((entry) => (Array.isArray(entry) ? entry : entry.path).length === 0));
});

test("a terminal leaf without complete required-field evidence is not complete taxonomy coverage", async () => {
  const adapter = fixtureAdapter();
  adapter.captureRequiredFields = async () => [];
  const result = await traverse(adapter, { marketplace: "facebook" });
  const leaf = Object.values(result.checkpoint.categories).find((node) => node.terminal_status === "confirmed_leaf");
  assert.ok(leaf);
  assert.equal(leaf.required_fields_schema_status, "uncollected");
  assert.equal(result.checkpoint.status, "partial_unresolved");
  assert.ok(Object.values(result.checkpoint.unresolved_paths).some((entry) => entry.reason === "REQUIRED_FIELD_SCHEMA_UNVERIFIED"));
});

test("live branches without affirmative enumeration proof remain unresolved", async () => {
  const adapter = { ...fixtureAdapter(), isLive: true,
    async readChildren() { return { items: [{ label: "Home", category_id: "h" }], complete: true, selector_evidence: { end_reached: true } }; } };
  const result = await traverse(adapter, { marketplace: "facebook", authorization: {
    authorized: true, marketplace: "facebook", source: "marketplace_provided_interface",
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  } });
  assert.equal(result.checkpoint.status, "partial_unresolved");
  assert.equal(result.checkpoint.unresolved_paths[""].reason, "AFFIRMATIVE_COMPLETENESS_EVIDENCE_MISSING");
  assert.equal(result.checkpoint.completed_paths.includes(""), false);
});

test("live incomplete root enumeration persists positively associated visible categories without claiming leaves", async () => {
  const saved = [];
  const adapter = {
    isLive: true,
    async readChildren() {
      return { complete: false, reason: "CATEGORY_OPTIONS_STILL_LOADING", items: [
        { label: "Tools", category_id: "fb-tools", identity_key: "id:fb-tools", selector_evidence: { role: "button", tag: "div" } },
        { label: "Household", category_id: "fb-household", identity_key: "id:fb-household", selector_evidence: { role: "button", tag: "div" } },
      ], selector_evidence: { picker_association: { confirmation: "expanded_combobox_nearby_coherent_category_rows" } } };
    },
    async select() { throw new Error("partial enumeration must not select a category"); },
    async captureRequiredFields() { throw new Error("partial enumeration must not inspect a schema"); },
    async backtrack() {},
  };
  const authorization = { authorized: true, marketplace: "facebook", source: "marketplace_provided_interface",
    expires_at: new Date(Date.now() + 60000).toISOString() };
  const result = await traverse(adapter, { marketplace: "facebook", checkpoint: createCheckpoint("facebook"), authorization,
    onCheckpoint: async (checkpoint) => saved.push(checkpoint) });
  const categories = Object.values(result.checkpoint.categories);
  assert.deepEqual(categories.map((item) => item.category_name).sort(), ["Household", "Tools"]);
  assert.ok(categories.every((item) => item.terminal_status === "unknown" && item.required_fields_schema_status === "uncollected"));
  assert.equal(result.checkpoint.status, "partial_unresolved");
  assert.equal(Object.keys(result.checkpoint.unresolved_paths).length, 1);
  assert.ok(saved.some((checkpoint) => Object.keys(checkpoint.categories).length === 2));
});

test("duplicate category labels remain distinct by marketplace identity across branches", async () => {
  const adapter = {
    isLive: false,
    async readChildren(path, identities) {
      if (!path.length) return [{ label: "Home", category_id: "root-a" }, { label: "Home", category_id: "root-b" }];
      if (identities[0] === "id:root-a") return [{ label: "Tools", category_id: "tools-a" }];
      if (identities[0] === "id:root-b") return [{ label: "Furniture", category_id: "furniture-b" }];
      return [];
    },
    async select(child, path, identities) { return { category_id: child.category_id, terminal: false,
      children: await this.readChildren(path, identities), children_complete: true }; },
    async captureRequiredFields() { return { fields: [], complete: true, evidence: { inspected: true } }; },
    async backtrack() {},
  };
  const result = await traverse(adapter, { marketplace: "facebook" });
  const nodes = Object.values(result.checkpoint.categories);
  assert.equal(nodes.filter((node) => node.category_name === "Home").length, 2);
  assert.ok(nodes.some((node) => node.identity_path[0] === "id:root-a" && node.full_path.join(" > ") === "Home > Tools"));
  assert.ok(nodes.some((node) => node.identity_path[0] === "id:root-b" && node.full_path.join(" > ") === "Home > Furniture"));
});

test("interrupted observation persistence replays idempotently from the saved frontier", async () => {
  const adapter = fixtureAdapter();
  const storageState = {};
  const storage = { async get(key) { return storageState[key] ? { [key]: structuredClone(storageState[key]) } : {}; },
    async set(values) { Object.assign(storageState, structuredClone(values)); } };
  let persisted = new Set(); let failOnce = true;
  const options = { marketplace: "facebook", sourceVersion: "fixture-v1", maxNodes: 1,
    async onCheckpoint(_projected, full) {
      for (const key of Object.keys(full.categories || {})) persisted.add(key);
      if (failOnce) { failOnce = false; throw new Error("simulated process interruption after observation commit"); }
    } };
  await assert.rejects(traverseWithStorage(adapter, options, storage, "interruptible"), /simulated process interruption/);
  const resumed = await traverseWithStorage(adapter, { ...options, maxNodes: 20 }, storage, "interruptible");
  assert.equal(resumed.checkpoint.status, "complete");
  assert.equal(Object.keys(resumed.checkpoint.categories).length, 5);
  assert.ok(persisted.size >= 1);
});

test("diagnostic evidence extracts visible category options and observed path without interaction", () => {
  const option = { innerText: "Kitchen", textContent: "Kitchen", tagName: "DIV", getAttribute: (name) => name === "role" ? "option" : name === "data-category-id" ? "c-3" : null, getClientRects: () => [{}] };
  const result = captureVisibleCategoryEvidence({ querySelectorAll: () => [option] }, "facebook", {
    interaction_diagnostic: { requested_category_path: ["Home", "Kitchen"], live_hierarchy_options: ["Kitchen", "Furniture"] },
    selector_diagnostic: { selectors_tried: ["[role=combobox]"] },
  });
  assert.deepEqual(result.category_path, ["Home", "Kitchen"]);
  assert.deepEqual(result.visible_options, [{ label: "Kitchen", category_id: "c-3", role: "option", selector: "div" }]);
  assert.equal(result.source, "user_initiated_form_diagnostic");
});

test("Facebook selected category evidence preserves seller-form path but does not claim a terminal leaf", () => {
  const result = captureVisibleCategoryEvidence({ querySelectorAll: () => [] }, "facebook", {
    interaction_diagnostic: {
      stage: "SELECTION_VERIFIED", selected_hierarchy_path: ["Household"],
      first_visible_options: ["Household", "Furniture", "Garden", "Save draft", "Add photos", "Next"],
    },
  });
  assert.deepEqual(result.category_path, ["Household"]);
  assert.deepEqual(result.visible_options.map((option) => option.label), ["Household", "Furniture", "Garden"]);
  assert.equal(result.observation_scope, "seller_form_visible_category_options");
  assert.equal(result.selection_status, "selection_verified");
  assert.equal(result.leaf_status, "unknown");
  assert.equal(result.leaf_evidence, null);
});

test("Facebook diagnostic #222 shape normalizes the stored Household path and drops wizard controls", () => {
  const result = captureVisibleCategoryEvidence({ querySelectorAll: () => [] }, "facebook", {
    verified_value: "Category HouseholdShipping available",
    interaction_diagnostic: {
      stage: "SELECTION_VERIFIED", post_selection_field_text: "Category HouseholdShipping available",
      requested_category_path: ["HouseholdShipping available"],
      first_visible_options: ["Details", "HouseholdShipping available", "Furniture", "Select shipping label", "Next"],
    },
  });
  assert.deepEqual(result.category_path, ["Household"]);
  assert.deepEqual(result.visible_options.map((option) => option.label), ["Household", "Furniture"]);
  assert.equal(result.selection_status, "selection_verified");
  assert.equal(result.leaf_status, "unknown");
});

test("diagnostic captures labels and selector evidence for visible required marketplace controls without values", () => {
  const required = {
    tagName: "INPUT", required: true, value: "private value must not be captured", id: "brand-id",
    labels: [{ innerText: "Brand" }], getClientRects: () => [{}],
    getAttribute: (name) => name === "aria-required" ? "true" : null,
  };
  const documentObject = { querySelectorAll: (selector) => selector.startsWith("input,") ? [required] : [] };
  const result = captureVisibleCategoryEvidence(documentObject, "mercari");
  assert.deepEqual(result.required_fields, [{
    name: "Brand", required: true, detected: true,
    selector_evidence: {
      tag: "input", role: null,
      selectors_tried: ["input", "textarea", "select", "[contenteditable='true']", "[aria-required='true']"],
      visible_option_labels: [],
    },
  }]);
  assert.equal(JSON.stringify(result).includes("private value"), false);
});
