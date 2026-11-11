// Presentation-only contracts for the timeline.  Keeping these values in a
// small pure module makes the useful-thumbnail and bounded-layout guarantees
// easy to regression-test without touching timeline classifications.
export const TIMELINE_THUMBNAIL_WIDTHS = [120, 160, 200, 240, 300];
// The default must be inspectable on a laptop without opening every photo.
// 200px keeps the filmstrip practical while exposing labels, packaging, and
// product shape; smaller sizes remain available through the toolbar.
export const DEFAULT_TIMELINE_ZOOM = 2;

export function clampTimelineZoom(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return DEFAULT_TIMELINE_ZOOM;
  return Math.max(1, Math.min(TIMELINE_THUMBNAIL_WIDTHS.length - 1, parsed));
}

export function timelineThumbnailWidth(zoom) {
  return TIMELINE_THUMBNAIL_WIDTHS[clampTimelineZoom(zoom)];
}
