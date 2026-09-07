import Head from 'next/head';
import Link from 'next/link';
import { useEffect, useRef } from 'react';
import { CheckCircle2, Layers3, Package, Send, Sparkles } from 'lucide-react';

const HIGHLIGHTS = [
  { icon: Layers3, label: 'Draft queue' },
  { icon: Package, label: 'Inventory control' },
  { icon: Send, label: 'Marketplace publishing' },
];

export default function AuthPage({ title, subtitle, children }) {
  const previousThemeRef = useRef(null);

  useEffect(() => {
    if (typeof document === 'undefined') return undefined;
    const root = document.documentElement;
    previousThemeRef.current = root.getAttribute('data-admin-theme');
    root.removeAttribute('data-admin-theme');
    document.body.style.background = '#f3f7fb';
    document.body.style.color = '#142033';
    return () => {
      if (previousThemeRef.current) {
        root.setAttribute('data-admin-theme', previousThemeRef.current);
      } else {
        root.removeAttribute('data-admin-theme');
      }
      document.body.style.background = '';
      document.body.style.color = '';
    };
  }, []);

  return (
    <>
      <Head>
        <style>{`
          html, body {
            background: #f3f7fb !important;
            color: #142033 !important;
            color-scheme: light;
          }
          body {
            min-height: 100vh;
          }
        `}</style>
      </Head>
      <div
        className="pp-auth-page min-h-screen px-4 py-8 sm:px-6 lg:px-8"
        style={{
          background: 'linear-gradient(180deg, #f6f8fc 0%, #edf2f7 100%)',
          color: '#142033',
        }}
      >
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] w-full max-w-4xl items-center">
        <div
          className="w-full overflow-hidden rounded-[32px] border shadow-[0_24px_80px_rgba(15,23,42,0.12)]"
          style={{
            borderColor: 'rgba(255,255,255,0.8)',
            background: 'rgba(255,255,255,0.94)',
          }}
        >
          <div className="border-b px-6 py-4 sm:px-8" style={{ borderColor: '#e2e8f0', background: '#ffffff' }}>
            <Link href="/" className="inline-flex items-center gap-2 text-sm font-semibold" style={{ color: '#173a63' }}>
              <Sparkles size={14} />
              PosterPro
            </Link>
          </div>

          <div className="grid gap-0 lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)]">
            <aside className="px-6 py-8 sm:px-8 sm:py-10" style={{ background: 'linear-gradient(180deg, #f8fbff 0%, #eef4fb 100%)' }}>
              <div className="inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.2em]" style={{ borderColor: '#dbe7f6', background: '#ffffff', color: '#355782' }}>
                <CheckCircle2 size={13} />
                Secure operator workspace
              </div>

              <h1 className="mt-5 max-w-md font-[var(--pp-heading-font)] text-3xl font-semibold tracking-[-0.06em] sm:text-4xl" style={{ color: '#142033' }}>
                One place for intake, drafts, publishing, and sales.
              </h1>

              <p className="mt-4 max-w-md text-sm leading-6 sm:text-base" style={{ color: '#57657a' }}>
                Sign in to manage listings, queues, marketplace connections, and recovery drafts from one workspace.
              </p>

              <div className="mt-6 grid gap-3">
                {HIGHLIGHTS.map(({ icon: Icon, label }) => (
                  <div key={label} className="flex items-center gap-3 rounded-[18px] border px-4 py-3" style={{ borderColor: '#dde6f0', background: '#ffffff' }}>
                    <span className="inline-flex h-10 w-10 items-center justify-center rounded-2xl" style={{ background: '#dce8f8', color: '#173a63' }}>
                      <Icon size={17} />
                    </span>
                    <div>
                      <p className="text-sm font-semibold" style={{ color: '#142033' }}>{label}</p>
                      <p className="text-xs" style={{ color: '#57657a' }}>Visible status and guarded actions.</p>
                    </div>
                  </div>
                ))}
              </div>
            </aside>

            <section className="bg-white px-6 py-8 sm:px-8 sm:py-10">
              <div className="max-w-md">
                <p className="text-xs font-semibold uppercase tracking-[0.18em]" style={{ color: '#667085' }}>Sign in</p>
                <h2 className="mt-3 font-[var(--pp-heading-font)] text-3xl font-semibold tracking-[-0.05em]" style={{ color: '#142033' }}>{title}</h2>
                {subtitle ? <p className="mt-3 text-sm leading-6" style={{ color: '#57657a' }}>{subtitle}</p> : null}
              </div>

              <div className="mt-6 rounded-[24px] border p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]" style={{ borderColor: '#e2e8f0', background: '#ffffff' }}>
                {children}
              </div>

              <div className="mt-6 flex flex-wrap gap-2 text-xs" style={{ color: '#57657a' }}>
                <span className="rounded-full border px-3 py-1" style={{ borderColor: '#e2e8f0', background: '#f8fafc' }}>Ready</span>
                <span className="rounded-full border px-3 py-1" style={{ borderColor: '#e2e8f0', background: '#f8fafc' }}>Session checks</span>
                <span className="rounded-full border px-3 py-1" style={{ borderColor: '#e2e8f0', background: '#f8fafc' }}>Marketplace access</span>
              </div>

              <p className="mt-5 max-w-md text-xs leading-5" style={{ color: '#57657a' }}>
                Use the account that owns your workspace, marketplace connections, and imports.
              </p>
            </section>
          </div>
        </div>
      </div>
      </div>
    </>
  );
}
