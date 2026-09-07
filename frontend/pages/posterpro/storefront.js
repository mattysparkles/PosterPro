import Head from 'next/head';
import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Copy, ExternalLink, Search, Store } from 'lucide-react';
import { useRouter } from 'next/router';

import Button from '../../components/ui/button';
import { fetchPublicStorefrontListings, toThumbnailImageUrl } from '../../lib/api';

const PAGE_SIZE_OPTIONS = [25, 50, 100];
const SORT_OPTIONS = [
  { value: 'updated:desc', label: 'Newest updated' },
  { value: 'created:desc', label: 'Newest created' },
  { value: 'price:asc', label: 'Price: low to high' },
  { value: 'price:desc', label: 'Price: high to low' },
  { value: 'title:asc', label: 'Title: A to Z' },
];

function formatCurrency(value) {
  const number = Number(value || 0);
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number.isFinite(number) ? number : 0);
}

function clampPage(value) {
  const page = Number(value);
  return Number.isFinite(page) && page > 0 ? page : 1;
}

function parseSort(value) {
  const [sortBy, sortDir] = String(value || 'updated:desc').split(':');
  return {
    sortBy: sortBy || 'updated',
    sortDir: sortDir || 'desc',
  };
}

export default function StorefrontPage({ initialData = null, initialError = '' }) {
  const router = useRouter();
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(initialError);

  const query = useMemo(() => {
    const page = clampPage(router.query.page || 1);
    const pageSize = PAGE_SIZE_OPTIONS.includes(Number(router.query.page_size)) ? Number(router.query.page_size) : 25;
    const search = typeof router.query.search === 'string' ? router.query.search : '';
    const sort = parseSort(router.query.sort || 'updated:desc');
    return { page, pageSize, search, ...sort };
  }, [router.query.page, router.query.page_size, router.query.search, router.query.sort]);

  useEffect(() => {
    if (!router.isReady) return;
    if (initialData && query.page === Number(initialData.page || query.page) && query.pageSize === Number(initialData.page_size || query.pageSize) && query.search === String(router.query.search || '') && query.sortBy === 'updated' && query.sortDir === 'desc') {
      return;
    }
    setLoading(true);
    setError('');
    fetchPublicStorefrontListings({
      page: query.page,
      pageSize: query.pageSize,
      search: query.search || undefined,
      sortBy: query.sortBy,
      sortDir: query.sortDir,
    })
      .then((result) => setData(result))
      .catch((err) => setError(err?.message || 'Storefront could not load.'))
      .finally(() => setLoading(false));
  }, [initialData, query.page, query.pageSize, query.search, query.sortBy, query.sortDir, router.isReady, router.query.search]);

  const total = Number(data?.total || 0);
  const totalPages = Number(data?.total_pages || 1);
  const currentPage = Number(data?.page || query.page || 1);
  const storefrontUrl = 'https://posterpro.sparkleserver.site/posterpro/storefront';

  const updateQuery = (next) => {
    const nextQuery = { ...router.query, ...next };
    Object.keys(nextQuery).forEach((key) => {
      if (nextQuery[key] === '' || nextQuery[key] == null) delete nextQuery[key];
    });
    router.replace({ pathname: router.pathname, query: nextQuery }, undefined, { shallow: true, scroll: false });
  };

  const items = Array.isArray(data?.items) ? data.items : [];

  return (
    <>
      <Head>
        <title>PosterPro Storefront</title>
        <meta
          name="description"
          content="PosterPro storefront catalog showing currently published items across connected marketplaces."
        />
      </Head>

      <main className="min-h-screen bg-gradient-to-b from-slate-50 via-white to-slate-100 px-3 py-4 text-slate-900 md:px-6 lg:px-8">
        <div className="mx-auto max-w-[1600px] space-y-6">
          <section className="overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-[0_20px_60px_rgba(15,23,42,0.08)]">
            <div className="bg-gradient-to-r from-slate-950 via-slate-900 to-blue-900 px-5 py-7 text-white md:px-8">
              <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
                <div className="max-w-3xl">
                  <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-sky-200">
                    <Store size={13} />
                    Public catalog
                  </div>
                  <h1 className="mt-4 text-3xl font-semibold tracking-[-0.04em] md:text-5xl">
                    Current published catalog
                  </h1>
                  <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-200 md:text-base">
                    Live published items surfaced from PosterPro’s catalog so you can review what is already posted and what is currently visible across connected marketplaces.
                  </p>
                  <div className="mt-5 flex flex-wrap gap-2">
                    <span className="rounded-full border border-white/10 bg-white/10 px-3 py-1 text-xs font-medium text-slate-100">
                      25-item grid per page
                    </span>
                    <span className="rounded-full border border-white/10 bg-white/10 px-3 py-1 text-xs font-medium text-slate-100">
                      Shareable public link
                    </span>
                    <span className="rounded-full border border-white/10 bg-white/10 px-3 py-1 text-xs font-medium text-slate-100">
                      Fast thumbnail catalog
                    </span>
                    <span className="rounded-full border border-white/10 bg-white/10 px-3 py-1 text-xs font-medium text-slate-100">
                      Listings without photos are hidden
                    </span>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3 text-sm md:min-w-[420px]">
                  <div className="rounded-[20px] border border-white/15 bg-white/10 p-4 backdrop-blur">
                    <p className="text-xs uppercase tracking-[0.18em] text-sky-200">Published items</p>
                    <p className="mt-2 text-2xl font-semibold">{total.toLocaleString()}</p>
                  </div>
                  <div className="rounded-[20px] border border-white/15 bg-white/10 p-4 backdrop-blur">
                    <p className="text-xs uppercase tracking-[0.18em] text-sky-200">Page</p>
                    <p className="mt-2 text-2xl font-semibold">{currentPage} / {totalPages}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      if (typeof window === 'undefined' || !window.navigator?.clipboard) return;
                      window.navigator.clipboard.writeText(storefrontUrl).catch(() => undefined);
                    }}
                    className="col-span-2 inline-flex items-center justify-center gap-2 rounded-[20px] border border-white/15 bg-white/10 px-4 py-3 text-sm font-semibold text-white transition hover:bg-white/15"
                  >
                    <Copy size={16} />
                    Copy storefront link
                  </button>
                </div>
              </div>
            </div>

            <div className="space-y-4 px-4 py-4 md:px-6 lg:px-8">
              <div className="rounded-[24px] border border-slate-200 bg-slate-50/80 p-4 shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div className="flex flex-1 flex-col gap-3 md:flex-row md:items-center">
                  <label className="relative flex-1">
                    <span className="sr-only">Search storefront</span>
                    <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
                    <input
                      value={query.search}
                      onChange={(event) => updateQuery({ search: event.target.value, page: 1 })}
                      placeholder="Search published items"
                      className="h-11 w-full rounded-2xl border border-slate-200 bg-white pl-9 pr-4 text-sm shadow-sm outline-none ring-0 transition focus:border-blue-300 focus:ring-4 focus:ring-blue-100"
                    />
                  </label>
                  <select
                    value={`${query.sortBy}:${query.sortDir}`}
                    onChange={(event) => {
                      const [sortBy, sortDir] = event.target.value.split(':');
                      updateQuery({ sort: `${sortBy}:${sortDir}`, page: 1 });
                    }}
                    className="h-11 rounded-2xl border border-slate-200 bg-white px-4 text-sm shadow-sm outline-none transition focus:border-blue-300 focus:ring-4 focus:ring-blue-100"
                  >
                    {SORT_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                    <select
                      value={query.pageSize}
                      onChange={(event) => updateQuery({ page_size: event.target.value, page: 1 })}
                      className="h-11 rounded-2xl border border-slate-200 bg-white px-4 text-sm shadow-sm outline-none transition focus:border-blue-300 focus:ring-4 focus:ring-blue-100"
                    >
                      {PAGE_SIZE_OPTIONS.map((size) => (
                        <option key={size} value={size}>{size} per page</option>
                      ))}
                    </select>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => updateQuery({ page: Math.max(1, currentPage - 1) })}
                      disabled={currentPage <= 1 || loading}
                    >
                      <ChevronLeft size={16} />
                      Previous
                    </Button>
                    <div className="rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700">
                      {currentPage} / {totalPages}
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => updateQuery({ page: Math.min(totalPages, currentPage + 1) })}
                      disabled={currentPage >= totalPages || loading}
                    >
                      Next
                      <ChevronRight size={16} />
                    </Button>
                  </div>
                </div>
              </div>

              {error ? (
                <div className="rounded-[20px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                  {error}
                </div>
              ) : null}

              {loading ? (
                <div className="rounded-[24px] border border-dashed border-slate-300 bg-slate-50 px-6 py-16 text-center text-sm text-slate-500">
                  Loading storefront…
                </div>
              ) : !items.length ? (
                <div className="rounded-[24px] border border-dashed border-slate-300 bg-slate-50 px-6 py-16 text-center text-sm text-slate-500">
                  No published listings found for this view.
                </div>
              ) : (
                <>
                  <div className="grid gap-2.5 [grid-template-columns:repeat(auto-fill,minmax(140px,1fr))]">
                    {items.map((listing) => {
                      const imagePath = listing.thumbnail_url || listing.image_urls?.[0] || '';
                      return (
                      <article key={listing.id} className="overflow-hidden rounded-[16px] border border-slate-200 bg-white shadow-[0_8px_20px_rgba(15,23,42,0.05)] transition hover:-translate-y-0.5 hover:shadow-[0_14px_28px_rgba(15,23,42,0.08)]">
                        <div className="aspect-square bg-slate-100">
                          {imagePath ? (
                            <img
                              src={toThumbnailImageUrl(imagePath, 180, 180)}
                              alt={listing.title}
                              loading="lazy"
                              decoding="async"
                              className="h-full w-full object-cover"
                            />
                          ) : (
                            <div className="flex h-full items-center justify-center text-sm text-slate-500">
                              No image
                            </div>
                          )}
                        </div>
                        <div className="space-y-1 p-2.5">
                          <div className="space-y-1">
                            <p className="line-clamp-2 text-[12px] font-semibold leading-4 text-slate-950">{listing.title}</p>
                            <p className="text-[13px] font-semibold text-slate-900">{formatCurrency(listing.price)}</p>
                          </div>
                          <p className="line-clamp-1 text-[10px] leading-4 text-slate-600">
                            {listing.marketplaces?.length ? listing.marketplaces.join(' · ') : 'published'}
                          </p>
                          <div className="flex flex-wrap gap-1">
                            {(listing.marketplaces || []).map((marketplace) => (
                              <span key={`${listing.id}-${marketplace}`} className="inline-flex items-center rounded-full bg-slate-100 px-1.5 py-0.5 text-[9px] font-medium text-slate-700">
                                {marketplace}
                              </span>
                            ))}
                            {!listing.marketplaces?.length ? (
                              <span className="inline-flex items-center rounded-full bg-slate-100 px-1.5 py-0.5 text-[9px] font-medium text-slate-700">
                                live
                              </span>
                            ) : null}
                          </div>
                          <div className="flex items-center justify-between gap-2 pt-0.5">
                            <div className="text-[9px] text-slate-500">
                              {listing.quantity > 1 ? `${listing.quantity} available` : 'Single item'}
                            </div>
                            {listing.listing_url ? (
                              <a
                                href={listing.listing_url}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center gap-1 rounded-full bg-blue-600 px-2 py-1 text-[9px] font-semibold text-white transition hover:bg-blue-700"
                              >
                                Open live listing
                                <ExternalLink size={12} />
                              </a>
                            ) : (
                              <span className="text-[9px] font-medium text-slate-500">Catalog view</span>
                            )}
                          </div>
                        </div>
                      </article>
                      );
                    })}
                  </div>

                  <div className="flex flex-col gap-3 border-t border-slate-200 pt-4 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-sm text-slate-600">
                      Showing {items.length.toLocaleString()} of {total.toLocaleString()} published listings.
                    </p>
                    <div className="flex items-center gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => updateQuery({ page: Math.max(1, currentPage - 1) })}
                        disabled={currentPage <= 1 || loading}
                      >
                        <ChevronLeft size={16} />
                        Previous
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => updateQuery({ page: Math.min(totalPages, currentPage + 1) })}
                        disabled={currentPage >= totalPages || loading}
                      >
                        Next
                        <ChevronRight size={16} />
                      </Button>
                    </div>
                  </div>
                </>
              )}
            </div>
          </section>
        </div>
      </main>
    </>
  );
}

export async function getServerSideProps(context) {
  const page = clampPage(context.query.page || 1);
  const pageSize = PAGE_SIZE_OPTIONS.includes(Number(context.query.page_size)) ? Number(context.query.page_size) : 25;
  const search = typeof context.query.search === 'string' ? context.query.search : '';
  const { sortBy, sortDir } = parseSort(context.query.sort || 'updated:desc');
  const params = new URLSearchParams();
  params.set('page', String(page));
  params.set('page_size', String(pageSize));
  if (search) params.set('search', search);
  params.set('sort_by', sortBy);
  params.set('sort_dir', sortDir);

  try {
    const apiBase = process.env.POSTERPRO_INTERNAL_API_BASE || 'http://127.0.0.1:8030';
    const response = await fetch(`${apiBase}/public/storefront/listings?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`Storefront API failed (${response.status})`);
    }
    const initialData = await response.json();
    return { props: { initialData, initialError: '' } };
  } catch (error) {
    return {
      props: {
        initialData: null,
        initialError: error?.message || 'Storefront could not load.',
      },
    };
  }
}
