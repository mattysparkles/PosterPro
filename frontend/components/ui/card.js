import { cn } from '../../lib/utils';

export function Card({ className, ...props }) {
  return <section className={cn('rounded-3xl border border-border/70 bg-card p-5 shadow-soft', className)} {...props} />;
}

export function CardTitle({ className, ...props }) {
  return <h2 className={cn('text-lg font-semibold text-card-foreground', className)} {...props} />;
}

export function CardDescription({ className, ...props }) {
  return <p className={cn('text-sm text-muted-foreground', className)} {...props} />;
}
