import Head from 'next/head';
import Link from 'next/link';
import { useState } from 'react';
import { ArrowLeft, Check, Copy } from 'lucide-react';
import Button from '../../../../components/ui/button';
import { toThumbnailImageUrl } from '../../../../lib/api';

const money = (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value || 0));

export default function StoreProduct({ store, product }) {
  const [active, setActive] = useState(0);
  const [buyer, setBuyer] = useState({ customer_name: '', customer_email: '', street: '', street2: '', city: '', region: '', postal_code: '', country: 'US', payment_method: store.payment_methods?.[0]?.method || '' });
  const [checkout, setCheckout] = useState(null);
  const [checkoutError, setCheckoutError] = useState('');
  const [checkoutBusy, setCheckoutBusy] = useState(false);
  const images = product.image_urls?.length ? product.image_urls : product.thumbnail_url ? [product.thumbnail_url] : [];
  const createCheckout = async (event) => {
    event.preventDefault(); setCheckoutBusy(true); setCheckoutError('');
    try {
      const response = await fetch(`/api/public/stores/${encodeURIComponent(store.slug)}/checkout`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ listing_id: product.id, customer_name: buyer.customer_name, customer_email: buyer.customer_email, payment_method: buyer.payment_method, idempotency_key: crypto.randomUUID(), shipping_address: { street: buyer.street, street2: buyer.street2, city: buyer.city, region: buyer.region, postal_code: buyer.postal_code, country: buyer.country } }) });
      const data = await response.json(); if (!response.ok) throw new Error(data?.detail || 'Checkout could not be started.'); setCheckout(data);
    } catch (error) { setCheckoutError(error.message); } finally { setCheckoutBusy(false); }
  };
  const reportSent = async () => {
    setCheckoutBusy(true); setCheckoutError('');
    try { const response = await fetch(`/api/public/stores/${encodeURIComponent(store.slug)}/orders/${encodeURIComponent(checkout.order_number)}/payment-sent?token=${encodeURIComponent(checkout.checkout_token)}`, { method: 'POST' }); const data = await response.json(); if (!response.ok) throw new Error(data?.detail || 'Could not update the order.'); setCheckout({ ...checkout, ...data, checkout_token: checkout.checkout_token }); }
    catch (error) { setCheckoutError(error.message); } finally { setCheckoutBusy(false); }
  };
  return <>
    <Head><title>{product.title} | {store.store_name}</title><meta name="description" content={(product.description || '').slice(0, 155)} /><meta property="og:title" content={product.title} />{images[0] ? <meta property="og:image" content={images[0]} /> : null}</Head>
    <main className="min-h-screen bg-[#f7f8fa] text-slate-900" style={{ '--store-accent': store.accent_color || '#1d4f7a' }}>
      <header className="border-b border-slate-200 bg-white"><div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4"><Link href={`/store/${store.slug}`} className="text-lg font-bold text-slate-950">{store.store_name}</Link><Link href={`/store/${store.slug}`} className="inline-flex items-center gap-2 text-sm font-semibold text-slate-700"><ArrowLeft size={17} /> Back to shop</Link></div></header>
      <div className="mx-auto grid max-w-7xl gap-10 px-5 py-8 lg:grid-cols-2 lg:py-12">
        <section aria-label="Product photos">
          <div className="aspect-square overflow-hidden rounded-2xl bg-white shadow-sm">{images[active] ? <img src={toThumbnailImageUrl(images[active], 1200, 1200)} alt={product.title} className="h-full w-full object-contain" /> : <div className="grid h-full place-items-center text-slate-400">Photo unavailable</div>}</div>
          {images.length > 1 ? <div className="mt-3 flex gap-2 overflow-x-auto">{images.map((src, index) => <button key={src} aria-label={`Show photo ${index + 1}`} onClick={() => setActive(index)} className={`h-16 w-16 shrink-0 overflow-hidden rounded-lg border-2 ${index === active ? 'border-slate-900' : 'border-transparent'}`}><img src={toThumbnailImageUrl(src, 96, 96)} alt="" className="h-full w-full object-cover" /></button>)}</div> : null}
        </section>
        <section className="pt-2">
          <p className="text-sm font-medium text-slate-500">{product.category || store.store_name}</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight">{product.title}</h1>
          <p className="mt-4 text-3xl font-bold">{money(product.price)}</p>
          <div className="mt-4 flex flex-wrap gap-2">{product.condition ? <span className="rounded-full bg-slate-100 px-3 py-1 text-sm text-slate-700">{product.condition}</span> : null}<span className="rounded-full bg-emerald-50 px-3 py-1 text-sm font-medium text-emerald-800">{product.availability}</span></div>
          <div className="mt-7 border-t border-slate-200 pt-6"><h2 className="text-lg font-semibold">Description</h2><p className="mt-3 whitespace-pre-line text-base leading-7 text-slate-700">{product.description || 'See photos for item details.'}</p></div>
          {Object.keys(product.specifications || {}).length ? <div className="mt-6"><h2 className="text-lg font-semibold">Details</h2><dl className="mt-3 divide-y divide-slate-200 rounded-xl border border-slate-200 bg-white">{Object.entries(product.specifications).map(([key, value]) => <div key={key} className="flex justify-between gap-5 px-4 py-3 text-sm"><dt className="text-slate-500">{key}</dt><dd className="text-right font-medium text-slate-800">{value}</dd></div>)}</dl></div> : null}
          {product.shipping_message ? <p className="mt-5 text-sm text-slate-600">{product.shipping_message}</p> : null}
          <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-5"><h2 className="text-lg font-semibold">Purchase options</h2>
            {store.direct_checkout_available ? <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4"><h3 className="font-semibold">Buy direct</h3>{checkout ? <div className="mt-3 space-y-3 text-sm"><p>Order <strong>{checkout.order_number}</strong> · {checkout.payment_status.replaceAll('_', ' ').toLowerCase()}</p><p>Total: <strong>{money(checkout.total)}</strong>{checkout.discount_amount ? ` (includes ${money(checkout.discount_amount)} discount)` : ''}</p><p>Send payment to <strong>{checkout.payment_handle}</strong> using {checkout.payment_label}. {checkout.payment_instructions}</p><p className="text-slate-600">Your seller must confirm receipt. Telling us you sent payment does not mark the order paid.</p>{checkout.payment_status === 'AWAITING_PAYMENT' ? <Button onClick={reportSent} disabled={checkoutBusy}>{checkoutBusy ? 'Saving…' : 'I sent payment'}</Button> : null}</div> : <form onSubmit={createCheckout} className="mt-3 grid gap-3 sm:grid-cols-2">
              <input required aria-label="Your name" placeholder="Full name" value={buyer.customer_name} onChange={(e) => setBuyer({ ...buyer, customer_name: e.target.value })} className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm" />
              <input required type="email" aria-label="Email address" placeholder="Email address" value={buyer.customer_email} onChange={(e) => setBuyer({ ...buyer, customer_email: e.target.value })} className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm" />
              <input required aria-label="Street address" placeholder="Street address" value={buyer.street} onChange={(e) => setBuyer({ ...buyer, street: e.target.value })} className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm sm:col-span-2" />
              <input aria-label="Apartment or unit" placeholder="Apartment / unit (optional)" value={buyer.street2} onChange={(e) => setBuyer({ ...buyer, street2: e.target.value })} className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm sm:col-span-2" />
              <input required aria-label="City" placeholder="City" value={buyer.city} onChange={(e) => setBuyer({ ...buyer, city: e.target.value })} className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm" />
              <input required aria-label="State or region" placeholder="State / region" value={buyer.region} onChange={(e) => setBuyer({ ...buyer, region: e.target.value })} className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm" />
              <input required aria-label="Postal code" placeholder="Postal code" value={buyer.postal_code} onChange={(e) => setBuyer({ ...buyer, postal_code: e.target.value })} className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm" />
              <input required aria-label="Country" placeholder="Country code" value={buyer.country} onChange={(e) => setBuyer({ ...buyer, country: e.target.value })} className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm" />
              <select required aria-label="Payment method" value={buyer.payment_method} onChange={(e) => setBuyer({ ...buyer, payment_method: e.target.value })} className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm sm:col-span-2">{(store.payment_methods || []).map((method) => <option key={method.method} value={method.method}>{method.label}{method.discount_percent ? ` · ${method.discount_percent}% discount` : ''}</option>)}</select>
              <Button type="submit" disabled={checkoutBusy} className="sm:col-span-2">{checkoutBusy ? 'Starting secure order…' : 'Place order'}</Button>
            </form>}</div> : null}
            {checkoutError ? <p role="alert" className="mt-3 text-sm font-medium text-rose-700">{checkoutError}</p> : null}
            {product.marketplace_links?.length ? <><p className="mt-5 text-sm font-medium text-slate-600">Also available on</p><div className="mt-3 grid gap-3">{product.marketplace_links.map((item) => <Button key={item.marketplace} variant="secondary" href={item.outbound_path} external className="w-full justify-center">Buy on {({ ebay: 'eBay', facebook: 'Facebook Marketplace', mercari: 'Mercari', poshmark: 'Poshmark', vinted: 'Vinted', etsy: 'Etsy', offerup: 'OfferUp' })[item.marketplace] || item.marketplace}<span className="ml-auto text-xs font-normal text-slate-500">Last known live</span></Button>)}</div></> : <p className="mt-3 text-sm text-slate-600">No current marketplace purchase links are available.</p>}
            {store.contact_email ? <a href={`mailto:${store.contact_email}`} className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-slate-700">Ask the seller a question</a> : null}
          </div>
        </section>
      </div>
      <footer className="border-t border-slate-200 bg-white px-5 py-6 text-center text-sm text-slate-500">{store.store_name} · Powered by PosterPro</footer>
    </main>
  </>;
}

export async function getServerSideProps({ params }) {
  try {
    const apiBase = process.env.POSTERPRO_INTERNAL_API_BASE || 'http://127.0.0.1:8030';
    const response = await fetch(`${apiBase}/public/stores/${encodeURIComponent(params.slug)}/products/${encodeURIComponent(params.listingId)}`);
    if (!response.ok) return { notFound: true };
    const data = await response.json();
    return { props: { store: data.store, product: data.product } };
  } catch { return { notFound: true }; }
}
