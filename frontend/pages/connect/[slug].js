import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';

import { fetchPublicSitePage } from '../../lib/api';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';

function ConnectShell({ title, brandName, children }) {
  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <main className="min-h-screen bg-[radial-gradient(circle_at_top,#e8f1ff_0,#f8fbff_30%,#ffffff_72%)] px-4 py-10 text-[#101828]">
        <div className="mx-auto max-w-[760px] rounded-[28px] border border-[#dfe6f2] bg-white/95 p-6 shadow-[0_24px_80px_rgba(15,23,42,0.08)] backdrop-blur sm:p-8">
          <div className="mb-8 border-b border-[#eaecf0] pb-5">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#667085]">{brandName || 'PosterPro'}</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-[-0.03em] text-[#101828]">{title}</h1>
          </div>
          {children}
        </div>
      </main>
    </>
  );
}

export default function HostedConnectPage() {
  const router = useRouter();
  const [page, setPage] = useState(null);
  const [status, setStatus] = useState({ state: 'loading', message: 'Loading page…' });

  useEffect(() => {
    const slug = typeof router.query.slug === 'string' ? router.query.slug : '';
    if (!slug) return;
    fetchPublicSitePage(slug)
      .then((data) => {
        setPage(data);
        if (data.kind !== 'ebay_auth_accepted') {
          setStatus({ state: 'ready', message: '' });
        }
      })
      .catch((err) => setStatus({ state: 'error', message: err.message }));
  }, [router.query.slug]);

  useEffect(() => {
    if (!page || page.kind !== 'ebay_auth_accepted' || !router.isReady) return;
    const code = typeof router.query.code === 'string' ? router.query.code : '';
    const state = typeof router.query.state === 'string' ? router.query.state : '';
    if (!code || !state) {
      setStatus({ state: 'warning', message: 'The eBay approval page loaded, but the OAuth code and state were missing from the URL.' });
      return;
    }

    const url = new URL(`${API_BASE}/ebay/callback`);
    url.searchParams.set('code', code);
    url.searchParams.set('state', state);

    fetch(url.toString(), { credentials: 'include' })
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || 'Failed to finalize the eBay connection');
        }
        setStatus({ state: 'success', message: 'The eBay account is now connected to PosterPro. This window can be closed.' });
        if (typeof window !== 'undefined' && window.opener && !window.opener.closed) {
          try {
            window.opener.location.reload();
          } catch (_) {
            // Ignore cross-window refresh failures.
          }
        }
      })
      .catch((err) => {
        setStatus({ state: 'error', message: err.message });
      });
  }, [page, router.isReady, router.query.code, router.query.state]);

  const title = page?.title || 'Connection status';
  const brandName = page?.brand_name || 'PosterPro';

  return (
    <ConnectShell title={title} brandName={brandName}>
      <div className="space-y-5">
        {page?.html ? <div className="prose prose-slate max-w-none text-sm leading-7 text-[#344054]" dangerouslySetInnerHTML={{ __html: page.html }} /> : null}
        <div
          className={`rounded-[18px] border px-4 py-4 text-sm ${
            status.state === 'success'
              ? 'border-[#b7e3c0] bg-[#edfdf1] text-[#166534]'
              : status.state === 'error'
              ? 'border-[#f4c7c3] bg-[#fff5f4] text-[#b42318]'
              : status.state === 'warning'
              ? 'border-[#f7d9a4] bg-[#fff8eb] text-[#b54708]'
              : 'border-[#dbe7ff] bg-[#f6f9ff] text-[#1d4ed8]'
          }`}
        >
          {status.message}
        </div>
        <div className="flex flex-wrap gap-3">
          <Link href="/settings?tab=ebay" className="inline-flex items-center justify-center rounded-[12px] bg-[#111827] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#0f172a]">
            Return to settings
          </Link>
          <button
            type="button"
            onClick={() => window.close()}
            className="inline-flex items-center justify-center rounded-[12px] border border-[#d0d5dd] bg-white px-4 py-2 text-sm font-medium text-[#344054] transition hover:bg-[#f8fafc]"
          >
            Close window
          </button>
        </div>
      </div>
    </ConnectShell>
  );
}

