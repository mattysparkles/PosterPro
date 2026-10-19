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
      <div className="pp-auth-shell mx-auto flex min-h-[calc(100vh-4rem)] w-full items-center">
        <div
          className="pp-auth-card w-full overflow-hidden rounded-2xl border shadow-[0_12px_36px_rgba(15,23,42,0.10)]"
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

          <div className="grid gap-0">
            <aside className="hidden">
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

            <section className="bg-white px-5 py-6 sm:px-7 sm:py-8">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em]" style={{ color: '#667085' }}>Sign in</p>
                <h2 className="mt-3 font-[var(--pp-heading-font)] text-3xl font-semibold tracking-[-0.05em]" style={{ color: '#142033' }}>{title}</h2>
                {subtitle ? <p className="mt-3 text-sm leading-6" style={{ color: '#57657a' }}>{subtitle}</p> : null}
              </div>

              <div className="mt-5">
                {children}
              </div>
            </section>
          </div>
        </div>
      </div>
      </div>
    </>
  );
}
