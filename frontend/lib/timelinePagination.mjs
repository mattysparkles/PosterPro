export async function loadTimelineWindow(fetchPage, requestedCount, pageSize = 500) {
  const target = Math.max(0, Number(requestedCount) || 0);
  const chunkSize = Math.max(1, Math.min(1000, Number(pageSize) || 500));
  const items = [];
  let latest = { total: 0, photo_count: 0, slate_count: 0 };

  while (items.length < target) {
    const limit = Math.min(chunkSize, target - items.length);
    const page = await fetchPage({ limit, offset: items.length });
    latest = page || latest;
    const rows = Array.isArray(page?.items) ? page.items : [];
    items.push(...rows);
    if (!rows.length || rows.length < limit || items.length >= Number(page?.total ?? Infinity)) break;
  }

  return {
    items,
    total: Number(latest?.total || 0),
    photo_count: Number(latest?.photo_count || 0),
    slate_count: Number(latest?.slate_count || 0),
  };
}
