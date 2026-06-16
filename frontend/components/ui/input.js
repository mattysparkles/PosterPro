import { cn } from '../../lib/utils';

export default function Input({ className, ...props }) {
  return (
    <input
      className={cn(
        'pp-input h-11 w-full rounded-xl border border-[var(--pp-border)] bg-white px-3 text-sm text-[var(--pp-text)] outline-none transition placeholder:text-[var(--pp-muted)] focus:border-[var(--pp-primary)] focus:ring-4 focus:ring-[var(--pp-focus-ring)]/20',
        className,
      )}
      {...props}
    />
  );
}
