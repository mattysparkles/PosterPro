import { describe, expect, it, vi } from 'vitest';
import { dispatchBrowserLabel } from '../lib/inventoryPrintTransport';

describe('browser inventory label transport', () => {
  it('creates the configured number of native-size label pages and does not claim physical confirmation', async () => {
    let html = '';
    const print = vi.fn();
    const frameDocument = {
      open: vi.fn(),
      write: vi.fn((value) => { html = value; }),
      close: vi.fn(),
      images: [{ complete: true, naturalWidth: 464 }, { complete: true, naturalWidth: 464 }],
    };
    const frame = {
      style: {},
      contentWindow: { document: frameDocument, focus: vi.fn(), print, addEventListener: vi.fn() },
      remove: vi.fn(),
    };
    vi.spyOn(document.body, 'appendChild').mockImplementation(() => frame);
    vi.spyOn(document, 'createElement').mockImplementationOnce(() => frame);
    const result = dispatchBrowserLabel('data:image/png;base64,AA==', { copies: 2, scalePercent: 100 });
    await Promise.resolve();
    await Promise.resolve();
    expect(html.match(/<img/g)).toHaveLength(2);
    expect(html).toContain('@page{size:58mm 40mm;margin:0}');
    expect(html).toContain('break-before:page');
    expect(print).toHaveBeenCalledOnce();
    expect(result).toEqual({ status: 'dispatched', physicallyVerified: false });
  });
});
