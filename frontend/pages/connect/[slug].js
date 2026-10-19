import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';

import HostedPageShell from '../../components/HostedPageShell';
import { fetchPublicSitePage } from '../../lib/api';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';

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
    <HostedPageShell
      page={page}
      title={title}
      brandName={brandName}
      statusTone={status.state === 'error' ? 'danger' : status.state}
      statusMessage={status.message}
      primaryHref="/settings?tab=ebay"
      primaryLabel="Return to settings"
    >
      {page?.html ? <div className="prose prose-slate max-w-none text-sm leading-7 text-[#344054]" dangerouslySetInnerHTML={{ __html: page.html }} /> : null}
    </HostedPageShell>
  );
}
