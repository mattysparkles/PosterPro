import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';
import AppShell from '../../../components/layout/AppShell';
import { jsonFetch } from '../../../lib/api';

export default function ToteDetail() {
  const router = useRouter(); const [data, setData] = useState(null); const [error, setError] = useState('');
  useEffect(() => { if (!router.isReady) return; jsonFetch(`/api/inventory/totes/${encodeURIComponent(router.query.code)}`).then(setData).catch(e => setError(e.message || 'Could not load tote')); }, [router.isReady, router.query.code]);
  return <AppShell active="/inventory" title="Tote"><main className="mx-auto max-w-3xl p-4">{error ? <p role="alert">{error}</p> : !data ? <p>Loading tote…</p> : <><h1 className="text-2xl font-bold">Tote {data.tote.code}</h1><p className="text-sm text-slate-600">Container {data.tote.container_uuid} · Parent {data.tote.parent_location_key || 'Unassigned'}</p>{data.items.length ? <ul className="mt-5 space-y-2">{data.items.map((item, i) => <li key={`${item.barcode}-${i}`} className="rounded border p-3"><b>{item.title}</b><div>{item.barcode || 'No barcode'} · Available {item.available} of {item.on_hand} · {item.status}</div></li>)}</ul> : <p className="mt-5">This tote is empty.</p>}</>}</main></AppShell>;
}
