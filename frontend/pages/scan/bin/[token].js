import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';
import Head from 'next/head';
import { fetchPublicScannedBin } from '../../../lib/api';

export default function ScannedBin() {
  const router = useRouter(); const [data, setData] = useState(null); const [error, setError] = useState('');
  useEffect(() => { if (!router.isReady || !router.query.token) return; fetchPublicScannedBin(router.query.token).then(setData).catch((e) => setError(e.message || 'Bin not found')); }, [router.isReady, router.query.token]);
  return <><Head><title>{data?.bin?.code || 'PosterPro bin'}</title></Head><main style={{ maxWidth: 760, margin: '0 auto', padding: 20, fontFamily: 'system-ui', color: '#172033' }}>{error ? <p>{error}</p> : !data ? <p>Loading bin…</p> : <><p style={{ color: '#667085', fontSize: 12 }}>POSTERPRO BIN</p><h1 style={{ marginTop: 0 }}>{data.bin.code}</h1><p>{data.count} active item{data.count === 1 ? '' : 's'}</p><div style={{ display: 'grid', gap: 10 }}>{data.items.map((item) => <div key={item.item_id} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: 12, border: '1px solid #eaecf0', borderRadius: 10 }}>{item.photo ? <img src={item.photo} alt="" style={{ width: 64, height: 64, objectFit: 'cover', borderRadius: 8 }} /> : null}<div><b>{item.title || item.item_id}</b><div style={{ color: '#667085', fontSize: 13 }}>{item.item_id} · Box {item.box_id || '—'}</div></div></div>)}</div></>}</main></>;
}
