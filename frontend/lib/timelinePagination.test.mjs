import test from "node:test";
import assert from "node:assert/strict";
import { loadTimelineWindow } from "./timelinePagination.mjs";

test("timeline mutation refresh reloads a previously loaded 1000-item window", async () => {
  const requests = [];
  const result = await loadTimelineWindow(async ({ limit, offset }) => {
    requests.push({ limit, offset });
    const items = Array.from({ length: Math.min(limit, 1500 - offset) }, (_, index) => ({ id: offset + index + 1 }));
    return { items, total: 1500, photo_count: 1490, slate_count: 10 };
  }, 1000, 500);

  assert.deepEqual(requests, [{ limit: 500, offset: 0 }, { limit: 500, offset: 500 }]);
  assert.equal(result.items.length, 1000);
  assert.equal(result.items[749].id, 750);
  assert.equal(result.items[999].id, 1000);
  assert.equal(result.total, 1500);
});

test("timeline refresh stops at the new end after a deletion near the loaded window", async () => {
  const requests = [];
  const result = await loadTimelineWindow(async ({ limit, offset }) => {
    requests.push({ limit, offset });
    const items = Array.from({ length: Math.min(limit, Math.max(0, 999 - offset)) }, (_, index) => ({ id: offset + index + 1 }));
    return { items, total: 999, photo_count: 990, slate_count: 9 };
  }, 1000, 500);

  assert.deepEqual(requests, [{ limit: 500, offset: 0 }, { limit: 500, offset: 500 }]);
  assert.equal(result.items.length, 999);
  assert.equal(result.items.at(-1).id, 999);
});
