import { useEffect, useMemo, useState } from 'react';

import ClusterPreview from '../components/ClusterPreview';
import ListingEditor from '../components/ListingEditor';
import PublishedListings from '../components/PublishedListings';
import { useEbayAuth } from '../hooks/useEbayAuth';
import { useEbayPublish } from '../hooks/useEbayPublish';
import { fetchClusters, fetchListings, generateListing, updateListing } from '../lib/api';

export default function Dashboard() {
  const [clusters, setClusters] = useState([]);
  const [listings, setListings] = useState([]);
  const { connect, loading: ebayAuthLoading, error: ebayAuthError } = useEbayAuth(1);
  const { publish, publishing, errors, success } = useEbayPublish();

  const reload = async () => {
    const [c, l] = await Promise.all([fetchClusters(), fetchListings()]);
    setClusters(c);
    setListings(l);
  };

  useEffect(() => {
    reload();
  }, []);

  const readyCount = useMemo(() => listings.filter((l) => l.status === 'ready').length, [listings]);

  const bulkApprove = async () => {
    await Promise.all(
      listings
        .filter((l) => l.status === 'draft')
        .map((l) => generateListing(l.id))
    );
    await reload();
  };

  return (
    <main className="container">
      <header className="topbar">
        <h1>Reseller Cross-Posting Dashboard</h1>
        <div className="actions">
          <button onClick={bulkApprove}>Bulk Approve Drafts</button>
          <button disabled={ebayAuthLoading} onClick={connect}>
            {ebayAuthLoading ? 'Connecting…' : 'Connect eBay'}
          </button>
          <span>{readyCount} ready listings</span>
        </div>
      </header>
      {ebayAuthError && <p className="error-text">{ebayAuthError}</p>}
      <ClusterPreview clusters={clusters} />
      <section className="card">
        <h2>Listing Editor</h2>
        <div className="listing-grid">
          {listings.map((listing) => (
            <ListingEditor
              key={listing.id}
              listing={listing}
              publishState={{
                loading: !!publishing[listing.id],
                error: errors[listing.id] || '',
                url: success[listing.id] || listing.marketplace_data?.ebay_url || '',
              }}
              onSave={async (id, form) => {
                await updateListing(id, form);
                await reload();
              }}
              onGenerate={async (id) => {
                await generateListing(id);
                await reload();
              }}
              onPublish={async (id) => {
                await publish(id);
                await reload();
              }}
            />
          ))}
        </div>
      </section>
      <PublishedListings listings={listings} />
    </main>
  );
}
