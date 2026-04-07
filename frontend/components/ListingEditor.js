import { useState } from 'react';

import StatusPill from './StatusPill';

export default function ListingEditor({ listing, onSave, onGenerate, onPublish }) {
  const [form, setForm] = useState({
    title: listing.title || '',
    description: listing.description || '',
    suggested_price: listing.suggested_price || '',
  });

  return (
    <article className="listing-item">
      <div className="listing-header">
        <h3>Listing #{listing.id}</h3>
        <StatusPill status={listing.status} />
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
        <button onClick={() => onPublish(listing.id)}>Publish eBay</button>
      </div>
    </article>
  );
}
