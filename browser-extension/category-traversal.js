/* Shared resumable marketplace taxonomy traversal primitives.
 * Marketplace adapters supply navigation; this module owns bounded DFS,
 * checkpoints, backtracking, leaf evidence, and authorization gating.
 */
(function attachPosterProTaxonomyTraversal(root) {
  "use strict";

  const SCHEMA_VERSION = 2;
  const LIVE_AUTH_SOURCES = new Set(["official_api", "marketplace_provided_interface", "written_permission"]);

  function normalizeLabel(value) {
    return String(value ?? "").normalize("NFKC").replace(/\s+/g, " ").trim();
  }

  function normalizeObservedCategoryLabel(value) {
    return normalizeLabel(value)
      .replace(/^category\s+/i, "")
      // Facebook appends its non-category shipping badge directly to the
      // accessible name on some rows (e.g. "HouseholdShipping available").
      .replace(/\s*shipping available$/i, "")
      .trim();
  }

  function pathKey(path, identities = []) {
    return path.map((part, index) => `${normalizeLabel(part).toLocaleLowerCase()}\u001e${identities[index] || ""}`).join("\u001f");
  }

  function createCheckpoint(marketplace, sourceVersion = null) {
    return {
      schema_version: SCHEMA_VERSION,
      marketplace: String(marketplace || "").toLowerCase(),
      source_version: sourceVersion,
      frontier: [{ path: [], identities: [] }],
      completed_paths: [],
      categories: {},
      updated_at: new Date().toISOString(),
      status: "in_progress",
    };
  }

  function validateCheckpoint(checkpoint, marketplace) {
    if (!checkpoint || ![1, SCHEMA_VERSION].includes(checkpoint.schema_version)
        || checkpoint.marketplace !== String(marketplace || "").toLowerCase()
        || !Array.isArray(checkpoint.frontier) || !Array.isArray(checkpoint.completed_paths)
        || !checkpoint.categories || typeof checkpoint.categories !== "object") {
      throw new Error("TAXONOMY_CHECKPOINT_INVALID");
    }
    if (checkpoint.schema_version === 1) {
      checkpoint.frontier = checkpoint.frontier.map((entry) => Array.isArray(entry) ? { path: entry, identities: [] } : entry);
      checkpoint.schema_version = SCHEMA_VERSION;
    }
    return checkpoint;
  }

  function requireAuthorization(adapter, grant, marketplace) {
    if (!adapter || typeof adapter.readChildren !== "function" || typeof adapter.select !== "function"
        || typeof adapter.backtrack !== "function" || typeof adapter.captureRequiredFields !== "function") {
      throw new Error("TAXONOMY_ADAPTER_INCOMPLETE");
    }
    if (adapter.isLive !== true) return;
    const source = String(grant?.source || "");
    const expires = Date.parse(grant?.expires_at || "");
    if (grant?.authorized !== true || grant.marketplace !== marketplace
        || !LIVE_AUTH_SOURCES.has(source) || !Number.isFinite(expires) || expires <= Date.now()) {
      throw new Error("TAXONOMY_LIVE_AUTHORIZATION_REQUIRED");
    }
  }

  async function traverse(adapter, {
    marketplace,
    sourceVersion = null,
    checkpoint = createCheckpoint(marketplace, sourceVersion),
    authorization = null,
    maxNodes = 250,
    maxDepth = 12,
      maxChildren = 500,
    onCheckpoint = async () => {},
  } = {}) {
    const market = String(marketplace || "").toLowerCase();
    if (!market) throw new Error("TAXONOMY_MARKETPLACE_REQUIRED");
    requireAuthorization(adapter, authorization, market);
    const state = validateCheckpoint(checkpoint, market);
    if (sourceVersion != null && state.source_version !== sourceVersion) {
      throw new Error("TAXONOMY_SOURCE_VERSION_MISMATCH");
    }
    const nodeLimit = Math.max(1, Math.min(Number(maxNodes) || 250, 2000));
    const depthLimit = Math.max(1, Math.min(Number(maxDepth) || 12, 24));
    const childLimit = Math.max(1, Math.min(Number(maxChildren) || 500, 2000));
    let visitedThisRun = 0;

    while (state.frontier.length && visitedThisRun < nodeLimit) {
      if (root.__POSTERPRO_TAXONOMY_PAUSE_REQUESTED === true) {
        state.status = "stopped";
        await onCheckpoint(JSON.parse(JSON.stringify(state)));
        break;
      }
      const entry = state.frontier.pop();
      const parentPath = Array.isArray(entry) ? entry : entry?.path;
      const parentIdentities = Array.isArray(entry) ? [] : (Array.isArray(entry?.identities) ? entry.identities : []);
      if (!Array.isArray(parentPath)) continue;
      const parentKey = pathKey(parentPath, parentIdentities);
      if (state.completed_paths.includes(parentKey)) continue;
      if (parentPath.length >= depthLimit) {
        state.unresolved_paths ||= {};
        state.unresolved_paths[parentKey] = { path: parentPath.slice(), identities: parentIdentities.slice(), reason: "MAX_DEPTH_REACHED" };
        state.status = "partial_unresolved";
        await onCheckpoint(JSON.parse(JSON.stringify(state)));
        continue;
      }

      let children;
      try {
        const response = await adapter.readChildren(parentPath.slice(), parentIdentities.slice());
        children = response;
        // Adapters may return a page object. A next cursor means the branch is
        // not complete; silently slicing it would lose categories permanently.
        if (response && !Array.isArray(response) && Array.isArray(response.items)) {
          const affirmative = response.completeness_evidence && typeof response.completeness_evidence === "object"
            && response.completeness_evidence.end_reached === true
            && response.completeness_evidence.loading === false
            && response.completeness_evidence.pagination_control === false;
          if (response.next_cursor != null || response.complete !== true || (adapter.isLive === true && !affirmative)) {
            state.unresolved_paths ||= {};
            const association = response.selector_evidence?.picker_association?.confirmation;
            const observedRows = Array.isArray(response.items) ? response.items.filter((child) => {
              const role = String(child?.selector_evidence?.role || "").toLowerCase();
              const tag = String(child?.selector_evidence?.tag || "").toLowerCase();
              return normalizeLabel(child?.label) && (role === "option" || role === "treeitem" || role === "menuitem"
                || role === "menuitemradio" || (role === "button" && tag === "div"));
            }) : [];
            const positivelyAssociated = ["expanded_combobox_nearby_coherent_category_rows", "new_visible_category_rows",
              "semantic_control_relationship"].includes(association);
            // Incomplete enumeration is not a reason to discard positively
            // associated rows already visible in the real seller picker. Save
            // them as observations only: branch/leaf and field schema remain
            // explicitly unknown until a later exhaustive pass verifies them.
            if (positivelyAssociated) {
              for (const child of observedRows) {
                const label = normalizeLabel(child.label);
                const identity = child.category_id != null ? `id:${child.category_id}` : String(child.identity_key || `label:${label}`);
                const identityPath = [...parentIdentities, identity];
                const path = [...parentPath, label];
                const key = pathKey(path, identityPath);
                if (state.categories[key]) continue;
                state.categories[key] = {
                  marketplace: market, category_id: typeof child.category_id === "string" ? child.category_id.slice(0, 160) : null,
                  category_name: label, category_identity: identity, identity_path: identityPath,
                  parent_path: parentPath.slice(), full_path: path, depth: path.length,
                  terminal_status: "unknown", terminal_evidence: null, required_fields: [],
                  required_fields_schema_status: "uncollected", required_fields_evidence: null,
                  selector_evidence: { ...(child.selector_evidence || {}),
                    picker_association: association, observation_state: "visible_candidate_unresolved",
                    observed_without_complete_enumeration: true, submission_performed: false },
                  source: "authorized_live_interface",
                };
              }
            }
            state.unresolved_paths[parentKey] = {
              path: parentPath.slice(), identities: parentIdentities.slice(), reason: response.next_cursor != null ? "CATEGORY_PAGE_REMAINS"
                : response.complete !== true ? (response.reason || "CATEGORY_ENUMERATION_INCOMPLETE") : "AFFIRMATIVE_COMPLETENESS_EVIDENCE_MISSING",
              next_cursor: response.next_cursor ?? null,
              observed_candidate_count: observedRows.length,
              selector_association: positivelyAssociated ? association : null,
            };
            if (!state.frontier.some((item) => pathKey(item?.path || [], item?.identities || []) === parentKey)) {
              state.frontier.push({ path: parentPath.slice(), identities: parentIdentities.slice() });
            }
            state.status = "partial_unresolved";
            state.updated_at = new Date().toISOString();
            await onCheckpoint(JSON.parse(JSON.stringify(state)));
            break;
          }
          children = response.items;
          state.branch_evidence ||= {};
          state.branch_evidence[parentKey] = response.completeness_evidence || response.selector_evidence || null;
          delete state.unresolved_paths?.[parentKey];
        }
        if (!Array.isArray(children)) throw new Error("TAXONOMY_CHILD_LIST_INVALID");
        if (children.length > childLimit) {
          state.unresolved_paths ||= {};
          state.unresolved_paths[parentKey] = { path: parentPath.slice(), identities: parentIdentities.slice(), reason: "CHILD_LIMIT_EXCEEDED", observed_count: children.length };
          state.frontier.push({ path: parentPath.slice(), identities: parentIdentities.slice() });
          state.status = "partial_unresolved";
          await onCheckpoint(JSON.parse(JSON.stringify(state)));
          break;
        }
      } catch (error) {
        state.frontier.push({ path: parentPath, identities: parentIdentities });
        state.status = "checkpointed_with_error";
        state.last_error = String(error?.code || error?.message || "TAXONOMY_READ_FAILED").slice(0, 100);
        break;
      }

      for (const child of children) {
        const label = normalizeLabel(child?.label);
        if (!label) continue;
        const path = [...parentPath, label];
        const identity = child?.category_id != null ? `id:${child.category_id}` : String(child?.identity_key || `label:${label}`);
        const identityPath = [...parentIdentities, identity];
        const key = pathKey(path, identityPath);
        const priorNode = state.categories[key];
        if (priorNode && priorNode.terminal_status !== "unknown"
            && !(priorNode.terminal_status === "confirmed_leaf"
              && !["verified_complete", "verified_empty"].includes(priorNode.required_fields_schema_status))) continue;
        let selection = {};
        try {
          selection = await adapter.select(child, path.slice(), identityPath.slice()) || {};
          const captured = await adapter.captureRequiredFields(child, path.slice(), identityPath.slice());
          const fields = Array.isArray(captured) ? captured : Array.isArray(captured?.fields) ? captured.fields : [];
          const schemaComplete = captured?.complete === true && Boolean(captured?.evidence);
          const descendants = Array.isArray(selection.children) ? selection.children : null;
          const affirmativeTerminal = selection.terminal_evidence && typeof selection.terminal_evidence === "object"
            && selection.terminal_evidence.selection_persisted === true
            && selection.terminal_evidence.picker_settled === true
            && selection.terminal_evidence.child_enumeration_complete === true
            && Array.isArray(selection.terminal_evidence.child_options)
            && selection.terminal_evidence.child_options.length === 0;
          const terminal = selection.terminal === true && (adapter.isLive !== true || affirmativeTerminal);
          const terminalStatus = terminal ? "confirmed_leaf"
            : descendants?.length && selection.children_complete === true ? "branch" : "unknown";
          state.categories[key] = {
            marketplace: market,
            category_id: typeof selection.category_id === "string" ? selection.category_id.slice(0, 160) : null,
            category_name: label,
            category_identity: identity,
            identity_path: identityPath.slice(),
            parent_path: parentPath.slice(),
            full_path: path,
            depth: path.length,
            terminal_status: terminalStatus,
            terminal_evidence: selection.terminal_evidence || null,
            required_fields: fields,
            required_fields_schema_status: schemaComplete ? (fields.length ? "verified_complete" : "verified_empty") : (captured?.incomplete === true ? "incomplete" : "uncollected"),
            required_fields_evidence: captured?.evidence || null,
            selector_evidence: selection.selector_evidence || child.selector_evidence || null,
            source: adapter.isLive ? "authorized_live_interface" : "test_fixture_or_import",
          };
          visitedThisRun += 1;
          if (descendants?.length && selection.children_complete === true && path.length < depthLimit) state.frontier.push({ path, identities: identityPath });
          else if (terminalStatus === "unknown") {
            state.unresolved_paths ||= {};
            state.unresolved_paths[key] = { path, identities: identityPath, reason: "TERMINAL_STATUS_UNVERIFIED" };
          }
          if (terminal && !schemaComplete) {
            state.unresolved_paths ||= {};
            state.unresolved_paths[key] = { path, identities: identityPath, reason: "REQUIRED_FIELD_SCHEMA_UNVERIFIED" };
          }
          if (terminalStatus !== "unknown" && (!terminal || schemaComplete)) delete state.unresolved_paths?.[key];
        } catch (error) {
          state.categories[key] = priorNode ? { ...priorNode, last_retry_error: String(error?.code || error?.message || "TAXONOMY_NODE_FAILED").slice(0, 100) } : {
            marketplace: market, category_id: null, category_name: label,
            parent_path: parentPath.slice(), full_path: path, depth: path.length,
            terminal_status: "unknown", required_fields: [], required_fields_schema_status: "uncollected",
            error: String(error?.code || error?.message || "TAXONOMY_NODE_FAILED").slice(0, 100),
          };
          visitedThisRun += 1;
        } finally {
          await adapter.backtrack(parentPath.slice(), parentIdentities.slice());
        }
        if (visitedThisRun >= nodeLimit) break;
      }
      const parentHasUnresolved = Object.entries(state.unresolved_paths || {}).some(([key, item]) => key === parentKey || pathKey(item.path || [], item.identities || []) === parentKey);
      const unresolvedDescendant = Object.values(state.unresolved_paths || {}).some((item) => {
        const path = item.path || []; const identities = item.identities || [];
        return path.length > parentPath.length && pathKey(path.slice(0, parentPath.length), identities.slice(0, parentPath.length)) === parentKey;
      });
      if (visitedThisRun < nodeLimit && !parentHasUnresolved && !unresolvedDescendant) {
        state.completed_paths.push(parentKey);
        delete state.unresolved_paths?.[parentKey];
      }
      else state.frontier.push({ path: parentPath, identities: parentIdentities });
      state.updated_at = new Date().toISOString();
      state.status = Object.keys(state.unresolved_paths || {}).length ? "partial_unresolved"
        : state.frontier.length ? "in_progress" : "complete";
      await onCheckpoint(JSON.parse(JSON.stringify(state)));
      if (parentHasUnresolved || unresolvedDescendant) break;
    }
    if (state.frontier.length && visitedThisRun >= nodeLimit && !Object.keys(state.unresolved_paths || {}).length) state.status = "checkpointed_at_node_limit";
    state.updated_at = new Date().toISOString();
    await onCheckpoint(JSON.parse(JSON.stringify(state)));
    return { checkpoint: state, visited_this_run: visitedThisRun };
  }

  async function traverseWithStorage(adapter, options, storage, storageKey) {
    if (!storage || typeof storage.get !== "function" || typeof storage.set !== "function" || !storageKey) {
      throw new Error("TAXONOMY_CHECKPOINT_STORAGE_UNAVAILABLE");
    }
    const existing = await storage.get(storageKey);
    let checkpoint = existing?.[storageKey] || createCheckpoint(options?.marketplace, options?.sourceVersion || null);
    let remoteRevision = Number(options?.remoteRevision || 0);
    if (options?.remoteCheckpoint && typeof options.remoteCheckpoint.load === "function") {
      const remote = await options.remoteCheckpoint.load();
      if (remote?.checkpoint) {
        checkpoint = remote.checkpoint;
        remoteRevision = Number(remote.revision || 0);
        await storage.set({ [storageKey]: checkpoint });
      }
    }
    const save = options?.onCheckpoint;
    const project = typeof options?.checkpointProjection === "function"
      ? options.checkpointProjection : (value) => value;
    return traverse(adapter, {
      ...options,
      checkpoint,
      onCheckpoint: async (value) => {
        if (typeof save === "function") await save(null, value);
        const persistedValue = project(JSON.parse(JSON.stringify(value)));
        await storage.set({ [storageKey]: persistedValue });
        if (options?.remoteCheckpoint && typeof options.remoteCheckpoint.save === "function") {
          const saved = await options.remoteCheckpoint.save({ checkpoint: persistedValue, expected_revision: remoteRevision });
          if (!Number.isInteger(saved?.revision) || saved.revision <= remoteRevision) {
            throw new Error("TAXONOMY_REMOTE_CHECKPOINT_REVISION_INVALID");
          }
          remoteRevision = saved.revision;
        }
        if (typeof options?.onDurableCheckpoint === "function") {
          await options.onDurableCheckpoint(persistedValue, remoteRevision);
        }
      },
    });
  }

  function captureVisibleCategoryEvidence(documentObject, marketplace, fieldResult = null, allFieldResults = []) {
    const interaction = fieldResult?.interaction_diagnostic || {};
    const selector = fieldResult?.selector_diagnostic || {};
    const controls = Array.from(documentObject?.querySelectorAll?.("[role='option'],[role='treeitem'],[role='menuitem'],option") || [])
      .filter((node) => node && node.getClientRects?.().length)
      .slice(0, 60)
      .map((node) => ({
        label: normalizeLabel(node.innerText || node.textContent || node.getAttribute?.("aria-label"))
          .replace(/\s*shipping available$/i, "").trim(),
        category_id: node.getAttribute?.("data-category-id") || node.getAttribute?.("data-id") || null,
        role: node.getAttribute?.("role") || null,
        selector: node.tagName?.toLowerCase() || null,
      })).filter((row) => row.label);
    const selectedText = interaction.post_selection_field_text || fieldResult?.verified_value || "";
    const selectedCategory = normalizeObservedCategoryLabel(selectedText);
    const requestedPath = interaction.requested_category_path || interaction.category_path || interaction.selected_hierarchy_path
      || (selectedCategory ? [selectedCategory] : []);
    const observed = interaction.category_picker_options || interaction.live_hierarchy_options
      || selector.category_picker_options || selector.visible_option_labels || [];
    // Some diagnostics predate the category-picker-specific field and saved a
    // broad visible-control sample. Keep category-like labels from that sample
    // for coverage evidence, but drop known wizard controls and never infer IDs
    // or terminal status from labels alone.
    const legacyObserved = interaction.first_visible_options || [];
    const nonCategoryControl = /^(save draft|add photos?(?: or drag and drop)?|add video.*|next|previous|back|cancel|close|publish|delivery method|select shipping label|condition|location|price|title|description|details|more details.*|review|learn more|try it|commerce policies|seller details|seller information|category|search facebook|matthew ruderman)$/i;
    const optionLabels = Array.isArray(observed) && observed.length ? observed : legacyObserved;
    const cleanObserved = Array.isArray(optionLabels) ? optionLabels.map((label) => normalizeLabel(label).replace(/\s*shipping available$/i, "").trim())
      .filter((label) => label && !nonCategoryControl.test(label)) : [];
    const explicitLeaf = interaction.terminal_leaf === true || interaction.leaf_status === "confirmed_leaf"
      || interaction.category_resolution_stage === "TERMINAL_LEAF_CONFIRMED";
    const requiredFields = (Array.isArray(allFieldResults) ? allFieldResults : [])
      .filter((row) => row?.required === true)
      .slice(0, 40)
      .map((row) => ({
        name: String(row.field || "").slice(0, 64),
        required: true,
        detected: row.detected === true,
        filled: row.filled === true,
        error_code: typeof row.error_code === "string" ? row.error_code.slice(0, 100) : null,
        selector_evidence: row.selector_diagnostic ? {
          selectors_tried: Array.isArray(row.selector_diagnostic.selectors_tried) ? row.selector_diagnostic.selectors_tried.slice(0, 8) : [],
          visible_option_labels: Array.isArray(row.selector_diagnostic.visible_option_labels) ? row.selector_diagnostic.visible_option_labels.slice(0, 20) : [],
        } : null,
      }));
    const requiredControls = Array.from(documentObject?.querySelectorAll?.("input,textarea,select,[contenteditable='true'],[aria-required='true']") || [])
      .filter((node) => node && node.getClientRects?.().length
        && (node.required === true || String(node.getAttribute?.("aria-required") || "").toLowerCase() === "true"))
      .slice(0, 40)
      .map((node) => {
        const label = node.labels?.[0]?.innerText || node.labels?.[0]?.textContent
          || node.getAttribute?.("aria-label") || node.getAttribute?.("placeholder")
          || node.getAttribute?.("name") || node.id || "unlabeled_required_control";
        return {
          name: normalizeLabel(label).slice(0, 64), required: true, detected: true,
          selector_evidence: {
            tag: String(node.tagName || "").toLowerCase() || null,
            role: node.getAttribute?.("role") || null,
            selectors_tried: ["input", "textarea", "select", "[contenteditable='true']", "[aria-required='true']"],
            visible_option_labels: [],
          },
        };
      });
    for (const field of requiredControls) {
      if (!requiredFields.some((known) => known.name.toLocaleLowerCase() === field.name.toLocaleLowerCase())) requiredFields.push(field);
    }
    return {
      marketplace: String(marketplace || "").toLowerCase(),
      captured_at: new Date().toISOString(),
      source: "user_initiated_form_diagnostic",
      category_path: Array.isArray(requestedPath) ? requestedPath.map(normalizeObservedCategoryLabel).filter(Boolean).slice(0, 16) : [],
      observation_scope: "seller_form_visible_category_options",
      selection_status: interaction.stage === "SELECTION_VERIFIED" || interaction.stage === "CATEGORY_PERSISTENCE_VERIFIED" ? "selection_verified" : "observed",
      // Prefer the current DOM picker rows when they are available, because
      // those rows may include real category identifiers and roles. Fall back
      // to label-only observations for older diagnostics that captured only
      // the broad visible text sample.
      visible_options: controls.length ? controls : cleanObserved.length
        ? cleanObserved.slice(0, 60).map((label) => ({ label, category_id: null, role: null, selector: null }))
        : controls,
      selectors_tried: Array.isArray(selector.selectors_tried) ? selector.selectors_tried.slice(0, 12) : [],
      required_fields: requiredFields.slice(0, 80),
      leaf_status: explicitLeaf ? "confirmed_leaf" : "unknown",
      leaf_evidence: explicitLeaf ? (interaction.category_resolution_stage || "TERMINAL_LEAF_CONFIRMED") : null,
    };
  }

  const api = { SCHEMA_VERSION, normalizeLabel, pathKey, createCheckpoint, validateCheckpoint, traverse, traverseWithStorage, captureVisibleCategoryEvidence };
  root.PosterProCategoryTraversal = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
