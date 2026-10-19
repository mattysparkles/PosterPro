import { useEffect, useRef, useState } from 'react';
import AppShell from '../../components/layout/AppShell';
import { findInventoryItem } from '../../lib/api';

/** Private Find My Item station; scanner readers simply type then emit Enter. */
export default function FindInventoryPage() {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const inputRef = useRef(null);
  useEffect(() => { inputRef.current?.focus(); }, []);
  const find = async (event) => {
    event?.preventDefault();
    if (!query.trim()) return;
    setError(''); setResult(null);
    try { setResult(await findInventoryItem(query.trim())); }
    catch (caught) { setError(caught.message || 'Lookup failed. Check your connection and try again.'); }
  };
  return <AppShell title="Find my item" subtitle="Scan a product, package, tote QR/NFC value, or type a code.">
    <main className="mx-auto max-w-3xl space-y-5 p-4 md:p-6">
      <form onSubmit={find} className="rounded-xl border bg-white p-5 shadow-sm">
        <label className="block text-sm font-semibold text-slate-800" htmlFor="find-code">Scan or enter an identifier</label>
        <div className="mt-2 flex gap-2"><input ref={inputRef} id="find-code" value={query} onChange={(event) => setQuery(event.target.value)} className="min-w-0 flex-1 rounded border px-3 py-2" placeholder="Barcode, PKG-..., AO9, NFC value" /><button type="submit" className="rounded bg-slate-900 px-4 py-2 text-white">Find</button></div>
        <p className="mt-2 text-xs text-slate-500">A keyboard-wedge or Bluetooth scanner can remain focused here and submits with its Enter suffix.</p>
      </form>
      {error ? <p role="alert" className="rounded border border-red-200 bg-red-50 p-4 text-red-800">{error}</p> : null}
      {result && !result.found ? <section className="rounded-xl border border-amber-200 bg-amber-50 p-5"><h2 className="font-semibold text-amber-900">Not found in this account</h2><p className="mt-1 text-sm text-amber-800">{result.message} If you are offline, reconnect and synchronize before treating this as a negative inventory result.</p></section> : null}
      {result?.match_type === 'product' ? <section className="space-y-3">{result.products.map((product) => <article key={product.product_id} className="rounded-xl border bg-white p-5 shadow-sm"><h2 className="font-semibold text-slate-900">{product.title}</h2><p className="text-sm text-slate-600">{product.barcode || product.product_key}</p>{product.locations.length ? <ul className="mt-3 space-y-2">{product.locations.map((location) => <li key={location.location_key} className="rounded bg-slate-50 p-3 text-sm"><strong>{location.label}</strong> · {location.available} available ({location.on_hand} on hand, {location.reserved} reserved)</li>)}</ul> : <p className="mt-3 text-sm text-amber-700">Known product, but no on-hand stock is currently recorded.</p>}</article>)}</section> : null}
      {result?.match_type === 'package' ? <section className="rounded-xl border bg-white p-5 shadow-sm"><h2 className="font-semibold">Package {result.package.package_code}</h2><p className="text-sm text-slate-600">{result.package.quantity} units · {result.package.parent_location_label}</p></section> : null}
      {result?.match_type === 'tote' ? <section className="rounded-xl border bg-white p-5 shadow-sm"><h2 className="font-semibold">Tote {result.tote.code}</h2><p className="mt-1 text-sm text-slate-600">{result.tote.contents.length} product line(s) currently inside.</p><ul className="mt-3 space-y-2">{result.tote.contents.map((entry) => <li key={entry.product_id} className="rounded bg-slate-50 p-3 text-sm">{entry.title} · {entry.on_hand} on hand</li>)}</ul></section> : null}
    </main>
  </AppShell>;
}
