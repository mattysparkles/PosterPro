import { useEffect, useMemo, useState } from 'react';

import ClusterPreview from '../components/ClusterPreview';
import ListingEditor from '../components/ListingEditor';
import { fetchClusters, fetchListings, generateListing, publishListing, updateListing } from '../lib/api';

export default function Dashboard() {
  const [clusters, setClusters] = useState([]);
  const [listings, setListings] = useState([]);

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
          <span>{readyCount} ready listings</span>
        </div>
      </header>
      <ClusterPreview clusters={clusters} />
      <section className="card">
        <h2>Listing Editor</h2>
        <div className="listing-grid">
          {listings.map((listing) => (
            <ListingEditor
              key={listing.id}
              listing={listing}
              onSave={async (id, form) => {
                await updateListing(id, form);
                await reload();
              }}
              onGenerate={async (id) => {
                await generateListing(id);
                await reload();
              }}
              onPublish={async (id) => {
                await publishListing(id);
                await reload();
              }}
            />
          ))}
        </div>
      </section>
    </main>
  );
}
