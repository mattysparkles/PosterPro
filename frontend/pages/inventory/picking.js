import { useCallback, useEffect, useState } from 'react';
import AppShell from '../../components/layout/AppShell';
import Button from '../../components/ui/button';
import PageHeader from '../../components/ui/page-header';
import { censusApi } from '../../lib/api';

const nextAction = {
  AWAITING_PICK: ['PICKED', 'Confirm Picked'],
  PICKED: ['PACKED', 'Confirm Packed'],
  PACKED: ['LABEL_READY', 'Mark Label Ready'],
  LABEL_READY: ['SHIPPED', 'Mark Shipped'],
};

export default function InventoryPickingPage() {
  const [orders, setOrders] = useState([]);
  const [scans, setScans] = useState({});
  const [entries, setEntries] = useState({});
  const [tracking, setTracking] = useState({});
  const [handoff, setHandoff] = useState({});
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const load = useCallback(async () => { const result = await censusApi('/picking/queue?limit=250'); setOrders(result.items || []); }, []);
  useEffect(() => { load().catch((caught) => setError(caught.message || 'Could not load sold items.')); }, [load]);
  const advance = async (order) => {
    const action = nextAction[order.pick_state];
    if (!action) return;
    const state = action[0];
    setBusy(order.sale_id); setError(''); setNotice('');
    try {
      const result = await censusApi('/picking/sales/' + order.sale_id + '/state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        operation_id: crypto.randomUUID(), state,
        ...(state === 'PICKED' ? { scanned_item_tokens: scans[order.sale_id] || [] } : {}),
        ...(state === 'SHIPPED' ? { operator_confirmed_shipped: handoff[order.sale_id] === true, tracking_reference: tracking[order.sale_id] || null } : {}),
      }) });
      if (state === 'PICKED') setScans((current) => ({ ...current, [order.sale_id]: [] }));
      setNotice(state === 'SHIPPED' ? 'Marked shipped in PosterPro. Marketplace order was not changed.' : 'Order ' + (order.order_id || order.sale_id) + ': ' + state.replaceAll('_', ' ').toLowerCase() + '.');
      if (result.marketplace_fulfillment_updated) throw new Error('Unexpected marketplace update response.');
      await load();
    } catch (caught) { setError(caught.message || 'Could not update this pick.'); }
    finally { setBusy(null); }
  };
  return <AppShell active="/inventory" title="Sold inventory picking">
    <main className="mx-auto max-w-5xl space-y-5 p-4 md:p-6">
      <PageHeader title="Find sold items" description="Scan the physical item label, follow its saved location, then move the order through pick, pack, label, and carrier handoff." />
      <div className="rounded-xl border border-sky-200 bg-sky-50 p-3 text-sm text-sky-950">Picking updates PosterPro's local fulfillment notes only. It never marks an order shipped at a marketplace. “Shipped” requires your carrier-handoff confirmation.</div>
      {error ? <p role="alert" className="rounded-lg bg-red-50 p-3 text-red-800">{error}</p> : null}
      {notice ? <p role="status" className="rounded-lg bg-emerald-50 p-3 text-emerald-900">{notice}</p> : null}
      {!orders.length ? <section className="rounded-xl border bg-white p-6 text-slate-600">No matched, unshipped sales are waiting in the pick queue.</section> : <section className="space-y-3">{orders.map((order) => {
        const action = nextAction[order.pick_state]; const staged = scans[order.sale_id] || [];
        return <article key={order.sale_id} className="space-y-3 rounded-xl border bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-bold">{order.product || 'Product identity needs review'}</h2><p className="text-sm text-slate-600">{order.marketplace} order {order.order_id || '#' + order.sale_id} · Qty {order.quantity} · SKU {order.sku || (order.locations?.[0]?.sku) || '—'}</p><p className="text-sm">Order deadline: {order.deadline ? new Date(order.deadline).toLocaleString() : 'Not provided by the marketplace sync'}</p></div><span className="rounded-full bg-slate-100 px-3 py-1 text-sm font-semibold">{order.pick_state.replaceAll('_', ' ')}</span></div>
          <div className="grid gap-2 sm:grid-cols-2">{order.locations.map((item) => <div key={item.item_uuid} className="rounded-lg bg-slate-50 p-3 text-sm"><b>{item.location}</b><br />SKU {item.sku || order.sku || '—'} · item scan token {item.barcode_token}</div>)}{order.location_unknown ? <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">Location unknown or too few physical units are recorded. Update the location in <a className="underline" href="/inventory/census">Shelf Census</a> before picking.</div> : null}</div>
          {order.pick_state === 'PICKED' ? <p className="text-sm text-emerald-800">Verified item IDs: {(order.picked_item_uuids || []).join(', ')}</p> : null}
          {order.pick_state === 'AWAITING_PICK' ? <div className="space-y-2 rounded-lg border p-3"><label className="block text-sm font-semibold">Scan item barcode{order.quantity > 1 ? ' (' + staged.length + '/' + order.quantity + ')' : ''}<input className="mt-1 w-full rounded border p-3" value={entries[order.sale_id] || ''} onChange={(event) => setEntries((current) => ({ ...current, [order.sale_id]: event.target.value }))} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); const value = String(entries[order.sale_id] || '').trim(); if (value) setScans((current) => ({ ...current, [order.sale_id]: [...(current[order.sale_id] || []), value] })); setEntries((current) => ({ ...current, [order.sale_id]: '' })); } }} placeholder="Scan the permanent item label; scanner Enter adds it" /></label><p className="text-xs text-slate-600">Scanned {staged.length} of {order.quantity}. Each scan must resolve to a separate physical item linked to this listing.</p></div> : null}
          {order.pick_state === 'LABEL_READY' ? <div className="space-y-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={handoff[order.sale_id] === true} onChange={(event) => setHandoff((current) => ({ ...current, [order.sale_id]: event.target.checked }))} /> I handed this package to the carrier</label><label className="block text-sm">Tracking reference (optional)<input className="mt-1 w-full rounded border p-2" value={tracking[order.sale_id] || ''} onChange={(event) => setTracking((current) => ({ ...current, [order.sale_id]: event.target.value }))} /></label></div> : null}
          {action ? <Button disabled={busy === order.sale_id || (order.location_unknown && order.pick_state === 'AWAITING_PICK') || (order.pick_state === 'AWAITING_PICK' && staged.length !== order.quantity) || (order.pick_state === 'LABEL_READY' && handoff[order.sale_id] !== true)} onClick={() => advance(order)}>{busy === order.sale_id ? 'Saving…' : action[1]}</Button> : null}
        </article>;
      })}</section>}
      <Button variant="outline" onClick={() => load().catch((caught) => setError(caught.message))}>Refresh sold items</Button>
    </main>
  </AppShell>;
}
