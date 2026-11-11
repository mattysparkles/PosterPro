import { cn } from '../../lib/utils';

export default function Select({ className, children, ...props }) {
  return <select className={cn('pp-input pp-input-shell h-11 w-full rounded-2xl bg-white px-3.5 text-sm text-[var(--pp-text)] outline-none transition focus:border-[var(--pp-primary)] focus:ring-4 focus:ring-[var(--pp-focus-ring)]/16', className)} {...props}>{children}</select>;
}
