import Head from 'next/head';
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';

import { fetchPublicSitePage } from '../../lib/api';

function PublicPageShell({ title, brandName, children }) {
  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <main className="min-h-screen bg-[linear-gradient(180deg,#f8fbff_0%,#ffffff_55%,#f7f8fa_100%)] px-4 py-10 text-[#101828]">
        <div className="mx-auto max-w-[880px] rounded-[28px] border border-[#dfe6f2] bg-white/95 p-6 shadow-[0_24px_80px_rgba(15,23,42,0.08)] backdrop-blur sm:p-8">
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

export default function HostedLegalPage() {
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
      <PublicPageShell title="Page not found" brandName="PosterPro">
        <p className="text-sm text-[#475467]">{error}</p>
      </PublicPageShell>
    );
  }

  if (!page) {
    return (
      <PublicPageShell title="Loading" brandName="PosterPro">
        <p className="text-sm text-[#475467]">Loading page…</p>
      </PublicPageShell>
    );
  }

  return (
    <PublicPageShell title={page.title} brandName={page.brand_name}>
      <div className="prose prose-slate max-w-none text-sm leading-7 text-[#344054]" dangerouslySetInnerHTML={{ __html: page.html }} />
    </PublicPageShell>
  );
}

