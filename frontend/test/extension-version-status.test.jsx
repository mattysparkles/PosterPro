import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import ExtensionVersionStatus from '../components/marketplaces/ExtensionVersionStatus';
import { requestCurrentBrowserPairing } from '../lib/extensionPairing';

const recentDevice = (id) => ({
  id,
  browser: 'Chrome',
  extension_version: '0.3.103',
  last_seen_at: new Date().toISOString(),
  revoked: false,
  update_required: false,
});

describe('browser extension status', () => {
  it('does not infer browser identity from a matching version when its device id is unavailable', () => {
    render(<ExtensionVersionStatus
      compact
      detected
      detectedVersion="0.3.103"
      detectedPaired
      state={{ devices: [recentDevice(7)], current_version: '0.3.103', minimum_version: '0.3.100' }}
      onPair={() => {}}
    />);

    expect(screen.getByText('Extension reports an existing pairing')).toBeInTheDocument();
    expect(screen.getByText(/device identity does not match a registered device/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pair this browser' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Check pairing in Marketplace settings' })).toBeInTheDocument();
  });

  it('matches device IDs consistently when JSON provides a string ID', () => {
    render(<ExtensionVersionStatus
      compact
      detected
      detectedVersion="0.3.103"
      currentDeviceId="7"
      state={{ devices: [recentDevice(7)], current_version: '0.3.103', minimum_version: '0.3.100' }}
      onPair={() => {}}
    />);

    expect(screen.queryByText('Extension detected; browser not paired')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pair this browser' })).not.toBeInTheDocument();
  });

  it('matches the paired device by ID even when multiple account devices share its version', () => {
    render(<ExtensionVersionStatus
      compact
      detected
      detectedVersion="0.3.103"
      currentDeviceId="7"
      detectedPaired
      state={{ devices: [recentDevice(7), recentDevice(8)], current_version: '0.3.103', minimum_version: '0.3.100' }}
      onPair={() => {}}
    />);

    expect(screen.queryByText('Extension detected; browser not paired')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pair this browser' })).not.toBeInTheDocument();
  });

  it('does not infer pairing from version alone when the extension reports unpaired', () => {
    render(<ExtensionVersionStatus
      compact
      detected
      detectedVersion="0.3.103"
      detectedPaired={false}
      state={{ devices: [recentDevice(7)], current_version: '0.3.103', minimum_version: '0.3.100' }}
      onPair={() => {}}
    />);

    expect(screen.getByText('Extension detected; browser not paired')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pair this browser' })).toBeInTheDocument();
  });

  it('Dashboard Pair this browser click creates an account code and dispatches it to the extension', async () => {
    const createCode = vi.fn().mockResolvedValue({ pairing_code: 'one-time-code-1234567890' });
    const pageWindow = { location: { origin: 'https://posterpro.sparkleserver.site' }, postMessage: vi.fn() };
    const onPair = () => requestCurrentBrowserPairing(createCode, pageWindow);
    render(<ExtensionVersionStatus
      compact
      detected
      detectedVersion="0.3.103"
      detectedPaired={false}
      state={{ devices: [], current_version: '0.3.103', minimum_version: '0.3.100' }}
      onPair={onPair}
    />);

    fireEvent.click(screen.getByRole('button', { name: 'Pair this browser' }));

    expect(createCode).toHaveBeenCalledWith('PosterPro browser');
    await vi.waitFor(() => expect(pageWindow.postMessage).toHaveBeenCalledWith({
      source: 'posterpro-settings',
      type: 'PAIR_EXTENSION',
      pairing_code: 'one-time-code-1234567890',
    }, 'https://posterpro.sparkleserver.site'));
  });
});
