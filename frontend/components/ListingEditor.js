import { useState } from 'react';

import MarketplaceStatusPanel from './MarketplaceStatusPanel';
import StatusPill from './StatusPill';

const MARKETPLACES = ['ebay', 'etsy', 'mercari', 'facebook'];

export default function ListingEditor({ listing, onSave, onGenerate, onPublish, publishState, statuses }) {
  const [form, setForm] = useState({
    title: listing.title || '',
    description: listing.description || '',
    suggested_price: listing.suggested_price || '',
  });

  return (
    <article className="listing-item">
      <div className="listing-header">
        <h3>Listing #{listing.id}</h3>
        <StatusPill status={listing.ebay_publish_status || listing.status} />
      </div>
      <input value={form.title} placeholder="Title" onChange={(e) => setForm({ ...form, title: e.target.value })} />
      <textarea
        value={form.description}
        placeholder="Description"
        onChange={(e) => setForm({ ...form, description: e.target.value })}
      />
      <input
        type="number"
        value={form.suggested_price}
        placeholder="Price"
        onChange={(e) => setForm({ ...form, suggested_price: Number(e.target.value) })}
      />
      <div className="actions">
        <button onClick={() => onSave(listing.id, form)}>Save</button>
        <button onClick={() => onGenerate(listing.id)}>Generate</button>
        <button disabled={publishState.loading} onClick={() => onPublish(listing.id, MARKETPLACES)}>
          {publishState.loading ? 'Publishing...' : 'Bulk Publish'}
        </button>
      </div>

      <div className="actions">
        {MARKETPLACES.map((market) => (
          <button key={market} disabled={publishState.loading} onClick={() => onPublish(listing.id, [market])}>
            Publish to {market}
          </button>
        ))}
      </div>

      {publishState.error && <p className="error-text">{publishState.error}</p>}
      <MarketplaceStatusPanel statuses={statuses} />
    </article>
  );
}
