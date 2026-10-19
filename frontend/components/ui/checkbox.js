import { cn } from '../../lib/utils';

/** Accessible checkbox with a consistent product control surface. */
export default function Checkbox({ label, description, className, ...props }) {
  return (
    <label className={cn('flex cursor-pointer items-start gap-3 rounded-xl border border-[var(--pp-border)] bg-white p-3 transition hover:border-[var(--pp-accent)]', className)}>
      <input {...props} type="checkbox" className="mt-0.5 h-4 w-4 accent-[var(--pp-accent)]" />
      <span className="min-w-0">
        {label ? <span className="block text-sm font-semibold text-[var(--pp-text)]">{label}</span> : null}
        {description ? <span className="mt-0.5 block text-xs leading-5 text-[var(--pp-muted)]">{description}</span> : null}
      </span>
    </label>
  );
}
