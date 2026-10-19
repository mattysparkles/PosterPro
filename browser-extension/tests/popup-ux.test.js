import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../popup.html", import.meta.url), "utf8");
const script = readFileSync(new URL("../popup.js", import.meta.url), "utf8");

test("routine extension popup does not expose job claiming or submission controls", () => {
  assert.doesNotMatch(html, /pollButton|completeButton|externalListingId|externalUrl/);
  assert.match(html, /Review job in PosterPro/);
  assert.match(script, /posterproActiveJob/);
  assert.match(script, /openPosterPro\(`/);
});

test("pairing code remains a fallback and pause/resume is local control", () => {
  assert.match(html, /One-time pairing code/);
  assert.match(html, /Authorize this browser/);
  assert.match(script, /set_automation_paused/);
});
