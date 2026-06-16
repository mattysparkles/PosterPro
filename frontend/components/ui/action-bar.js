import { cn } from '../../lib/utils';

export default function ActionBar({ className, left, right }) {
  return (
    <div className={cn('flex flex-col gap-3 rounded-2xl border border-[var(--pp-border)] bg-white px-4 py-3 md:flex-row md:items-center md:justify-between', className)}>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{left}</div>
      {right ? <div className="flex shrink-0 items-center gap-2 text-sm text-[var(--pp-muted)]">{right}</div> : null}
    </div>
  );
}
