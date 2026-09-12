import test from "node:test";
import assert from "node:assert/strict";
import { slatePreviewDataUrl } from "./timelinePreview.mjs";

test("missing manual Slate artwork still has a visible identity-preserving preview", () => {
  const preview = decodeURIComponent(slatePreviewDataUrl({ timeline_role: "HEAD", slate: { item_id: "ITEM-7", box_id: "BOX-2", location: "Shelf A" } }).split(",", 2)[1]);
  assert.match(preview, /HEAD SLATE/);
  assert.match(preview, /ITEM-7/);
  assert.match(preview, /BOX-2/);
  assert.match(preview, /Shelf A/);
  assert.match(preview, /#39ff14/);
});

test("Tail Slate fallback is unmistakably fuchsia and escapes stored text", () => {
  const preview = decodeURIComponent(slatePreviewDataUrl({ timeline_role: "TAIL", slate: { title: "<unsafe>" } }).split(",", 2)[1]);
  assert.match(preview, /TAIL SLATE/);
  assert.match(preview, /#ff00d4/);
  assert.match(preview, /&lt;unsafe&gt;/);
});
