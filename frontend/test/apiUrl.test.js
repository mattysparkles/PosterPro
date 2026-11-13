import { describe, expect, it, vi } from 'vitest';
import { fetchMarketplaceExtensionDevices, resolveApiUrl } from '../lib/api';

describe('same-origin API URL resolution', () => {
  it('resolves the production /api path against the current site origin', () => {
    expect(resolveApiUrl('/api/browser-extension/devices', 'https://posterpro.sparkleserver.site').toString())
      .toBe('https://posterpro.sparkleserver.site/api/browser-extension/devices');
  });

  it('requests paired devices with credentials using a valid same-origin URL', async () => {
    const response = { devices: [{ id: 111 }], current_version: '0.3.103' };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: { get: () => 'application/json' },
      json: async () => response,
    });
    vi.stubGlobal('fetch', fetchMock);
    try {
      await expect(fetchMarketplaceExtensionDevices()).resolves.toEqual(response);
      expect(fetchMock.mock.calls[0][0]).toBe(`${window.location.origin}/api/browser-extension/devices`);
      expect(fetchMock.mock.calls[0][1].credentials).toBe('include');
    } finally { vi.unstubAllGlobals(); }
  });
});
