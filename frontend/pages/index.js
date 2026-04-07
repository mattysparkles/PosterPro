import { useEffect, useMemo, useState } from 'react';

import ClusterPreview from '../components/ClusterPreview';
import ListingEditor from '../components/ListingEditor';
import PublishedListings from '../components/PublishedListings';
import SyncPanel from '../components/SyncPanel';
import { useMarketplacePublish } from '../hooks/useMarketplacePublish';
import {
  connectMarketplace,
  fetchClusters,
  fetchListings,
  fetchMarketplaces,
  generateListing,
  updateListing,
} from '../lib/api';

export default function Dashboard() {
  const [clusters, setClusters] = useState([]);
  const [listings, setListings] = useState([]);
  const [marketplaces, setMarketplaces] = useState([]);
  const [connectError, setConnectError] = useState('');
  const { publish, publishing, errors, statusByListing, refreshStatus } = useMarketplacePublish();

  const reload = async () => {
    const [c, l, m] = await Promise.all([fetchClusters(), fetchListings(), fetchMarketplaces()]);
    setClusters(c);
    setListings(l);
    setMarketplaces(m.marketplaces || []);
  };

  useEffect(() => {
    reload();
  }, []);

  useEffect(() => {
    listings.forEach((listing) => {
      refreshStatus(listing.id).catch(() => undefined);
    });
  }, [listings, refreshStatus]);

  const readyCount = useMemo(() => listings.filter((l) => l.status === 'ready').length, [listings]);

  const bulkApprove = async () => {
    await Promise.all(
      listings
        .filter((l) => l.status === 'draft')
        .map((l) => generateListing(l.id))
    );
    await reload();
  };

  const connect = async (name) => {
    setConnectError('');
    try {
      await connectMarketplace(name, 1);
    } catch (err) {
      setConnectError(err.message);
    }
  };

  return (
    <main className="container">
      <header className="topbar">
        <h1>Reseller Cross-Posting Dashboard</h1>
        <div className="actions">
          <button onClick={bulkApprove}>Bulk Approve Drafts</button>
          <span>{readyCount} ready listings</span>
        </div>
        <div className="actions">
          {marketplaces.map((m) => (
            <button key={m.name} onClick={() => connect(m.name)}>
              Connect {m.name}
            </button>
          ))}
        </div>
      </header>
      {connectError && <p className="error-text">{connectError}</p>}
      <ClusterPreview clusters={clusters} />
      <SyncPanel />
      <section className="card">
        <h2>Listing Editor</h2>
        <div className="listing-grid">
          {listings.map((listing) => (
            <ListingEditor
              key={listing.id}
              listing={listing}
              statuses={statusByListing[listing.id] || []}
              publishState={{
                loading: !!publishing[listing.id],
                error: errors[listing.id] || '',
              }}
              onSave={async (id, form) => {
                await updateListing(id, form);
                await reload();
              }}
              onGenerate={async (id) => {
                await generateListing(id);
                await reload();
              }}
              onPublish={async (id, targets) => {
                await publish(id, targets);
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
