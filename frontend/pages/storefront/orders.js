import { useEffect, useState } from 'react';
import AppShell from '../../components/layout/AppShell';
import Button from '../../components/ui/button';
import PageHeader from '../../components/ui/page-header';
import Input from '../../components/ui/input';
import Select from '../../components/ui/select';
import { confirmStorefrontOrderPayment, fetchStorefrontOrders, rejectStorefrontOrderPayment, updateStorefrontOrderShipment } from '../../lib/api';

const money = (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value || 0));

export default function StoreOrders() {
  const [status, setStatus] = useState('');
  const [orders, setOrders] = useState([]);
  const [message, setMessage] = useState('');
  const [busyId, setBusyId] = useState(null);
  const reload = () => fetchStorefrontOrders(status).then((result) => setOrders(result.items || [])).catch((error) => setMessage(error.message));
  useEffect(() => { reload(); }, [status]);
  const act = async (id, callback, success) => { setBusyId(id); setMessage(''); try { await callback(id); setMessage(success); await reload(); } catch (error) { setMessage(error.message); } finally { setBusyId(null); } };
  return <AppShell><main className="mx-auto max-w-6xl space-y-5 px-5 py-6">
    <PageHeader title="Store orders" description="Review manual payments, fulfill paid orders, and keep direct-store sales in sync with inventory." />
    <div className="flex flex-wrap items-center justify-between gap-3"><label className="text-sm font-semibold">Payment status<Select value={status} onChange={(e) => setStatus(e.target.value)} className="ml-3 inline-flex w-auto"><option value="">All orders</option><option value="PAYMENT_PENDING_VERIFICATION">Needs payment review</option><option value="PAID">Paid</option><option value="PAYMENT_FAILED">Not received</option><option value="MANUAL_REVIEW">Manual review</option></Select></label><span className="text-sm text-slate-500">{orders.length} recent orders</span></div>
    {message ? <p role="status" className="rounded-lg border border-slate-200 bg-white p-3 text-sm text-slate-700">{message}</p> : null}
    {!orders.length ? <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center text-slate-600">No direct store orders yet.</div> : <div className="space-y-4">{orders.map((order) => <article key={order.order_number} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="text-lg font-bold">{order.order_number}</h2><p className="mt-1 text-sm text-slate-600">{order.customer_name} · <a className="text-slate-800 underline" href={`mailto:${order.customer_email}`}>{order.customer_email}</a></p><p className="mt-1 text-sm text-slate-500">{order.payment_method} · {order.payment_status.replaceAll('_', ' ')} · {order.fulfillment_status.replaceAll('_', ' ')}</p></div><p className="text-xl font-bold">{money(order.total)}</p></div>
      <ul className="mt-4 divide-y divide-slate-100">{(order.items || []).map((item) => <li key={`${item.listing_id}-${item.title}`} className="py-2 text-sm">{item.quantity} × {item.title} <span className="text-slate-500">({money(item.unit_price)})</span></li>)}</ul>
      {Object.keys(order.shipping_address || {}).length ? <p className="mt-3 text-sm text-slate-600">Ship to: {['street','street2','city','region','postal_code','country'].map((key) => order.shipping_address[key]).filter(Boolean).join(', ')}</p> : null}
      {order.internal_note ? <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{order.internal_note}</p> : null}
      <div className="mt-4 flex flex-wrap gap-2">
        {order.payment_status === 'PAYMENT_PENDING_VERIFICATION' ? <><Button disabled={busyId === order.id} onClick={() => { if (window.confirm('Confirm that you verified this payment in your Cash App or Venmo account? This will record the sale and reconcile inventory.')) act(order.id, confirmStorefrontOrderPayment, 'Payment confirmed; sale and inventory reconciliation recorded.'); }}>Confirm payment received</Button><Button variant="secondary" disabled={busyId === order.id} onClick={() => { if (window.confirm('Mark this payment as not received? The inventory reservation will be released.')) act(order.id, rejectStorefrontOrderPayment, 'Order marked not received.'); }}>Not received</Button></> : null}
        {order.payment_status === 'PAID' && order.fulfillment_status !== 'SHIPPED' ? <ShipmentForm order={order} onSave={(body) => act(order.id, (id) => updateStorefrontOrderShipment(id, body), 'Shipment saved.')} busy={busyId === order.id} /> : null}
        {order.tracking_number ? <p className="self-center text-sm text-slate-600">{order.carrier}: {order.tracking_number}</p> : null}
      </div>
    </article>)}</div>}
  </main></AppShell>;
}

function ShipmentForm({ onSave, busy }) {
  const [carrier, setCarrier] = useState('USPS'); const [tracking, setTracking] = useState('');
  return <form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); if (tracking.trim()) onSave({ carrier, tracking_number: tracking }); }}><Input aria-label="Carrier" value={carrier} onChange={(e) => setCarrier(e.target.value)} className="w-28" /><Input required aria-label="Tracking number" value={tracking} onChange={(e) => setTracking(e.target.value)} placeholder="Tracking number" className="w-44" /><Button type="submit" variant="secondary" disabled={busy}>Mark shipped</Button></form>;
}

StoreOrders.requireAuth = true;
