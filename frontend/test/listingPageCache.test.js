import { beforeEach, describe, expect, it } from "vitest";
import { clearListingPageCache, readListingPageCache, writeListingPageCache } from "../lib/listingPageCache.mjs";

describe("listing page cache", () => {
  beforeEach(() => clearListingPageCache());

  it("returns a recently loaded page immediately for its exact query key", () => {
    const page = { items: [{ id: 42 }], pagination: { page: 3 }, bucketCounts: { review: 6 } };
    writeListingPageCache("user:2|page:3|review", page, 1000);
    expect(readListingPageCache("user:2|page:3|review", 2000)).toEqual(page);
    expect(readListingPageCache("user:2|page:2|review", 2000)).toBeNull();
  });

  it("expires cached data so every return is still revalidated against the server", () => {
    writeListingPageCache("catalog", { items: [{ id: 42 }] }, 1000);
    expect(readListingPageCache("catalog", 90_999)).not.toBeNull();
    expect(readListingPageCache("catalog", 91_001)).toBeNull();
  });
});
