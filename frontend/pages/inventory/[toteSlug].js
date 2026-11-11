import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useCallback, useEffect, useState } from 'react';
import AppShell from '../../components/layout/AppShell';
import { fetchInventoryTote } from '../../lib/api';

function permanentToteCode(slug) {
  const match = String(slug || '').match(/^tote-(a0[1-8])$/i);
  return match ? match[1].toUpperCase() : null;
}

export default function PermanentTotePage() {
  const router = useRouter();
  const code = permanentToteCode(router.query.toteSlug);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!code) return;
    setLoading(true);
    setError('');
    try {
      // Viewing an NFC page is read-only. Tote provisioning is an explicit
      // owner/admin setup operation, never an incidental page-load side effect.
      setData(await fetchInventoryTote(code));
    } catch (requestError) {
      setError(requestError.message || 'Unable to load this tote. Sign in to PosterPro and try again.');
    } finally {
      setLoading(false);
    }
  }, [code]);

  useEffect(() => {
    if (router.isReady && code) load();
  }, [router.isReady, code, load]);

  if (!router.isReady) return <AppShell active="/inventory" title="Tote"><main className="mx-auto max-w-4xl p-5">Loading tote…</main></AppShell>;
  if (!code) return <AppShell active="/inventory" title="Tote not found"><main className="mx-auto max-w-4xl p-5"><h1 className="text-xl font-semibold">Tote page not found</h1><p className="mt-2 text-sm text-slate-600">This permanent link does not match one of PosterPro totes A01–A08.</p><Link className="mt-4 inline-block underline" href="/inventory">Open inventory</Link></main></AppShell>;

  const items = data?.items || [];
  return <AppShell active="/inventory" title={`Tote ${code}`}>
    <Head><title>{`Tote ${code} · PosterPro`}</title><meta name="robots" content="noindex,nofollow" /></Head>
    <main className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Live inventory manifest</p><h1 className="mt-1 text-2xl font-bold">Tote {code}</h1><p className="mt-1 text-sm text-slate-600">Permanent tote identity · contents reflect current PosterPro stock records.</p></div>
        <button type="button" onClick={load} disabled={loading} className="rounded-lg border px-4 py-2 text-sm font-medium disabled:opacity-50">{loading ? 'Refreshing…' : 'Refresh manifest'}</button>
      </header>
      {error ? <section role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900"><p>{error}</p><button type="button" onClick={load} className="mt-3 rounded border border-rose-300 px-3 py-1.5 font-medium">Retry</button></section> : null}
      {data ? <>
        <section className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-xl border bg-white p-4"><p className="text-sm text-slate-500">Product lines</p><p className="mt-1 text-2xl font-semibold">{data.summary?.product_count ?? items.length}</p></div>
          <div className="rounded-xl border bg-white p-4"><p className="text-sm text-slate-500">Confirmed units on hand</p><p className="mt-1 text-2xl font-semibold">{data.summary?.on_hand ?? 0}</p></div>
          <div className="rounded-xl border bg-white p-4"><p className="text-sm text-slate-500">Available units</p><p className="mt-1 text-2xl font-semibold">{data.summary?.available ?? 0}</p></div>
        </section>
        {data.summary?.unresolved_location_claims ? <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">{data.summary.unresolved_location_claims} barcode location claim(s) need reconciliation and are not included in confirmed stock totals.</p> : null}
        <section className="overflow-hidden rounded-xl border bg-white">
          <div className="border-b px-4 py-3"><h2 className="font-semibold">Current contents</h2><p className="text-xs text-slate-500">Permanent URL: https://posterpro.sparkleserver.site/inventory/tote-{code.toLowerCase()}</p></div>
          {items.length ? <ul className="divide-y">{items.map((item, index) => <li key={`${item.product_id || item.barcode || item.title}-${index}`} className="flex flex-wrap items-center justify-between gap-2 p-4"><div><p className="font-medium">{item.title || 'Unidentified item'}</p><p className="text-sm text-slate-600">Raw barcode: {item.barcode || 'not recorded'} · {item.identification_status || item.status || 'inventory'}{item.candidate_identifier_type ? ` · ${item.candidate_identifier_type}` : ''}</p>{item.warning ? <p className="mt-1 text-sm text-amber-800">{item.warning}</p> : null}{item.barcode ? <Link className="mt-1 inline-block text-sm font-medium underline" href={`/packing?tote=${code}&resolve=${encodeURIComponent(item.barcode)}`}>Resolve item / look up barcode</Link> : null}</div><p className="text-sm font-medium">{item.status === 'pending_reconciliation' ? 'Unconfirmed location claim · not counted' : `Available ${item.available} / ${item.on_hand}`}</p></li>)}</ul> : <p className="p-5 text-sm text-slate-600">This tote is currently empty. Scan or import a barcode manifest into Tote {code} to add its physical contents.</p>}
        </section>
        <nav className="flex flex-wrap gap-4 text-sm"><Link className="underline" href={`/packing?tote=${code}`}>Scan into Tote {code}</Link><Link className="underline" href="/packing">Open scanner workstation</Link><Link className="underline" href="/inventory/manifests">Import or review barcode manifests</Link><Link className="underline" href="/inventory">Inventory overview</Link></nav>
      </> : !error ? <p role="status" className="rounded-xl border bg-white p-5 text-sm text-slate-600">{loading ? 'Loading the live tote manifest…' : 'No manifest loaded.'}</p> : null}
    </main>
  </AppShell>;
}
