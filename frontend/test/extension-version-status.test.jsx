import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import ExtensionVersionStatus from '../components/marketplaces/ExtensionVersionStatus';

const recentDevice = (id) => ({
  id,
  browser: 'Chrome',
  extension_version: '0.3.103',
  last_seen_at: new Date().toISOString(),
  revoked: false,
  update_required: false,
});

describe('browser extension status', () => {
  it('recognizes the paired browser when presence arrives before its device id', () => {
    render(<ExtensionVersionStatus
      compact
      detected
      detectedVersion="0.3.103"
      detectedPaired
      state={{ devices: [recentDevice(7)], current_version: '0.3.103', minimum_version: '0.3.100' }}
      onPair={() => {}}
    />);

    expect(screen.queryByText('Extension detected; browser not paired')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pair this browser' })).not.toBeInTheDocument();
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

  it('does not guess which browser is current when multiple devices match', () => {
    render(<ExtensionVersionStatus
      compact
      detected
      detectedVersion="0.3.103"
      detectedPaired
      state={{ devices: [recentDevice(7), recentDevice(8)], current_version: '0.3.103', minimum_version: '0.3.100' }}
      onPair={() => {}}
    />);

    expect(screen.getByText('Extension detected; browser not paired')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pair this browser' })).toBeInTheDocument();
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
});
