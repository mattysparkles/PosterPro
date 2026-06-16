import Link from 'next/link';

import { cn } from '../../lib/utils';

export default function QuickActionCard({ href, icon: Icon, eyebrow, title, description, meta, className }) {
  return (
    <Link
      href={href}
      className={cn(
        'group block rounded-[18px] border border-[var(--pp-border)] bg-white p-4 transition hover:-translate-y-[1px] hover:border-[#c6d6f5] hover:bg-[#f8fbff] hover:shadow-[0_10px_30px_rgba(15,23,42,0.06)]',
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="inline-flex h-11 w-11 items-center justify-center rounded-[14px] bg-[#edf4ff] text-[var(--pp-primary)]">
          {Icon ? <Icon size={18} /> : null}
        </div>
        {meta ? <span className="rounded-full bg-[#f3f6fb] px-2.5 py-1 text-[11px] font-semibold text-[var(--pp-muted)]">{meta}</span> : null}
      </div>
      {eyebrow ? <p className="mt-4 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--pp-shell-soft-copy)]">{eyebrow}</p> : null}
      <p className="font-[var(--pp-heading-font)] mt-2 text-sm font-semibold text-[var(--pp-text)]">{title}</p>
      <p className="mt-1 text-sm leading-6 text-[var(--pp-muted)]">{description}</p>
    </Link>
  );
}
