(function attach(root) {
  "use strict";
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const visible = (el) => Boolean(el && el.isConnected !== false && el.getClientRects?.().length
    && root.getComputedStyle?.(el)?.visibility !== "hidden" && root.getComputedStyle?.(el)?.display !== "none");
  const label = (el) => norm(el && (el.getAttribute?.("aria-label") || el.innerText || el.textContent));
  const CATEGORY_WORD = /\bcategory\b/i;
  const LOADING = /^(loading|please wait|fetching|updating)\b/i;
  const PAGE = /^(next|more|show more|load more)$/i;
  const FACEBOOK_OPTION_SELECTOR = "[role='option'],[role='treeitem'],[role='menuitem'],[role='menuitemradio'],[role='button'],button";
  // Facebook's seller category menu is a portal. The successful form
  // diagnostic observes DIV[role=button] rows, not a listbox/tree wrapper.
  // Keep the portal boundary tied to rows that became visible after clicking
  // the known category field; never treat arbitrary page buttons as options.
  let pendingPortalEvidence = null;
  let inferredPortal = null;
  const inferredContainers = new WeakSet();
  const inferredContainerEvidence = new WeakMap();

  function checkStopped() {
    if (root.__POSTERPRO_TAXONOMY_STOP_REQUESTED === true) throw Object.assign(new Error("COLLECTION_STOP_REQUESTED"), { code: "COLLECTION_STOP_REQUESTED" });
  }
  function categoryControl(doc = root.document) {
    // Reuse the same field resolver as the working Marketplace form
    // automation (diagnostic #226). It prefers the adapter's category field
    // selector and the actual role=combobox label instead of guessing among
    // every page button containing the word "category".
    const sharedResolver = root.ppFindField;
    const selectors = root.POSTERPRO_MARKETPLACE_ADAPTERS?.facebook?.category || [];
    if (doc === root.document && typeof sharedResolver === "function") {
      const resolved = sharedResolver(selectors, ["category"]);
      if (resolved && visible(resolved)) return resolved;
    }
    return Array.from(doc.querySelectorAll("[role='combobox'],button,[role='button'],label"))
      .filter(visible).filter((el) => CATEGORY_WORD.test(label(el)))
      .sort((a, b) => label(a).length - label(b).length)[0] || null;
  }
  function categoryOptionCandidates(doc = root.document) {
    const sharedCandidates = doc === root.document && typeof root.ppFacebookVisibleCategoryOptionCandidates === "function"
      ? root.ppFacebookVisibleCategoryOptionCandidates() : Array.from(doc.querySelectorAll(FACEBOOK_OPTION_SELECTOR));
    const actionableRows = new Set(sharedCandidates.map((el) => el.closest?.(FACEBOOK_OPTION_SELECTOR) || el));
    return Array.from(actionableRows).filter((el) => {
      if (!visible(el) || el.disabled || !label(el) || label(el).length > 180) return false;
      if (String(el.getAttribute?.("role") || "").toLowerCase() === "heading") return false;
      // Keep a positively shaped category row even if Facebook nests a
      // decorative/actionable descendant inside it. Only suppress it when a
      // nested node is itself another category row, which avoids duplicate
      // parent/child candidates without dropping DIV[role=button] options.
      return !Array.from(el.querySelectorAll?.(FACEBOOK_OPTION_SELECTOR) || []).some(categoryRowShape);
    });
  }
  function categoryRowShape(node) {
    const role = String(node?.getAttribute?.("role") || "").toLowerCase();
    if (["option", "treeitem", "menuitem", "menuitemradio"].includes(role)) return true;
    // The real Facebook seller form diagnostic (#228) recorded category rows
    // as DIV[role=button]. Plain buttons and arbitrary page-level role=button
    // controls are deliberately excluded from this non-semantic portal path.
    return node?.tagName?.toLowerCase() === "div" && role === "button";
  }
  function commonAncestor(nodes) {
    if (!nodes.length) return null;
    const ancestors = [];
    for (let node = nodes[0]; node; node = node.parentElement) ancestors.push(node);
    return ancestors.find((candidate) => nodes.every((node) => candidate === node || candidate.contains?.(node))) || null;
  }
  function inferredCategoryPortal(doc = root.document) {
    const control = categoryControl(doc);
    const expanded = String(control?.getAttribute?.("aria-expanded") || "").toLowerCase() === "true";
    if (expanded && inferredPortal?.isConnected !== false && inferredPortal && visible(inferredPortal)) return inferredPortal;
    const evidence = pendingPortalEvidence;
    if (!evidence || evidence.doc !== doc) return null;
    const current = categoryOptionCandidates(doc).filter(categoryRowShape);
    const appeared = current.filter((node) => !evidence.before.has(node));
    if (appeared.length < 2) return null;
    const container = commonAncestor(appeared);
    if (!container || container === doc.body || container === doc.documentElement) return null;
    // Require a real cluster of newly-visible actionable rows. The actual
    // Facebook diagnostic exposed multiple category DIV[role=button] rows in
    // a detached portal; a single unrelated button cannot satisfy this.
    const rows = current.filter((node) => container === node || container.contains?.(node));
    if (rows.length < 2 || appeared.length < 2) return null;
    inferredPortal = container;
    inferredContainers.add(container);
    inferredContainerEvidence.set(container, { confirmation: "new_visible_category_rows", observed_category_row_count: rows.length });
    return container;
  }
  function expandedCategoryPortal(doc = root.document, control = categoryControl(doc)) {
    // Facebook can keep its menu row nodes mounted while the menu is closed.
    // In that case the rows are already in the DOM before the click, so a
    // newly-inserted-node check cannot identify the portal. Use the expanded
    // state of the *resolved Category combobox* and a nearby, coherent group
    // of the DIV[role=button] rows observed in the real seller form.
    if (!control || String(control.getAttribute?.("aria-expanded") || "").toLowerCase() !== "true") return null;
    const rows = categoryOptionCandidates(doc).filter((node) => {
      const text = label(node);
      return node !== control && categoryRowShape(node)
        && text && text.length <= 180 && !/^(save|save draft|publish|next|back|cancel|close|create|help|learn more|try it)$/i.test(text);
    });
    if (rows.length < 2) return null;
    const controlRect = control.getBoundingClientRect?.();
    const gap = (a, b) => {
      if (!a || !b || !Number.isFinite(a.left) || !Number.isFinite(b.left)) return Infinity;
      const dx = Math.max(0, a.left - b.right, b.left - a.right);
      const dy = Math.max(0, a.top - b.bottom, b.top - a.bottom);
      return Math.hypot(dx, dy);
    };
    const candidates = new Map();
    for (const row of rows) {
      let ancestor = row.parentElement;
      for (let depth = 0; ancestor && ancestor !== doc.body && ancestor !== doc.documentElement && depth < 10;
        depth += 1, ancestor = ancestor.parentElement) {
        if (ancestor === control || ancestor.contains?.(control)) continue;
        const members = rows.filter((item) => ancestor === item || ancestor.contains?.(item));
        if (members.length < 2) continue;
        const rect = ancestor.getBoundingClientRect?.();
        const distance = gap(controlRect, rect);
        // A portal should be visually associated with its expanded control.
        // This bound rejects distant navigation/action clusters while leaving
        // room for tall, scrolled Marketplace forms.
        if (distance > 900) continue;
        // Collapse ancestor wrappers that contain exactly the same rows. Keep
        // the closest common container so unrelated controls elsewhere in a
        // wider page wrapper cannot be swept into the option list.
        const memberKey = members.map((item) => rows.indexOf(item)).sort((a, b) => a - b).join(",");
        const prior = candidates.get(memberKey);
        if (!prior || depth < prior.depth || (depth === prior.depth && distance < prior.distance)) {
          candidates.set(memberKey, { container: ancestor, members, distance, depth });
        }
      }
    }
    const ranked = Array.from(candidates.values()).sort((a, b) => b.members.length - a.members.length
      || a.distance - b.distance || a.depth - b.depth);
    const best = ranked[0];
    if (!best || best.members.length < 2) return null;
    // The best cluster must be materially stronger than any other nearby
    // group; ties are ambiguous and remain unresolved instead of classifying
    // arbitrary page buttons as category options.
    const runnerUp = ranked.find((item) => item !== best);
    if (runnerUp && runnerUp.members.length >= best.members.length - 1 && Math.abs(runnerUp.distance - best.distance) < 80) return null;
    inferredPortal = best.container;
    inferredContainers.add(best.container);
    inferredContainerEvidence.set(best.container, { confirmation: "expanded_combobox_nearby_coherent_category_rows",
      observed_category_row_count: best.members.length, distance_px: Math.round(best.distance) });
    pendingPortalEvidence = null;
    return best.container;
  }
  async function waitForCategoryPicker({ timeoutMs = 3500 } = {}) {
    const started = Date.now();
    let mutationWake = null;
    let observer = null;
    try {
      if (typeof root.MutationObserver === "function" && root.document?.documentElement) {
        observer = new root.MutationObserver(() => mutationWake?.());
        observer.observe(root.document.documentElement, { childList: true, subtree: true, attributes: true });
      }
      while (Date.now() - started < timeoutMs) {
        checkStopped();
        const picker = pickerContainer(root.document, { allowInferred: true });
        if (picker) return { picker, wait_ms: Date.now() - started, confirmation: inferredContainers.has(picker) ? "new_visible_category_rows" : "semantic_relationship" };
        const remain = timeoutMs - (Date.now() - started);
        if (remain <= 0) break;
        const mutation = new Promise((resolve) => { mutationWake = resolve; });
        await Promise.race([wait(Math.min(150, remain)), mutation]);
        mutationWake = null;
      }
      return { picker: null, wait_ms: Date.now() - started, confirmation: "not_confirmed" };
    } finally {
      mutationWake = null;
      observer?.disconnect();
    }
  }
  function pickerContainer(doc = root.document, { allowInferred = true } = {}) {
    const control = categoryControl(doc);
    if (!control) return null;
    const ids = [control.getAttribute("aria-controls"), control.getAttribute("aria-owns")].filter(Boolean);
    for (const id of ids) {
      const target = doc.getElementById(id);
      if (target && visible(target) && (target.matches?.("[role='listbox'],[role='tree'],[role='menu'],[role='dialog']")
        || target.querySelector?.("[role='listbox'],[role='tree'],[role='menu']"))) return target;
    }
    // A category picker must have a structural relationship: a dialog/popup
    // containing a category heading and a list/tree, or a list/tree explicitly
    // labelled by the category control. Never inspect arbitrary page buttons.
    const labelled = doc.querySelectorAll("[role='listbox'][aria-labelledby],[role='tree'][aria-labelledby]");
    for (const list of labelled) {
      const refs = String(list.getAttribute("aria-labelledby") || "").split(/\s+/);
      if (refs.includes(control.id) || refs.includes(control.getAttribute("aria-labelledby"))) return list;
    }
    const dialogs = Array.from(doc.querySelectorAll("[role='dialog'],[aria-modal='true']")).filter(visible);
    for (const dialog of dialogs) {
      const heading = norm(dialog.getAttribute("aria-label") || dialog.querySelector("h1,h2,h3,[role='heading']")?.textContent);
      if (CATEGORY_WORD.test(heading) && dialog.querySelector("[role='listbox'],[role='tree'],[role='menu']")) return dialog;
    }
    if (allowInferred) {
      const insertedPortal = inferredCategoryPortal(doc);
      if (insertedPortal) return insertedPortal;
      return expandedCategoryPortal(doc, control);
    }
    return null;
  }
  function listStructure(container) {
    if (!container) return null;
    if (inferredContainers.has(container)) return container;
    if (container.matches?.("[role='listbox'],[role='tree'],[role='menu']")) return container;
    return container.querySelector?.("[role='listbox'],[role='tree'],[role='menu']") || null;
  }
  function optionElements(list) {
    if (!list) return [];
    return Array.from(list.querySelectorAll(FACEBOOK_OPTION_SELECTOR))
      .filter(visible)
      .filter((el) => !el.querySelectorAll?.(FACEBOOK_OPTION_SELECTOR)?.length
        || (categoryRowShape(el) && !Array.from(el.querySelectorAll(FACEBOOK_OPTION_SELECTOR)).some(categoryRowShape)))
      .filter((el) => String(el.getAttribute?.("role") || "").toLowerCase() !== "heading")
      .filter((el) => !inferredContainers.has(list) || categoryRowShape(el));
  }
  function optionIdentity(el, index) {
    const text = label(el);
    const id = el.getAttribute("data-category-id") || el.getAttribute("data-id") || el.getAttribute("data-key") || el.getAttribute("value") || null;
    return { el, label: text.replace(/\s*shipping available$/i, "").trim(), category_id: id,
      identity_key: id ? `id:${id}` : el.getAttribute("aria-posinset") ? `position:${el.getAttribute("aria-posinset")}` : `label:${text.toLocaleLowerCase()}`,
      role: el.getAttribute("role"), selector_evidence: { role: el.getAttribute("role"), tag: el.tagName?.toLowerCase(),
        id: el.id || null, data_category_id: el.getAttribute("data-category-id"), data_id: el.getAttribute("data-id"),
        aria_posinset: el.getAttribute("aria-posinset"), aria_setsize: el.getAttribute("aria-setsize") } };
  }
  function loadingEvidence(container) {
    const list = listStructure(container) || container;
    if (!list) return false;
    // Facebook's page shell can keep unrelated status/spinner nodes mounted
    // beside the portal. Only treat the actual option list as busy. A stable,
    // visible category set remains useful evidence even when the shell is
    // loading another part of the form.
    if (String(list.getAttribute?.("aria-busy") || "").toLowerCase() === "true") return true;
    const rows = optionElements(list);
    const scroll = scrollContainer(list, rows);
    const region = scroll || list;
    return Array.from(region.querySelectorAll?.("[aria-busy='true'],[role='status'],[role='progressbar']") || [])
      .some((el) => visible(el) && (String(el.getAttribute?.("aria-busy") || "").toLowerCase() === "true"
        || LOADING.test(label(el))));
  }
  function scrollContainer(list, rows = optionElements(list)) {
    const candidates = new Set();
    for (const row of rows) {
      for (let node = row; node && node !== (root.document?.body || null); node = node.parentElement) {
        if (node === list || list?.contains?.(node)) candidates.add(node);
        if (node === list) break;
      }
    }
    // Semantic listboxes themselves are common scroll owners; mocked and
    // older Facebook DOMs may not expose row parent links.
    candidates.add(list);
    for (let node = list?.parentElement; node && node !== (root.document?.body || null); node = node.parentElement) {
      if (node.scrollHeight > node.clientHeight + 2 && node.clientHeight > 0) candidates.add(node);
    }
    return Array.from(candidates).filter((node) => node?.scrollHeight > node?.clientHeight + 2 && node?.clientHeight > 0)
      .sort((a, b) => {
        const aCount = rows.filter((row) => a === row || a.contains?.(row)).length;
        const bCount = rows.filter((row) => b === row || b.contains?.(row)).length;
        return bCount - aCount;
      })[0] || null;
  }
  async function settled(container) {
    let stable = 0; let last = "";
    for (let i = 0; i < 20; i += 1) {
      checkStopped();
      const list = listStructure(container);
      const sig = optionElements(list).map((el) => `${el.getAttribute("data-category-id") || el.id || ""}:${label(el)}`).join("\u001f");
      if (sig === last) stable += 1; else stable = 0;
      if (stable >= 2) return true;
      last = sig; await wait(200);
    }
    return false;
  }
  async function enumerateVisible(doc = root.document, opts = {}) {
    const container = opts.container || pickerContainer(doc);
    const list = listStructure(container);
    if (!container || !list) return { items: [], complete: false, state: "selector_failure", reason: "CATEGORY_PICKER_STRUCTURE_NOT_CONFIRMED" };
    const didSettle = await settled(container);
    const all = new Map(); let passes = 0; let pageCount = 0; let complete = true; let reason = null;
    let anyScroll = false; let anyVirtualized = false; let endReached = false; let expected = null; let ambiguousDuplicate = false;
    const maxPasses = Math.max(1, Math.min(Number(opts.max_scrolls) || 500, 500));
    const maxPages = Math.max(1, Math.min(Number(opts.max_pages) || 100, 100));
    while (pageCount < maxPages) {
      pageCount += 1;
      const currentList = listStructure(container);
      const initialNodes = optionElements(currentList);
      const scroll = scrollContainer(currentList, initialNodes); anyScroll ||= Boolean(scroll);
      let stableBottom = 0; let lastSig = ""; const pageSeen = new Set();
      while (passes < maxPasses) {
        checkStopped(); passes += 1;
        const nodes = optionElements(currentList);
        const labelsOnScreen = new Map();
        const repeatedLabels = new Map();
        nodes.forEach((el) => {
          const normalized = label(el).toLocaleLowerCase();
          repeatedLabels.set(normalized, (repeatedLabels.get(normalized) || 0) + 1);
        });
        nodes.forEach((el, i) => {
          const row = optionIdentity(el, i);
          if (!row.label || LOADING.test(row.label) || PAGE.test(row.label)) return;
          const stableIdentity = row.category_id || el.getAttribute("aria-posinset");
          if (!stableIdentity && repeatedLabels.get(row.label.toLocaleLowerCase()) > 1) {
            row.identity_key = `label:${row.label.toLocaleLowerCase()}:duplicate-visible-${i}`;
            ambiguousDuplicate = true;
          }
          const normalizedLabel = row.label.toLocaleLowerCase();
          labelsOnScreen.set(normalizedLabel, (labelsOnScreen.get(normalizedLabel) || 0) + 1);
          const key = `${row.identity_key}\u001f${row.label}`;
          if (!all.has(key)) all.set(key, row);
          pageSeen.add(`${row.identity_key}\u001f${row.label}`);
        });
        if (Array.from(labelsOnScreen.values()).some((count) => count > 1)
            && nodes.some((el) => !el.getAttribute("data-category-id") && !el.getAttribute("data-id") && !el.getAttribute("data-key") && !el.getAttribute("value"))) ambiguousDuplicate = true;
        const sig = nodes.map((el, i) => `${el.getAttribute("data-category-id") || el.id || i}:${label(el)}`).join("\u001f");
        const atBottom = !scroll || scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 2;
        if (atBottom && sig === lastSig && !loadingEvidence(currentList)) stableBottom += 1; else stableBottom = 0;
        if (!scroll || stableBottom >= 2) { endReached = atBottom && (!scroll || stableBottom >= 2); break; }
        lastSig = sig;
        const oldTop = scroll.scrollTop; scroll.scrollTop = Math.min(scroll.scrollHeight, oldTop + Math.max(80, scroll.clientHeight * 0.8));
        await wait(220); await settled(container);
        if (scroll.scrollTop === oldTop && atBottom) stableBottom += 1;
      }
      anyVirtualized ||= Boolean(currentList && (currentList.querySelectorAll("[aria-posinset]").length || currentList.getAttribute("aria-setsize")));
      const countValues = [currentList?.getAttribute("aria-setsize"), currentList?.getAttribute("data-total-count"), container.getAttribute("data-total-count")]
        .map((v) => Number(v)).filter(Number.isFinite);
      const declared = countValues.length ? Math.max(...countValues) : null;
      if (declared != null) expected = expected == null ? declared : Math.max(expected, declared);
      const next = Array.from(container.querySelectorAll("button[aria-label*='next page' i],[role='button'][aria-label*='next page' i],[data-pagination='next']"))
        .filter(visible).find((el) => !el.disabled && el.getAttribute("aria-disabled") !== "true");
      if (passes >= maxPasses) { complete = false; reason = "SCROLL_LIMIT_REACHED"; break; }
      if (!didSettle || !endReached || loadingEvidence(currentList)) {
        complete = false; reason = loadingEvidence(currentList) ? "CATEGORY_OPTIONS_STILL_LOADING"
          : !didSettle ? "CATEGORY_OPTIONS_DID_NOT_SETTLE" : "ENUMERATION_END_NOT_PROVEN"; break;
      }
      if (!next) break;
      const before = optionElements(currentList).map((el) => `${el.getAttribute("data-category-id") || ""}:${label(el)}`).join("\u001f");
      next.click(); await wait(250);
      if (!await settled(container)) { complete = false; reason = "PAGINATION_DID_NOT_SETTLE"; break; }
      const afterList = listStructure(container);
      const after = optionElements(afterList).map((el) => `${el.getAttribute("data-category-id") || ""}:${label(el)}`).join("\u001f");
      if (after === before) { complete = false; reason = "PAGINATION_DID_NOT_ADVANCE"; break; }
      if (pageCount >= maxPages) { complete = false; reason = "PAGE_LIMIT_REACHED"; }
    }
    const remainingNext = Array.from(container.querySelectorAll("button[aria-label*='next page' i],[role='button'][aria-label*='next page' i],[data-pagination='next']"))
      .some((el) => visible(el) && !el.disabled && el.getAttribute("aria-disabled") !== "true");
    const loading = loadingEvidence(list);
    complete = complete && didSettle && endReached && !loading && !remainingNext && pageCount < maxPages
          && (expected == null || all.size >= expected) && !ambiguousDuplicate;
    if (!complete && !reason) reason = ambiguousDuplicate ? "DUPLICATE_LABELS_WITHOUT_STABLE_IDENTITIES"
      : expected != null && all.size < expected ? "OBSERVED_COUNT_BELOW_DECLARED_COUNT"
        : remainingNext ? "PAGINATION_REMAINS" : "ENUMERATION_END_NOT_PROVEN";
    return { items: Array.from(all.values()), complete, state: complete ? "settled" : remainingNext ? "pagination_remaining" : loading ? "loading" : "incomplete",
      reason: complete ? null : reason,
      evidence: { captured_at: new Date().toISOString(), option_count: all.size, declared_count: expected, scroll_passes: passes, pages: pageCount,
        scroll_container_found: anyScroll, virtualized: anyVirtualized, end_reached: endReached, loading,
        pagination_control: remainingNext, list_role: listStructure(container)?.getAttribute("role"), container_role: container.getAttribute("role") || null,
        picker_association: inferredContainerEvidence.get(container) || { confirmation: "semantic_control_relationship", observed_category_row_count: all.size } } };
  }
  function blankCreateFormEvidence() {
    const url = new URL(root.location.href);
    if (url.origin !== "https://www.facebook.com" || !/^\/marketplace\/create(?:\/|$)/i.test(url.pathname)) {
      return { ok: false, blank: false, form_ready: false, reason: "FACEBOOK_CREATE_URL_REQUIRED", populated_fields: [], inspected_fields: [], photo_preview_count: 0 };
    }
    // The Facebook create page and app shell share one document. Only inspect
    // semantically identified listing fields; global search, saved locality,
    // account defaults, and unrelated comboboxes are not listing content.
    const controls = Array.from(root.document.querySelectorAll("input:not([type=file]),textarea,[contenteditable='true'],[role='combobox']"))
      .filter(visible);
    const inspected = [];
    const populated = [];
    const metadata = /search|location|zip|postal|seller information|seller account|country|region|city|state|shipping|delivery method/i;
    const listingField = /title|item name|name of item|description|price|brand|size|color|material|quantity|weight|condition|category|model|style|product type|item specifics/i;
    const placeholderValue = /^(enter title|title|price|description|condition|brand|size|details|category|select category|choose category|add details|search)$/i;
    for (const el of controls) {
      const fieldLabel = norm([el.getAttribute?.("aria-label"), el.getAttribute?.("placeholder"), el.getAttribute?.("name"), el.id,
        el.labels?.[0]?.innerText, el.labels?.[0]?.textContent].filter(Boolean).join(" ")).slice(0, 100);
      if (metadata.test(fieldLabel) || !listingField.test(fieldLabel)) continue;
      const rawValue = norm(el.value || el.innerText || el.textContent);
      const valuePresent = Boolean(rawValue && !placeholderValue.test(rawValue));
      const evidence = { field: fieldLabel || "unlabelled listing control", tag: String(el.tagName || "").toLowerCase(),
        type: String(el.type || "").toLowerCase() || null, value_present: valuePresent,
        placeholder_present: Boolean(el.getAttribute?.("placeholder")), control_visible: true };
      inspected.push(evidence);
      // Facebook preselects New on a blank CREATE page. That default does not
      // represent operator-entered product content.
      const defaultCondition = /condition/i.test(fieldLabel) && /^(new|used|good|fair|poor)$/i.test(rawValue);
      if (valuePresent && !defaultCondition) populated.push(evidence);
    }
    // Facebook's create page contains unrelated navigation imagery. A photo
    // preview is established only by the seller-form remove control or an
    // explicit preview marker, never by an image URL containing "marketplace".
    const photoPreviews = Array.from(root.document.querySelectorAll("button[aria-label*='remove photo' i],[data-testid*='photo-preview' i],[aria-label*='photo preview' i]"))
      .filter(visible).length;
    const selectedFiles = Array.from(root.document.querySelectorAll("input[type=file]"))
      .filter(visible).reduce((count, el) => count + Number(el.files?.length || 0), 0);
    const category = categoryControl();
    const formReady = Boolean(category && controls.some((el) => /title|item name|price|description/i.test(
      norm([el.getAttribute?.("aria-label"), el.getAttribute?.("placeholder"), el.getAttribute?.("name"), el.id,
        el.labels?.[0]?.innerText, el.labels?.[0]?.textContent].filter(Boolean).join(" ")))));
    return { ok: true, blank: formReady && populated.length === 0 && photoPreviews === 0 && selectedFiles === 0,
      form_ready: formReady, reason: !formReady ? "FACEBOOK_CREATE_FORM_NOT_HYDRATED"
        : populated.length || photoPreviews || selectedFiles ? "LISTING_CONTENT_PRESENT" : null,
      category_control_found: Boolean(category), inspected_fields: inspected.slice(0, 24), populated_fields: populated.slice(0, 12),
      photo_preview_count: photoPreviews, selected_file_count: selectedFiles };
  }
  function blankCreateForm() { return blankCreateFormEvidence().blank; }
  async function openPicker() {
    const control = categoryControl();
    if (!control) throw Object.assign(new Error("FACEBOOK_CATEGORY_CONTROL_NOT_FOUND"), { code: "FACEBOOK_CATEGORY_CONTROL_NOT_FOUND" });
    const alreadyOpen = pickerContainer();
    if (alreadyOpen) return control;
    pendingPortalEvidence = { doc: root.document, before: new Set(categoryOptionCandidates()) };
    inferredPortal = null;
    control.click();
    const opened = await waitForCategoryPicker();
    if (!opened.picker) {
      const before = pendingPortalEvidence?.before || new Set();
      pendingPortalEvidence = null;
      const after = categoryOptionCandidates();
      const newRows = after.filter((node) => !before.has(node));
      throw Object.assign(new Error("FACEBOOK_CATEGORY_PICKER_NOT_CONFIRMED"), { code: "FACEBOOK_CATEGORY_PICKER_NOT_CONFIRMED",
        evidence: { category_control: { tag: control.tagName?.toLowerCase() || null, role: control.getAttribute?.("role") || null,
          label: label(control).slice(0, 120), aria_expanded: control.getAttribute?.("aria-expanded") || null,
          aria_controls: control.getAttribute?.("aria-controls") || null }, wait_ms: opened.wait_ms,
          visible_option_count_after_click: after.length,
          new_option_rows: newRows.slice(0, 20).map((node) => ({ label: label(node).slice(0, 120), tag: node.tagName?.toLowerCase() || null,
            role: node.getAttribute?.("role") || null, data_testid: node.getAttribute?.("data-testid") || null })) } });
    }
    return control;
  }
  async function returnToRoot() {
    await openPicker();
    for (let i = 0; i < 16; i += 1) {
      const container = pickerContainer();
      const back = Array.from(container.querySelectorAll("button,[role='button'],[role='treeitem']"))
        .filter(visible).find((el) => /^(back|go back)$/i.test(label(el)));
      if (!back) return enumerateVisible().then((result) => result.items);
      back.click(); await wait(250);
    }
    throw Object.assign(new Error("FACEBOOK_CATEGORY_BACKTRACK_LIMIT"), { code: "FACEBOOK_CATEGORY_BACKTRACK_LIMIT" });
  }
  async function enterPath(path, identities = []) {
    await openPicker(); await returnToRoot();
    for (const part of path) {
      const result = await enumerateVisible();
      if (!result.complete) throw Object.assign(new Error(result.reason || "FACEBOOK_CATEGORY_ENUMERATION_INCOMPLETE"), { code: "FACEBOOK_CATEGORY_ENUMERATION_INCOMPLETE" });
      const wanted = identities.shift();
      const match = result.items.find((row) => row.label.toLocaleLowerCase() === String(part).toLocaleLowerCase()
        && (!wanted || wanted === `id:${row.category_id}` || wanted === row.identity_key));
      if (!match) throw Object.assign(new Error("FACEBOOK_CATEGORY_PATH_NOT_FOUND:" + String(part).slice(0, 60)), { code: "FACEBOOK_CATEGORY_PATH_NOT_FOUND" });
      match.el.click(); await wait(350);
    }
  }
  function schemaSnapshot() {
    const category = categoryControl();
    const form = category?.closest?.("form,[role='dialog']") || root.document.querySelector("form") || root.document;
    const controls = Array.from(form.querySelectorAll("input,textarea,select,[contenteditable='true'],[role='combobox'],[role='checkbox'],[role='radio']")).filter(visible);
    const metadataLabel = /search|location|zip|postal|seller account|seller information|country|region|city|state|cookie|password|email|phone/i;
    const sellerFieldLabel = /title|item name|description|price|brand|size|color|material|quantity|weight|condition|category|model|style|product type|shipping|delivery method|item specifics/i;
    const fields = controls.map((el) => {
      const name = norm((el.labels && el.labels[0] && (el.labels[0].innerText || el.labels[0].textContent)) || el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.getAttribute("name") || el.id);
      if (!sellerFieldLabel.test(name) || metadataLabel.test(name)) return null;
      const required = el.required === true || String(el.getAttribute("aria-required") || "").toLowerCase() === "true" || /\*$/.test(name);
      const allowed = el.tagName === "SELECT" ? Array.from(el.options || []).map((opt) => norm(opt.label || opt.textContent)).filter(Boolean) : [];
      return { name: name.replace(/\s*\*\s*$/, "").slice(0, 120), native_id: el.id || el.getAttribute("name") || null,
        required, conditional: false, input_type: el.getAttribute("type") || el.getAttribute("role") || el.tagName.toLowerCase(),
        allowed_values: allowed, constraints: { min: el.getAttribute("min"), max: el.getAttribute("max"), pattern: el.getAttribute("pattern"),
          max_length: el.getAttribute("maxlength"), required_attribute: el.required === true, aria_required: el.getAttribute("aria-required") } };
    }).filter((field) => field?.name);
    return { fields, complete: false, evidence: { captured_at: new Date().toISOString(), source: "facebook_authenticated_seller_form_dom",
      visible_control_count: controls.length, required_indicators_observed: fields.some((field) => field.required),
      inspected_conditions: [], uninspected_conditions: ["progressive_form_steps", "category_dependent_fields", "shipping_configuration", "conditional_values"],
      complete: false, incomplete: true, completeness_reason: "VISIBLE_FIELDS_ONLY; progressive and conditional seller-form requirements have not been exhaustively inspected",
      submission_performed: false } };
  }
  async function runTestCommand(command, parameters = {}) {
    const params = parameters && typeof parameters === "object" && !Array.isArray(parameters) ? parameters : {};
    const allowedKeys = new Set(["path", "identities", "label", "category_id", "max_scrolls", "max_nodes"]);
    if (Object.keys(params).some((key) => !allowedKeys.has(key))) throw Object.assign(new Error("TAXONOMY_TEST_PARAMETER_NOT_ALLOWED"), { code: "TAXONOMY_TEST_PARAMETER_NOT_ALLOWED" });
    const path = Array.isArray(params.path) ? params.path.slice(0, 12).map((item) => String(item).slice(0, 120)) : [];
    const identities = Array.isArray(params.identities) ? params.identities.slice(0, 12).map((item) => String(item).slice(0, 120)) : [];
    const safeRows = (rows) => (rows || []).map(({ el, ...row }) => row);
    const identity = root.__POSTERPRO_CONTENT_SCRIPT_IDENTITY || {};
    const base = { command, extension_version: identity.content_script_version || null,
      content_script_build_id: identity.content_script_build_id || null,
      taxonomy_collector_build_id: identity.taxonomy_collector_build_id || null,
      adapter_build_id: "posterpro-0.3.103-facebook-taxonomy-window-tab-discovery", tab_url_path: root.location.pathname.slice(0, 180),
      document_state: root.document.readyState, submission_performed: false };
    switch (command) {
      case "INSPECT_FORM": {
        const blank = blankCreateFormEvidence();
        return { ...base, ok: true, form_ready: blank.form_ready, blank: blank.blank,
          blank_form_evidence: blank, fields: schemaSnapshot().fields, read_only: true };
      }
      case "INSPECT_CATEGORY_PICKER":
      case "ENUMERATE_VISIBLE_OPTIONS":
      case "SCROLL_CATEGORY_LIST": {
        const result = await enumerateVisible(root.document, { max_scrolls: Math.max(1, Math.min(Number(params.max_scrolls) || 20, 20)), max_pages: 10 });
        return { ...base, ok: result.complete || result.items.length > 0, picker_found: Boolean(pickerContainer()),
          items: safeRows(result.items), complete: result.complete, state: result.state, reason_code: result.reason,
          selector_evidence: result.evidence, current_path: path, read_only: true };
      }
      case "OPEN_CATEGORY_PICKER": {
        await openPicker();
        const result = await enumerateVisible(root.document, { max_scrolls: Math.max(1, Math.min(Number(params.max_scrolls) || 20, 20)), max_pages: 10 });
        return { ...base, ok: result.complete, picker_open: true, items: safeRows(result.items), complete: result.complete,
          state: result.state, reason_code: result.reason, selector_evidence: result.evidence, read_only: true };
      }
      case "SELECT_CATEGORY_OPTION": {
        if (!blankCreateForm()) throw Object.assign(new Error("CATEGORY_SELECTION_REQUIRES_BLANK_CREATE_FORM"), { code: "CATEGORY_SELECTION_REQUIRES_BLANK_CREATE_FORM" });
        const label = String(params.label || "").slice(0, 160);
        if (!label) throw Object.assign(new Error("TAXONOMY_TEST_CATEGORY_LABEL_REQUIRED"), { code: "TAXONOMY_TEST_CATEGORY_LABEL_REQUIRED" });
        const categoryId = params.category_id ? String(params.category_id).slice(0, 160) : null;
        const identityPath = [...identities, categoryId ? `id:${categoryId}` : `label:${label}`];
        const selection = await adapter.select({ label, category_id: categoryId }, [...path, label], identityPath);
        return { ...base, ok: true, path: [...path, label], category_id: selection.category_id || null,
          terminal: selection.terminal === true, children: selection.children || [], children_complete: selection.children_complete === true,
          selector_evidence: selection.selector_evidence, read_only: false };
      }
      case "BACKTRACK_CATEGORY": {
        if (!blankCreateForm()) throw Object.assign(new Error("CATEGORY_BACKTRACK_REQUIRES_BLANK_CREATE_FORM"), { code: "CATEGORY_BACKTRACK_REQUIRES_BLANK_CREATE_FORM" });
        await adapter.backtrack(path, identities);
        return { ...base, ok: true, current_path: path, picker_open: true, read_only: false };
      }
      case "INSPECT_CATEGORY_FIELDS": {
        const snapshot = schemaSnapshot();
        return { ...base, ok: true, fields: snapshot.fields, complete: snapshot.complete,
          evidence: snapshot.evidence, read_only: true };
      }
      case "GET_COLLECTOR_STATE":
        return { ...base, ok: true, collector_running: root.__POSTERPRO_TAXONOMY_COLLECTOR_RUNNING === true,
          collector_stage: root.__POSTERPRO_TAXONOMY_COLLECTOR_STAGE || null, read_only: true };
      case "RUN_COLLECTION_STEP": {
        if (!blankCreateForm()) throw Object.assign(new Error("COLLECTION_REQUIRES_BLANK_FACEBOOK_CREATE_FORM"), { code: "COLLECTION_REQUIRES_BLANK_FACEBOOK_CREATE_FORM" });
        const maxNodes = Math.max(1, Math.min(Number(params.max_nodes) || 1, 5));
        const result = await root.__POSTERPRO_RUN_FACEBOOK_TAXONOMY_STEP?.(maxNodes);
        if (!result) throw Object.assign(new Error("FACEBOOK_TAXONOMY_COLLECTOR_UNAVAILABLE"), { code: "FACEBOOK_TAXONOMY_COLLECTOR_UNAVAILABLE" });
        return { ...base, ...result, ok: true, max_nodes: maxNodes, read_only: false };
      }
      case "STOP_COLLECTION":
        root.__POSTERPRO_TAXONOMY_STOP_REQUESTED = true;
        return { ...base, ok: true, stop_requested: true, read_only: false };
      default: throw Object.assign(new Error("TAXONOMY_TEST_COMMAND_NOT_ALLOWED"), { code: "TAXONOMY_TEST_COMMAND_NOT_ALLOWED" });
    }
  }
  const adapter = {
    isLive: true,
    async readChildren(parentPath, identities = []) {
      checkStopped(); await enterPath(parentPath, identities.slice());
      const result = await enumerateVisible();
      if (!result.items.length && !result.complete) return { items: [], complete: false, reason: result.reason || "EMPTY_WITHOUT_AFFIRMATIVE_END_EVIDENCE", selector_evidence: result.evidence };
      const evidence = { ...(result.evidence || {}), end_reached: result.evidence?.end_reached === true,
        loading: result.evidence?.loading === false, pagination_control: result.evidence?.pagination_control === false };
      return { items: result.items.map(({ el, ...row }) => row), complete: result.complete,
        reason: result.reason, selector_evidence: result.evidence, completeness_evidence: result.complete ? evidence : null };
    },
    async select(child, path, identityPath = []) {
      checkStopped(); await enterPath(path.slice(0, -1), identityPath.slice(0, -1));
      const result = await enumerateVisible();
      const match = result.items.find((row) => row.label.toLocaleLowerCase() === String(child.label).toLocaleLowerCase()
        && (!child.category_id || row.category_id === child.category_id));
      if (!match) throw Object.assign(new Error("FACEBOOK_CATEGORY_OPTION_DISAPPEARED_OR_AMBIGUOUS"), { code: "FACEBOOK_CATEGORY_OPTION_DISAPPEARED_OR_AMBIGUOUS" });
      match.el.click(); await wait(300);
      const control = categoryControl();
      const picker = pickerContainer();
      const selectedText = norm(control?.getAttribute("data-value") || control?.getAttribute("aria-valuetext") || control?.getAttribute("aria-label") || control?.textContent);
      const selectedId = control?.getAttribute("data-category-id") || control?.getAttribute("data-value") || null;
      const persisted = Boolean(selectedId && selectedId === match.category_id || selectedText.toLowerCase().includes(String(child.label).toLowerCase()));
      const stable = Boolean(picker && await settled(picker));
      const descendants = picker ? await enumerateVisible(root.document, { container: picker }) : { items: [], complete: false, state: "selector_failure" };
      const terminal = persisted && stable && descendants.complete && descendants.items.length === 0;
      const evidence = { captured_at: new Date().toISOString(), selected_label: child.label, category_id: match.category_id,
        selected_value: selectedText, selected_native_id: selectedId, selection_persisted: persisted, picker_settled: stable,
        child_enumeration_state: descendants.state, child_enumeration_complete: descendants.complete,
        child_options: descendants.items.map(({ el, ...row }) => row), child_enumeration_evidence: descendants.evidence || null,
        page_url: root.location.origin + root.location.pathname, submission_performed: false };
      return { category_id: match.category_id, terminal, terminal_evidence: terminal ? { proof: "persisted selected identity and settled exhaustive picker showed zero children", ...evidence } : null,
        children: descendants.items.map(({ el, ...row }) => row), children_complete: descendants.complete,
        selector_evidence: evidence };
    },
    async captureRequiredFields(_child, path) { const snapshot = schemaSnapshot(); snapshot.evidence.category_path = path; return snapshot; },
    async backtrack(parentPath, identities = []) { await enterPath(parentPath, identities.slice()); },
  };
  root.PosterProFacebookTaxonomyAdapter = { adapter, blankCreateForm, blankCreateFormEvidence, schemaSnapshot,
    categoryControl, openPicker, pickerContainer, enumerateVisible, optionElements, runTestCommand };
})(globalThis);
