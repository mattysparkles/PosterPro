import { cn } from '../../lib/utils';

export default function Input({ className, ...props }) {
  return (
    <input
      className={cn(
        'pp-input pp-input-shell h-11 w-full rounded-2xl px-3.5 text-sm text-[var(--pp-text)] outline-none transition placeholder:text-[var(--pp-muted)] focus:border-[var(--pp-primary)] focus:ring-4 focus:ring-[var(--pp-focus-ring)]/16',
        className,
      )}
      {...props}
    />
  );
}
