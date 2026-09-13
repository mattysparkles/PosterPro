import test from "node:test";
import assert from "node:assert/strict";
import { canonicalGroupTone, groupPalette, slatePalette } from "./timelineTheme.mjs";

test("canonical group sequence alternates dark and light independently of slate numbers", () => {
  assert.deepEqual([1, 2, 3, 4].map(canonicalGroupTone), ["dark", "light", "dark", "light"]);
  assert.equal(canonicalGroupTone(0), "light");
});

test("dark group controls and text use high-contrast light colors", () => {
  const palette = groupPalette("dark");
  assert.equal(palette.color, "#ffffff");
  assert.equal(palette.controlColor, "#ffffff");
  assert.equal(palette.controlBackground, "#373737");
  assert.equal(palette.controlBorder, "#a3a3a3");
  assert.notEqual(palette.backgroundColor, groupPalette("light").backgroundColor);
});

test("head and tail slate accents remain unmistakable", () => {
  assert.equal(slatePalette("HEAD").backgroundColor, "#39ff14");
  assert.equal(slatePalette("TAIL").backgroundColor, "#ff00d4");
});
