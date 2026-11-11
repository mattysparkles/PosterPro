import { cn } from '../../lib/utils';
import Link from 'next/link';

export default function MetricCard({ label, value, detail, className, onClick, href, ...props }) {
  const interactiveProps = onClick
    ? {
        role: 'button',
        tabIndex: 0,
        onKeyDown: (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onClick(event);
          }
        },
      }
    : {};
  const classes = cn('pp-card pp-metric-card min-h-[122px] p-4 sm:p-5', onClick || href ? 'cursor-pointer transition hover:-translate-y-0.5 hover:shadow-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500' : '', className);
  const content = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[13px] font-semibold uppercase tracking-[0.08em] text-[var(--pp-muted)]">{label}</p>
        <span className="pp-metric-card__dot" aria-hidden="true" />
      </div>
      <p className="font-[var(--pp-heading-font)] mt-3 text-[32px] font-semibold leading-none tracking-[-0.04em] text-[var(--pp-text)]">{value}</p>
      {detail ? <p className="mt-2 text-sm leading-5 text-[var(--pp-muted)]">{detail}</p> : null}
    </>
  );
  if (href) return <Link href={href} className={classes} aria-label={`${label}: ${value}. ${detail || ''}`} {...props}>{content}</Link>;
  return <div className={classes} onClick={onClick} {...interactiveProps} {...props}>{content}</div>;
}
