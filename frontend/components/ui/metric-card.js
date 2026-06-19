import { cn } from '../../lib/utils';

export default function MetricCard({ label, value, detail, className }) {
  return (
    <div className={cn('pp-card pp-metric-card min-h-[146px] p-5', className)}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--pp-muted)]">{label}</p>
        <span className="pp-metric-card__dot" aria-hidden="true" />
      </div>
      <p className="font-[var(--pp-heading-font)] mt-4 text-[34px] font-semibold leading-none tracking-[-0.05em] text-[var(--pp-text)]">{value}</p>
      {detail ? <p className="mt-3 text-sm leading-6 text-[var(--pp-muted)]">{detail}</p> : null}
    </div>
  );
}
