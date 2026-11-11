import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';
import Head from 'next/head';
import { fetchPublicScannedItem } from '../../../lib/api';

export default function ScannedItem() {
  const router = useRouter();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => { if (!router.isReady || !router.query.token) return; fetchPublicScannedItem(router.query.token).then(setData).catch((e) => setError(e.message || 'Item not found')); }, [router.isReady, router.query.token]);
  return <><Head><title>{data?.title || 'PosterPro item'}</title></Head><main style={{ maxWidth: 640, margin: '0 auto', padding: 20, fontFamily: 'system-ui', color: '#172033' }}>
    {error ? <p style={{ color: '#b42318' }}>{error}</p> : !data ? <p>Loading item…</p> : <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', gap: 12 }}><div><p style={{ margin: 0, color: '#667085', fontSize: 12 }}>POSTERPRO ITEM</p><h1 style={{ margin: '4px 0 8px', fontSize: 26 }}>{data.title || data.item_id}</h1><p style={{ margin: 0 }}>{data.item_id} · Box {data.box_id || '—'} · Bin {data.bin_code || '—'}</p></div><strong style={{ color: data.sale?.sold ? '#b42318' : '#027a48' }}>{data.sale?.sold ? 'SOLD' : 'IN INVENTORY'}</strong></div>
      {data.photos?.length ? <img src={data.photos[0]} alt="Product" style={{ width: '100%', maxHeight: 360, objectFit: 'contain', margin: '20px 0', borderRadius: 12, background: '#f2f4f7' }} /> : null}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}><div><b>Brand</b><br />{data.brand || '—'}</div><div><b>Condition</b><br />{data.condition || '—'}</div><div><b>Location</b><br />{data.location || '—'}</div><div><b>Current price</b><br />{data.listing?.listing_price ? `$${Number(data.listing.listing_price).toFixed(2)}` : '—'}</div></div>
      {data.sale?.sold ? <button onClick={() => window.print()} style={{ marginTop: 24, padding: '12px 16px', borderRadius: 8, border: 0, background: '#175cd3', color: '#fff' }}>Print shipping / item reference</button> : null}
    </>}
  </main></>;
}
