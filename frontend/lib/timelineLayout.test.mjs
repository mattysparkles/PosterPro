import assert from "node:assert/strict";
import { clampTimelineZoom, DEFAULT_TIMELINE_ZOOM, TIMELINE_THUMBNAIL_WIDTHS, timelineThumbnailWidth } from "./timelineLayout.mjs";

assert.equal(DEFAULT_TIMELINE_ZOOM, 1);
assert.ok(TIMELINE_THUMBNAIL_WIDTHS[DEFAULT_TIMELINE_ZOOM] >= 160);
assert.equal(clampTimelineZoom(-10), 1);
assert.equal(clampTimelineZoom(999), TIMELINE_THUMBNAIL_WIDTHS.length - 1);
assert.equal(timelineThumbnailWidth("not-a-number"), 160);
assert.equal(timelineThumbnailWidth(4), 300);
