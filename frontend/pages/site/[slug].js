import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';

import HostedPageShell from '../../components/HostedPageShell';
import { fetchPublicSitePage } from '../../lib/api';

export default function HostedSitePage() {
  const router = useRouter();
  const [page, setPage] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const slug = typeof router.query.slug === 'string' ? router.query.slug : '';
    if (!slug) return;
    fetchPublicSitePage(slug)
      .then(setPage)
      .catch((err) => setError(err.message));
  }, [router.query.slug]);

  if (error) {
    return (
      <HostedPageShell title="Page not found" brandName="PosterPro" statusTone="danger" statusMessage={error} primaryHref="/">
        <p className="text-sm text-[#475467]">{error}</p>
      </HostedPageShell>
    );
  }

  if (!page) {
    return (
      <HostedPageShell title="Loading" brandName="PosterPro" statusMessage="Loading page…" primaryHref="/">
        <p className="text-sm text-[#475467]">Loading page…</p>
      </HostedPageShell>
    );
  }

  return (
    <HostedPageShell page={page} title={page.title} brandName={page.brand_name} primaryHref="/">
      <div className="prose prose-slate max-w-none text-sm leading-7 text-[#344054]" dangerouslySetInnerHTML={{ __html: page.html }} />
    </HostedPageShell>
  );
}
