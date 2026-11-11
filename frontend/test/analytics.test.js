import { afterEach, describe, expect, it, vi } from 'vitest';
import { matomoPageUrl, matomoPush, trackSignupCompleted } from '../lib/analytics';

describe('Matomo tracking helpers', () => {
  afterEach(() => { delete window._paq; });

  it('keeps useful catalog filters but strips search text and credentials from page URLs', () => {
    expect(matomoPageUrl('/listings?tab=attention&page_size=100&source=amazon_vine&q=private+search&token=secret'))
      .toBe('/listings?tab=attention&page_size=100&source=amazon_vine');
  });

  it('queues events without sending signup fields', () => {
    window._paq = [];
    expect(trackSignupCompleted()).toBe(true);
    expect(window._paq).toEqual([['trackEvent', 'Account', 'signup', 'registration_completed']]);
    expect(matomoPush(['trackEvent', 'UI', 'click', 'Save'])).toBe(true);
    expect(window._paq).toHaveLength(2);
  });
});
