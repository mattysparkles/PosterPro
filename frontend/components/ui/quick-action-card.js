import Link from 'next/link';

import { cn } from '../../lib/utils';

export default function QuickActionCard({ href, icon: Icon, eyebrow, title, description, meta, className }) {
  return (
    <Link
      href={href}
      className={cn(
        'group block rounded-[16px] border border-[#e5e7eb] bg-white p-4 transition hover:-translate-y-[1px] hover:border-[#bfd2ff] hover:bg-[#f8fbff]',
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="inline-flex h-11 w-11 items-center justify-center rounded-[14px] bg-[#eef4ff] text-[#2563eb]">
          {Icon ? <Icon size={18} /> : null}
        </div>
        {meta ? <span className="rounded-full bg-[#f2f4f7] px-2.5 py-1 text-[11px] font-semibold text-[#475467]">{meta}</span> : null}
      </div>
      {eyebrow ? <p className="mt-4 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#667085]">{eyebrow}</p> : null}
      <p className="mt-2 text-sm font-semibold text-[#101828]">{title}</p>
      <p className="mt-1 text-sm leading-6 text-[#667085]">{description}</p>
    </Link>
  );
}
