import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../posterpro-link.js", import.meta.url), "utf8");

function harness() {
  const listeners = {};
  const outbound = [];
  const runtimeMessages = [];
  const context = {
    Date,
    location: { origin: "https://posterpro.sparkleserver.site" },
    document: { addEventListener: (type, callback) => { listeners[`document:${type}`] = callback; } },
    window: {
      postMessage: (message, origin) => outbound.push({ message, origin }),
      addEventListener: (type, callback) => { listeners[`window:${type}`] = callback; },
    },
    chrome: {
      runtime: {
        sendMessage: async (message) => {
          runtimeMessages.push(message);
          return { ok: true, device: { id: 12, name: "Chrome", user_id: 7 } };
        },
      },
    },
  };
  vm.runInNewContext(source, context);
  return { listeners, outbound, runtimeMessages, context };
}

test("PosterPro page detects the extension and one trusted authorize click pairs without exposing a device token", async () => {
  const h = harness();
  assert.equal(h.outbound[0].message.type, "PRESENCE");
  h.listeners["window:message"]({
    source: h.context.window,
    origin: h.context.location.origin,
    data: { source: "posterpro-settings", type: "PAIR_EXTENSION", pairing_code: "x".repeat(32) },
  });
  assert.equal(h.runtimeMessages.length, 0, "pairing without a trusted operator click is ignored");

  h.listeners["document:click"]({ isTrusted: true, target: { closest: () => ({}) } });
  await h.listeners["window:message"]({
    source: h.context.window,
    origin: h.context.location.origin,
    data: { source: "posterpro-settings", type: "PAIR_EXTENSION", pairing_code: "x".repeat(32) },
  });
  assert.equal(JSON.stringify(h.runtimeMessages), JSON.stringify([{ action: "pair_device", pairingCode: "x".repeat(32), deviceName: "PosterPro browser" }]));
  const authorized = h.outbound.at(-1).message;
  assert.equal(authorized.type, "AUTHORIZED");
  assert.equal(authorized.device.id, 12);
  assert.equal(JSON.stringify(h.outbound).includes("device_token"), false);
});

test("extension pairing bridge rejects foreign origins and untrusted clicks", async () => {
  const h = harness();
  h.listeners["document:click"]({ isTrusted: false, target: { closest: () => ({}) } });
  await h.listeners["window:message"]({
    source: h.context.window,
    origin: "https://evil.example",
    data: { source: "posterpro-settings", type: "PAIR_EXTENSION", pairing_code: "x".repeat(32) },
  });
  assert.equal(h.runtimeMessages.length, 0);
});

test("extension source and permissions do not read or transmit marketplace cookies", () => {
  const background = readFileSync(new URL("../background.js", import.meta.url), "utf8");
  const manifest = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));
  assert.doesNotMatch(background, /chrome\.cookies|cookies_present/);
  assert.equal(manifest.permissions.includes("cookies"), false);
});
