import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';
import { Search, ShoppingBag } from 'lucide-react';
import Button from '../../components/ui/button';
import { toThumbnailImageUrl } from '../../lib/api';

const API = '/api';
const PAGE_SIZES = [12, 24, 48, 96];
const money = (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value || 0));

export default function PublicStore({ slug, initialStore, initialPage }) {
  const router = useRouter();
  const [data, setData] = useState(initialPage);
  const [busy, setBusy] = useState(false);
  const q = router.query;
  const page = Math.max(1, Number(q.page || 1));
  const pageSize = PAGE_SIZES.includes(Number(q.page_size)) ? Number(q.page_size) : 24;
  const search = typeof q.search === 'string' ? q.search : '';
  const selectedSort = typeof q.sort === 'string' ? q.sort : initialStore?.default_sort || 'newest';
  const sort = selectedSort === 'price' ? 'price_desc' : selectedSort;
  const sortBy = sort === 'price_asc' || sort === 'price_desc' ? 'price' : sort;
  const sortDir = sort === 'price_asc' ? 'asc' : 'desc';
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize), sort_by: sortBy, sort_dir: sortDir });
  for (const key of ['search', 'category', 'condition', 'brand', 'marketplace', 'min_price', 'max_price']) {
    if (typeof q[key] === 'string' && q[key]) params.set(key, q[key]);
  }

  useEffect(() => {
    if (!router.isReady) return;
    setBusy(true);
    fetch(`${API}/public/stores/${encodeURIComponent(slug)}/listings?${params.toString()}`)
      .then(async (r) => { if (!r.ok) throw new Error('This store is temporarily unavailable.'); return r.json(); })
      .then(setData).catch(() => setData({ items: [], total: 0, page, page_size: pageSize, total_pages: 1 })).finally(() => setBusy(false));
    // Query parameters define the public catalog view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router.isReady, router.asPath, slug]);

  const setQuery = (next) => {
    const query = { ...router.query, ...next };
    for (const key of Object.keys(query)) if (query[key] === '' || query[key] == null) delete query[key];
    router.push({ pathname: `/store/${slug}`, query }, undefined, { scroll: true });
  };
  const store = initialStore || {};
  const items = data?.items || [];

  return <>
    <Head>
      <title>{store.store_name || 'Shop'} | Store</title>
      <meta name="description" content={store.description || `Shop ${store.store_name || 'our store'} online.`} />
      <meta property="og:title" content={store.store_name || 'Shop'} />
      <meta property="og:description" content={store.description || ''} />
      {items[0]?.thumbnail_url ? <meta property="og:image" content={items[0].thumbnail_url} /> : null}
    </Head>
    <main className="min-h-screen bg-[#f7f8fa] text-slate-900" style={{ '--store-accent': store.accent_color || '#1d4f7a' }}>
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-5 px-5 py-4 lg:px-8">
          <Link href={`/store/${slug}`} className="flex min-w-0 items-center gap-3 text-slate-950 no-underline">
            {store.logo_url ? <img src={store.logo_url} alt="" className="h-11 w-11 rounded-xl object-cover" /> : <ShoppingBag className="shrink-0" style={{ color: store.accent_color || '#1d4f7a' }} />}
            <span className="truncate text-xl font-bold tracking-tight">{store.store_name || 'Shop'}</span>
          </Link>
          <nav className="hidden items-center gap-6 text-sm font-medium text-slate-600 md:flex"><a href="#products" className="hover:text-slate-950">Shop</a>{store.description ? <a href="#about" className="hover:text-slate-950">About</a> : null}</nav>
          <label className="relative ml-auto min-w-[220px] flex-1 md:max-w-md">
            <span className="sr-only">Search products</span><Search size={17} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input aria-label="Search products" value={search} onChange={(e) => setQuery({ search: e.target.value, page: '1' })} placeholder="Search the store" className="h-11 w-full rounded-full border border-slate-300 bg-slate-50 pl-10 pr-4 text-sm outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200" />
          </label>
        </div>
        {store.banner_url ? <div className="h-48 bg-cover bg-center" style={{ backgroundImage: `url("${store.banner_url.replaceAll('"', '')}")` }} /> : null}
      </header>
      <div className="mx-auto max-w-[1440px] px-5 py-8 lg:px-8">
        {store.description ? <section id="about" className="mb-6 max-w-3xl"><p className="text-base leading-7 text-slate-600">{store.description}</p></section> : null}
        <section id="products" aria-label="Products">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <div><h1 className="text-2xl font-bold tracking-tight">Shop all</h1><p className="mt-1 text-sm text-slate-600">{Number(data?.total || 0).toLocaleString()} items</p></div>
            <div className="flex flex-wrap gap-2">
              <select aria-label="Sort products" value={sort} onChange={(e) => setQuery({ sort: e.target.value, page: '1' })} className="h-10 rounded-lg border border-slate-300 bg-white px-3 text-sm"><option value="newest">Newest</option><option value="price_asc">Price: low to high</option><option value="price_desc">Price: high to low</option><option value="name">Name</option><option value="featured">Featured</option></select>
              <select aria-label="Products per page" value={pageSize} onChange={(e) => setQuery({ page_size: e.target.value, page: '1' })} className="h-10 rounded-lg border border-slate-300 bg-white px-3 text-sm">{PAGE_SIZES.map((size) => <option key={size} value={size}>{size} per page</option>)}</select>
            </div>
          </div>
          <div className="mb-5 grid gap-2 rounded-xl border border-slate-200 bg-white p-3 sm:grid-cols-2 lg:grid-cols-5">
            <input aria-label="Filter by category" placeholder="Category" defaultValue={typeof q.category === 'string' ? q.category : ''} onBlur={(e) => setQuery({ category: e.target.value, page: '1' })} className="h-10 rounded-lg border border-slate-300 px-3 text-sm" />
            <input aria-label="Filter by condition" placeholder="Condition" defaultValue={typeof q.condition === 'string' ? q.condition : ''} onBlur={(e) => setQuery({ condition: e.target.value, page: '1' })} className="h-10 rounded-lg border border-slate-300 px-3 text-sm" />
            <input aria-label="Filter by brand" placeholder="Brand" defaultValue={typeof q.brand === 'string' ? q.brand : ''} onBlur={(e) => setQuery({ brand: e.target.value, page: '1' })} className="h-10 rounded-lg border border-slate-300 px-3 text-sm" />
            <select aria-label="Filter by marketplace" value={typeof q.marketplace === 'string' ? q.marketplace : ''} onChange={(e) => setQuery({ marketplace: e.target.value, page: '1' })} className="h-10 rounded-lg border border-slate-300 bg-white px-3 text-sm"><option value="">Any marketplace</option>{['ebay','facebook','mercari','poshmark','vinted','etsy','offerup'].map((market) => <option key={market} value={market}>{market[0].toUpperCase() + market.slice(1)}</option>)}</select>
            <div className="flex gap-2"><input aria-label="Minimum price" type="number" min="0" placeholder="Min $" defaultValue={typeof q.min_price === 'string' ? q.min_price : ''} onBlur={(e) => setQuery({ min_price: e.target.value, page: '1' })} className="h-10 min-w-0 w-1/2 rounded-lg border border-slate-300 px-3 text-sm" /><input aria-label="Maximum price" type="number" min="0" placeholder="Max $" defaultValue={typeof q.max_price === 'string' ? q.max_price : ''} onBlur={(e) => setQuery({ max_price: e.target.value, page: '1' })} className="h-10 min-w-0 w-1/2 rounded-lg border border-slate-300 px-3 text-sm" /></div>
          </div>
          {busy ? <p className="py-16 text-center text-slate-500">Loading products…</p> : items.length ? <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,220px),1fr))] gap-5">
            {items.map((item) => <article key={item.id} className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
              <Link href={`/store/${slug}/products/${item.id}`} className="block text-inherit no-underline">
                <div className="aspect-[4/5] bg-slate-100">{item.thumbnail_url ? <img src={toThumbnailImageUrl(item.thumbnail_url, 480, 600)} alt={item.title} loading="lazy" className="h-full w-full object-cover" /> : <div className="grid h-full place-items-center text-sm text-slate-400">Photo unavailable</div>}</div>
                <div className="p-4"><h2 className="line-clamp-2 min-h-12 text-sm font-semibold leading-6 text-slate-900">{item.title}</h2><p className="mt-2 text-lg font-bold">{money(item.price)}</p>{item.condition ? <p className="mt-1 text-sm text-slate-500">{item.condition}</p> : null}<p className="mt-1 text-xs font-medium text-emerald-700">{item.availability}</p>{item.marketplace_links?.length ? <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Available marketplaces">{item.marketplace_links.map((link) => <span key={link.marketplace} className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-600">{link.marketplace}</span>)}</div> : null}</div>
              </Link>
            </article>)}
          </div> : <div className="rounded-2xl border border-slate-200 bg-white px-5 py-16 text-center text-slate-600">No products match this view.</div>}
          <div className="mt-7 flex items-center justify-center gap-3">
            <Button variant="secondary" disabled={page <= 1 || busy} onClick={() => setQuery({ page: String(Math.max(1, page - 1)) })}>Previous</Button>
            <span className="text-sm font-medium text-slate-700">Page {page} of {data?.total_pages || 1}</span>
            <Button variant="secondary" disabled={page >= Number(data?.total_pages || 1) || busy} onClick={() => setQuery({ page: String(page + 1) })}>Next</Button>
          </div>
        </section>
      </div>
      <footer className="mt-14 border-t border-slate-200 bg-white"><div className="mx-auto flex max-w-[1440px] flex-wrap justify-between gap-3 px-5 py-7 text-sm text-slate-500 lg:px-8"><span>{store.store_name || 'Shop'}</span>{store.contact_email ? <a href={`mailto:${store.contact_email}`} className="text-slate-700">Contact</a> : null}<span>Powered by PosterPro</span></div></footer>
    </main>
  </>;
}

export async function getServerSideProps({ params, query }) {
  const apiBase = process.env.POSTERPRO_INTERNAL_API_BASE || 'http://127.0.0.1:8030';
  const slug = String(params.slug || '');
  const page = Math.max(1, Number(query.page || 1));
  const size = PAGE_SIZES.includes(Number(query.page_size)) ? Number(query.page_size) : 24;
  const search = typeof query.search === 'string' ? query.search : '';
  try {
    const storeRes = await fetch(`${apiBase}/public/stores/${encodeURIComponent(slug)}`);
    if (!storeRes.ok) return { notFound: true };
    const store = await storeRes.json();
    const chosenSort = query.sort || store.store?.default_sort || 'newest';
    const chosenSortBy = chosenSort.startsWith('price_') ? 'price' : chosenSort;
    const params = new URLSearchParams({ page: String(page), page_size: String(size), search, sort_by: chosenSortBy, sort_dir: chosenSort.endsWith('_asc') ? 'asc' : 'desc' });
    for (const key of ['category', 'condition', 'brand', 'marketplace', 'min_price', 'max_price']) {
      if (typeof query[key] === 'string' && query[key]) params.set(key, query[key]);
    }
    const pageRes = await fetch(`${apiBase}/public/stores/${encodeURIComponent(slug)}/listings?${params.toString()}`);
    if (!pageRes.ok) return { notFound: true };
    const pageData = await pageRes.json();
    return { props: { slug, initialStore: store.store, initialPage: pageData } };
  } catch { return { notFound: true }; }
}
