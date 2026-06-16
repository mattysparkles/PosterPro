import { cn } from '../../lib/utils';

export default function MetricCard({ label, value, detail, className }) {
  return (
    <div className={cn('pp-card min-h-[132px] p-5', className)}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--pp-shell-soft-copy)]">{label}</p>
      <p className="font-[var(--pp-heading-font)] mt-2.5 text-[30px] font-semibold leading-none tracking-[-0.04em] text-[var(--pp-text)]">{value}</p>
      {detail ? <p className="mt-2 text-sm leading-6 text-[var(--pp-muted)]">{detail}</p> : null}
    </div>
  );
}
