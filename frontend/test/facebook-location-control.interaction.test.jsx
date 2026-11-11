import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import FacebookMarketplaceLocationControl from '../components/settings/FacebookMarketplaceLocationControl';

describe('Facebook Marketplace location control', () => {
  it('renders the editable location and invokes save with the visible value', () => {
    const onChange = vi.fn();
    const onSave = vi.fn();
    const { rerender } = render(<FacebookMarketplaceLocationControl value="" onChange={onChange} onSave={onSave} />);

    expect(screen.getByLabelText('Default Marketplace location')).toHaveAttribute(
      'placeholder', 'Enter the location Facebook should use for listings',
    );
    expect(screen.getByText('No location saved yet. PosterPro will not guess a ZIP code or city.')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Default Marketplace location'), { target: { value: 'Portland, Maine' } });
    expect(onChange).toHaveBeenCalledWith('Portland, Maine');
    rerender(<FacebookMarketplaceLocationControl value="Portland, Maine" onChange={onChange} onSave={onSave} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save location' }));
    expect(onSave).toHaveBeenCalledOnce();
  });

  it('shows the loaded value and prevents saving while empty or saving', () => {
    const onSave = vi.fn();
    const { rerender } = render(<FacebookMarketplaceLocationControl value="" onSave={onSave} />);
    expect(screen.getByRole('button', { name: 'Save location' })).toBeDisabled();

    rerender(<FacebookMarketplaceLocationControl value="Portland, Maine" saving onSave={onSave} />);
    expect(screen.getByLabelText('Default Marketplace location')).toHaveValue('Portland, Maine');
    expect(screen.getByText('Saved location is loaded from Marketplace Setup.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled();
  });
});
