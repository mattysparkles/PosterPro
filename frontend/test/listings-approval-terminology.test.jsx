import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../components/listings/workspace/ListingsMarketplacePreflightBadges', () => ({
  default: () => null,
}));

import ListingsTitleCell from '../components/listings/table/ListingsTitleCell';

describe('listing approval terminology', () => {
  it('labels a completed review-queue listing Ready for Approval', () => {
    render(<ListingsTitleCell
      listing={{ id: 42, title: 'Completed draft', status: 'draft' }}
      getListingTitle={(listing) => listing.title}
      getReadinessSummary={() => ({ blockers: [] })}
      getListingImageCount={() => 1}
      isAmazonVineSource={() => false}
      getListingBucket={() => 'review'}
      publishSingleListing={vi.fn()}
      publishing={false}
      approveListing={vi.fn()}
      deleteListing={vi.fn()}
      confirmDeleteListings={vi.fn()}
      archiveListing={vi.fn()}
      isArchivedListing={() => false}
      router={{ push: vi.fn() }}
      getMarketplacePreflightSummary={() => null}
      getMarketplacePreflightStatus={() => null}
      getMarketplacePreflightTone={() => null}
      getMarketplaceTopBlocker={() => null}
      getMarketplacePreflightAgeLabel={() => null}
    />);

    expect(screen.getByText('Ready for Approval')).toBeInTheDocument();
    expect(screen.queryByText('Needs Review')).not.toBeInTheDocument();
  });
});
