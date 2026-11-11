import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_TIMELINE_ZOOM, TIMELINE_THUMBNAIL_WIDTHS, clampTimelineZoom, timelineThumbnailWidth } from "./timelineLayout.mjs";

test("timeline defaults to a useful inspectable thumbnail size", () => {
  assert.equal(DEFAULT_TIMELINE_ZOOM, 2);
  assert.equal(timelineThumbnailWidth(DEFAULT_TIMELINE_ZOOM), 200);
  assert.ok(timelineThumbnailWidth(DEFAULT_TIMELINE_ZOOM) >= 180);
});

test("timeline zoom stays bounded to the non-wrapping filmstrip sizes", () => {
  assert.equal(clampTimelineZoom(-20), 1);
  assert.equal(clampTimelineZoom(999), TIMELINE_THUMBNAIL_WIDTHS.length - 1);
  assert.equal(clampTimelineZoom("bad"), DEFAULT_TIMELINE_ZOOM);
});
