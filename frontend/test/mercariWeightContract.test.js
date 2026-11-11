import { afterEach, describe, expect, it, vi } from 'vitest';
import { startMercariInventoryDryRun } from '../lib/api';

describe('Mercari packed weight request contract', () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each(['', '  \t ', null])('omits blank weight %j instead of sending zero or an empty string', async (package_weight) => {
    const fetchMock = vi.fn().mockResolvedValue({
      headers: { get: () => 'application/json' }, ok: true, json: async () => ({ id: 1 }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await startMercariInventoryDryRun(3460, null, { package_weight, package_weight_unit: 'lb' });

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).not.toHaveProperty('package_weight');
    expect(body).toMatchObject({ package_weight_unit: 'lb' });
  });

  it('trims and preserves a positive decimal as a number', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      headers: { get: () => 'application/json' }, ok: true, json: async () => ({ id: 1 }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await startMercariInventoryDryRun(3460, null, { package_weight: ' 1.25 ', package_weight_unit: 'lb' });

    expect(JSON.parse(fetchMock.mock.calls[0][1].body).package_weight).toBe(1.25);
  });

  it.each([['0', 'oz'], ['-1', 'lb'], ['NaN', 'g'], ['2400.01', 'kg'], ['1', 'stone']])(
    'rejects invalid weight/unit input before sending a request (%s %s)', async (package_weight, package_weight_unit) => {
      const fetchMock = vi.fn();
      vi.stubGlobal('fetch', fetchMock);

      expect(() => startMercariInventoryDryRun(3460, null, { package_weight, package_weight_unit })).toThrow();
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );
});
