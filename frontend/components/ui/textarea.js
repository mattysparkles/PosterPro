import { cn } from '../../lib/utils';

export default function Textarea({ className, ...props }) {
  return <textarea className={cn('pp-input pp-input-shell min-h-24 w-full rounded-2xl px-3.5 py-3 text-sm text-[var(--pp-text)] outline-none transition placeholder:text-[var(--pp-muted)] focus:border-[var(--pp-primary)] focus:ring-4 focus:ring-[var(--pp-focus-ring)]/16', className)} {...props} />;
}
