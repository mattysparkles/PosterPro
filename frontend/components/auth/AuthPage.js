import Link from 'next/link';
import { ArrowRight, Layers3, Package, Send, Sparkles } from 'lucide-react';

const HIGHLIGHTS = [
  {
    icon: Layers3,
    title: 'Draft queue',
    body: 'Keep each item attached to its photos, notes, and state before it reaches a marketplace.',
  },
  {
    icon: Package,
    title: 'Inventory control',
    body: 'Track active stock, sold items, and archived rows from one place.',
  },
  {
    icon: Send,
    title: 'Marketplace publishing',
    body: 'Work through eBay publishing and assisted handoff without extra clutter.',
  },
];

export default function AuthPage({ title, subtitle, children }) {
  return (
    <div className="min-h-screen bg-white px-4 py-6 text-[var(--pp-text)] sm:px-6 lg:px-8 lg:py-8">
      <div className="mx-auto max-w-5xl">
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_420px] lg:items-start">
          <section className="rounded-[24px] border border-[var(--pp-border)] bg-white p-6 shadow-[0_1px_2px_rgba(15,23,42,0.05)] sm:p-8">
            <Link href="/" className="inline-flex items-center gap-2 text-sm font-semibold text-[var(--pp-primary)]">
              <Sparkles size={14} />
              PosterPro
            </Link>
            <div className="mt-6 space-y-3">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--pp-shell-soft-copy)]">Reseller operations workspace</p>
              <h1 className="font-[var(--pp-heading-font)] text-4xl font-semibold tracking-[-0.05em] text-[var(--pp-text)] sm:text-5xl">
                One system for intake, drafts, publishing, and sold tracking.
              </h1>
              <p className="max-w-2xl text-base leading-7 text-[var(--pp-muted)] sm:text-lg">
                PosterPro keeps marketplace work in one control room so operators can move from photos to drafts to ready-to-publish listings without hunting through a maze of controls.
              </p>
            </div>

            <div className="mt-6 flex flex-wrap gap-2">
              <span className="inline-flex items-center gap-2 rounded-full border border-[var(--pp-border)] bg-[var(--pp-shell-hover)] px-3 py-1.5 text-sm text-[var(--pp-muted)]">
                <ArrowRight size={14} className="text-[var(--pp-primary)]" />
                eBay-first publishing
              </span>
              <span className="inline-flex items-center gap-2 rounded-full border border-[var(--pp-border)] bg-[var(--pp-shell-hover)] px-3 py-1.5 text-sm text-[var(--pp-muted)]">
                <ArrowRight size={14} className="text-[var(--pp-primary)]" />
                Assisted marketplaces supported
              </span>
            </div>

            <div className="mt-8 grid gap-3 sm:grid-cols-3">
              {HIGHLIGHTS.map((item) => {
                const Icon = item.icon;
                return (
                  <article key={item.title} className="rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-shell-hover)] p-4">
                    <div className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-white text-[var(--pp-primary)]">
                      <Icon size={18} />
                    </div>
                    <h2 className="font-[var(--pp-heading-font)] mt-4 text-sm font-semibold text-[var(--pp-text)]">{item.title}</h2>
                    <p className="mt-2 text-sm leading-6 text-[var(--pp-muted)]">{item.body}</p>
                  </article>
                );
              })}
            </div>
          </section>

          <section className="rounded-[24px] border border-[var(--pp-border)] bg-[#f8fafc] p-6 shadow-[0_1px_2px_rgba(15,23,42,0.05)] sm:p-8">
            <div className="max-w-md">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--pp-shell-soft-copy)]">Sign in</p>
              <h2 className="font-[var(--pp-heading-font)] mt-3 text-3xl font-semibold tracking-[-0.04em] text-[var(--pp-text)]">{title}</h2>
              {subtitle ? <p className="mt-3 text-sm leading-6 text-[var(--pp-muted)]">{subtitle}</p> : null}
            </div>

            <div className="mt-6">{children}</div>
          </section>
        </div>
      </div>
    </div>
  );
}
