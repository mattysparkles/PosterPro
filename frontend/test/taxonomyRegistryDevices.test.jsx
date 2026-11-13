import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
    fetchMarketplaceTaxonomyRegistryStatus: vi.fn(),
    searchMarketplaceTaxonomyRegistry: vi.fn(),
  refreshOfficialEbayTaxonomy: vi.fn(),
  synchronizeMarketplaceTaxonomyRegistry: vi.fn(),
  fetchInventoryTaxonomyCohorts: vi.fn(),
  fetchInventoryCohortTaxonomyCandidates: vi.fn(),
  fetchMarketplaceExtensionDevices: vi.fn(),
  createTaxonomyTestSession: vi.fn(),
  fetchTaxonomyTestSessions: vi.fn(),
  cancelTaxonomyTestSession: vi.fn(),
  createTaxonomyTestJob: vi.fn(),
  fetchTaxonomyTestJobs: vi.fn(),
  cancelTaxonomyTestJob: vi.fn(),
}));

vi.mock('../lib/api', () => api);
vi.mock('../components/layout/AppShell', () => ({ default: ({ children }) => <main>{children}</main> }));
vi.mock('../components/ui/page-header', () => ({ default: ({ title }) => <h1>{title}</h1> }));
vi.mock('../components/ui/button', () => ({ default: ({ children, ...props }) => <button {...props}>{children}</button> }));

import TaxonomyRegistryPage from '../pages/admin/taxonomy-registry';

describe('Taxonomy Registry paired device authorization', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.fetchMarketplaceTaxonomyRegistryStatus.mockResolvedValue({ marketplaces: [] });
    api.searchMarketplaceTaxonomyRegistry.mockResolvedValue({ items: [] });
    api.fetchMarketplaceExtensionDevices.mockResolvedValue({
      current_version: '0.3.103',
      devices: [{ id: 111, user_id: 2, name: 'Paired Chrome', extension_version: '0.3.103', revoked: false,
        last_seen_at: new Date(Date.now() - 5000).toISOString() }],
    });
    api.fetchTaxonomyTestSessions.mockResolvedValue({ items: [], latest_expired: { device_id: 111, expires_at: '2026-11-12T06:14:01Z' } });
    api.fetchTaxonomyTestJobs.mockResolvedValue({ items: [] });
    api.createTaxonomyTestSession.mockResolvedValue({ session_id: 'new-session', device_id: 111, expires_at: new Date(Date.now() + 1800000).toISOString() });
  });

  it('shows the existing paired device and requires consent before authorizing it', async () => {
    render(<TaxonomyRegistryPage />);
    const select = await screen.findByRole('combobox', { name: 'Taxonomy test browser' });
    expect(await screen.findByRole('option', { name: /Paired Chrome · 0\.3\.103 · #111/ })).toBeTruthy();
    expect(select.value).toBe('111');
    expect(screen.getByText(/Previous taxonomy authorization expired/)).toBeTruthy();

    const authorize = screen.getByRole('button', { name: 'Authorize test session' });
    expect(authorize.disabled).toBe(true);
    fireEvent.click(screen.getByRole('checkbox'));
    expect(authorize.disabled).toBe(false);
    fireEvent.click(authorize);

    await waitFor(() => expect(api.createTaxonomyTestSession).toHaveBeenCalledWith({
      device_id: 111, operator_opt_in: true, expires_in_seconds: 1800,
    }));
    expect(await screen.findByText(/Session capability: new-session/)).toBeTruthy();
  });

  it('keeps an online paired device visible but disables authorization when its extension version is incompatible', async () => {
    api.fetchMarketplaceExtensionDevices.mockResolvedValueOnce({ current_version: '0.3.104', devices: [
      { id: 111, user_id: 2, name: 'Paired Chrome', extension_version: '0.3.103', revoked: false,
        last_seen_at: new Date(Date.now() - 5000).toISOString() },
    ] });
    render(<TaxonomyRegistryPage />);
    const option = await screen.findByRole('option', { name: /#111 · needs extension 0\.3\.104/ });
    expect(option.disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Authorize test session' }).disabled).toBe(true);
  });
});
