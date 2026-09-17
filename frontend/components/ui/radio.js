import { cn } from '../../lib/utils';

/** Accessible radio choice rendered as a consistent product card. */
export default function Radio({ label, description, selected = false, className, ...props }) {
  return (
    <label className={cn(
      'flex cursor-pointer items-start gap-3 rounded-xl border bg-white p-4 transition hover:border-[var(--pp-accent)]',
      selected ? 'border-[var(--pp-accent)] bg-[var(--pp-primary-soft)] ring-2 ring-[var(--pp-focus-ring)]/20' : 'border-[var(--pp-border)]',
      className,
    )}>
      <input {...props} type="radio" checked={selected} className="mt-1 h-4 w-4 accent-[var(--pp-accent)]" />
      <span className="min-w-0">
        {label ? <span className="block text-sm font-semibold text-[var(--pp-text)]">{label}</span> : null}
        {description ? <span className="mt-1 block text-sm leading-5 text-[var(--pp-muted)]">{description}</span> : null}
      </span>
    </label>
  );
}
