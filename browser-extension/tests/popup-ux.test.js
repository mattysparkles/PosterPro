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

test("popup treats a connection error as stale when a later authenticated heartbeat succeeded", () => {
  assert.match(script, /posterproLastSuccessfulHeartbeatAt/);
  assert.match(script, /heartbeatAt > errorAt/);
  assert.match(script, /changes\.posterproAgentError \|\| changes\.posterproDevice/);
});

test("popup state response includes persistent Facebook taxonomy startup diagnostics", () => {
  const background = readFileSync(new URL("../background.js", import.meta.url), "utf8");
  assert.match(background, /chrome\.storage\.local\.get\(\[[^\]]*posterproFacebookTaxonomyCollection[^\]]*posterproFacebookTaxonomyDiagnosticHistory/);
  assert.match(script, /local\.posterproFacebookTaxonomyCollection/);
  assert.match(script, /taxonomy\.error_code/);
  assert.match(script, /taxonomy\.updated_at/);
});
