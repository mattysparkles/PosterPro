function ppFindField(selectors, labelPatterns) {
  for (const selector of selectors || []) {
    const node = document.querySelector(selector);
    if (node && !node.disabled && node.getAttribute("aria-hidden") !== "true") return node;
  }
  for (const label of document.querySelectorAll("label")) {
    const text = String(label.innerText || label.getAttribute("aria-label") || "").trim().toLowerCase();
    if ((labelPatterns || []).some((pattern) => text.includes(pattern))) {
      const control = label.control || label.querySelector("input,textarea");
      if (control && !control.disabled) return control;
    }
  }
  for (const node of document.querySelectorAll("input,textarea,[contenteditable='true']")) {
    const text = `${node.getAttribute("aria-label") || ""} ${node.getAttribute("placeholder") || ""} ${node.getAttribute("name") || ""}`.toLowerCase();
    if ((labelPatterns || []).some((pattern) => text.includes(pattern)) && !node.disabled) return node;
  }
  return null;
}

function ppSetValue(node, value) {
  if (!node || value == null || value === "") return false;
  node.focus();
  if (node.isContentEditable) {
    node.textContent = String(value);
  } else {
    const prototype = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (setter) setter.call(node, String(value)); else node.value = String(value);
  }
  node.dispatchEvent(new Event("input", { bubbles: true }));
  node.dispatchEvent(new Event("change", { bubbles: true }));
  node.blur();
  return true;
}

function ppDataUrlToFile(image, index) {
  const parts = String(image.data_url || "").match(/^data:([^;,]+);base64,(.+)$/s);
  if (!parts) return null;
  const binary = atob(parts[2]);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new File([bytes], image.file_name || `posterpro-photo-${index + 1}.jpg`, { type: parts[1] });
}

async function ppFillListing({ marketplace, payload, images }) {
  const adapters = globalThis.POSTERPRO_MARKETPLACE_ADAPTERS || {};
  const adapter = adapters[String(marketplace || "").toLowerCase()];
  if (!adapter) return { ok: false, error_code: "ADAPTER_NOT_IMPLEMENTED", error: `No field adapter is available for ${marketplace}.` };
  if (!adapter.hosts.some((host) => location.hostname.endsWith(host))) {
    return { ok: false, error_code: "UNEXPECTED_MARKETPLACE_DOMAIN", error: "The active page does not match the requested marketplace." };
  }

  const populated_fields = [];
  const missing_required_fields = [];
  const fields = [
    ["title", adapter.title, ["title"]],
    ["price", adapter.price, ["price"]],
    ["description", adapter.description, ["description", "describe"]],
  ];
  for (const [name, selectors, labels] of fields) {
    const node = ppFindField(selectors, labels);
    if (ppSetValue(node, payload[name])) populated_fields.push(name);
    else missing_required_fields.push(name);
  }

  let uploaded_image_count = 0;
  if (images?.length) {
    const input = document.querySelector(adapter.photos);
    if (input && !input.disabled) {
      const transfer = new DataTransfer();
      images.forEach((image, index) => {
        const file = ppDataUrlToFile(image, index);
        if (file) transfer.items.add(file);
      });
      if (transfer.files.length) {
        input.files = transfer.files;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
        uploaded_image_count = transfer.files.length;
        populated_fields.push("photos");
      }
    }
  }
  if (!uploaded_image_count) missing_required_fields.push("photos");

  // Category, condition, and shipping remain explicit operator-review fields
  // until a destination-specific taxonomy/selector mapping is verified.
  // Do not infer destination taxonomy/condition from an eBay category or a
  // canonical label. These values need marketplace-specific selection.
  missing_required_fields.push("category_operator_selection_required", "condition_operator_selection_required");
  if (payload.shipping || payload.delivery_method) missing_required_fields.push("shipping_operator_review_required");
  return {
    ok: true,
    stage: "AWAITING_OPERATOR_REVIEW",
    marketplace,
    page_url: location.origin + location.pathname,
    selector_strategy: "central_marketplace_map_then_semantic_labels",
    populated_fields,
    missing_required_fields,
    uploaded_image_count,
    submission_performed: false,
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.action !== "posterpro_fill_listing") return false;
  ppFillListing(message).then(sendResponse).catch((error) => sendResponse({
    ok: false,
    error_code: "FORM_FILL_FAILED",
    error: String(error?.message || error),
    page_url: location.origin + location.pathname,
  }));
  return true;
});
