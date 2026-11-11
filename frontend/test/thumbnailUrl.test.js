import { describe, expect, it } from 'vitest';
import { toThumbnailImageUrl } from '../lib/api';

describe('storefront thumbnail URL contract', () => {
  it('clamps image dimensions to the public media endpoint limit', () => {
    const url = new URL(toThumbnailImageUrl('/media/amazon-vine/product-front.jpg', 480, 600), 'https://posterpro.invalid');
    expect(url.pathname).toBe('/media/thumbnail');
    expect(url.searchParams.get('path')).toBe('/media/amazon-vine/product-front.jpg');
    expect(url.searchParams.get('width')).toBe('480');
    expect(url.searchParams.get('height')).toBe('512');
  });

  it('keeps external image URLs unchanged', () => {
    expect(toThumbnailImageUrl('https://images.example.test/product.jpg', 480, 600))
      .toBe('https://images.example.test/product.jpg');
  });
});
