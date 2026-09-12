function escapeXml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

export function slatePreviewDataUrl(photo = {}) {
  const role = String(photo.timeline_role || photo.slate?.boundary_position || "head").toUpperCase();
  const tail = role === "TAIL";
  const accent = tail ? "#ff00d4" : "#39ff14";
  const slate = photo.slate || {};
  const rows = [
    slate.title || photo.original_filename || "Inventory Slate",
    `Item: ${slate.item_id || photo.item_id || "Unassigned"}`,
    `Box: ${slate.box_id || "Not recorded"}`,
    `Location: ${slate.location || "Not recorded"}`,
  ].map(escapeXml);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320"><rect width="320" height="320" rx="24" fill="#171717"/><rect x="8" y="8" width="304" height="304" rx="20" fill="none" stroke="${accent}" stroke-width="12"/><text x="24" y="52" fill="${accent}" font-family="Arial,sans-serif" font-size="24" font-weight="700">${tail ? "TAIL" : "HEAD"} SLATE</text>${rows.map((row, i) => `<text x="24" y="${112 + i * 42}" fill="#ffffff" font-family="Arial,sans-serif" font-size="${i === 0 ? 19 : 16}">${row}</text>`).join("")}</svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
