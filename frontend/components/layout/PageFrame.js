import { cn } from '../../lib/utils';

export function PageFrame({ children, className }) {
  return <div className={cn('space-y-6', className)}>{children}</div>;
}

export function PageSplit({
  children,
  className,
  columnsClassName = 'xl:grid-cols-[minmax(0,1fr)_360px]',
  gapClassName = 'gap-6',
}) {
  return <div className={cn('grid items-start', gapClassName, columnsClassName, className)}>{children}</div>;
}

export function PageMain({ children, className }) {
  return <div className={cn('min-w-0 space-y-6', className)}>{children}</div>;
}

export function PageAside({ children, className, sticky = true, stickyTopClassName = 'xl:top-[96px]' }) {
  return (
    <aside className={cn('min-w-0 space-y-6', sticky && 'xl:sticky', sticky && stickyTopClassName, className)}>
      {children}
    </aside>
  );
}

export function PageBand({ children, className }) {
  return (
    <section
      className={cn(
        'rounded-[24px] border border-[var(--pp-border)] bg-[var(--pp-surface)] px-4 py-4 shadow-[0_12px_30px_rgba(15,23,42,0.04)] sm:px-5',
        className,
      )}
    >
      {children}
    </section>
  );
}
