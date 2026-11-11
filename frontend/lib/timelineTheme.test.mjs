import test from "node:test";
import assert from "node:assert/strict";
import { canonicalGroupTone, groupPalette, slatePalette } from "./timelineTheme.mjs";

test("canonical group sequence alternates dark and light independently of slate numbers", () => {
  assert.deepEqual([1, 2, 3, 4].map(canonicalGroupTone), ["dark", "light", "dark", "light"]);
  assert.equal(canonicalGroupTone(0), "light");
});

test("alternating groups use professional high-contrast neutral surfaces", () => {
  const palette = groupPalette("dark");
  assert.equal(palette.color, "#0f172a");
  assert.equal(palette.controlColor, "#0f172a");
  assert.equal(palette.controlBackground, "#ffffff");
  assert.equal(palette.controlBorder, "#cbd5e1");
  assert.notEqual(palette.backgroundColor, groupPalette("light").backgroundColor);
});

test("head and tail slate accents are distinct, calm, and readable", () => {
  assert.equal(slatePalette("HEAD").backgroundColor, "#eff6ff");
  assert.equal(slatePalette("TAIL").backgroundColor, "#fdf2f8");
  assert.notEqual(slatePalette("HEAD").borderColor, slatePalette("TAIL").borderColor);
});
