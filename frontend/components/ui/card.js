import { cn } from '../../lib/utils';

export function Card({ className, ...props }) {
  return <section className={cn('pp-card overflow-hidden p-4 md:p-5', className)} {...props} />;
}

export function CardTitle({ className, ...props }) {
  return <h2 className={cn('text-base font-semibold tracking-[-0.02em] text-[var(--pp-text)]', className)} {...props} />;
}

export function CardDescription({ className, ...props }) {
  return <p className={cn('text-sm leading-6 text-[var(--pp-muted)]', className)} {...props} />;
}
