import { cn } from '../../lib/utils';

export default function SectionPanel({ title, description, action, children, className, noPadding = false, ...props }) {
  return (
    <section className={cn('pp-card', className)} {...props}>
      {(title || description || action) ? (
        <div className={cn('flex items-start justify-between gap-4 border-b border-[var(--pp-border)] px-5 py-4', noPadding ? '' : '')}>
          <div>
            {title ? <h2 className="font-[var(--pp-heading-font)] text-base font-semibold tracking-[-0.02em] text-[var(--pp-text)]">{title}</h2> : null}
            {description ? <p className="mt-1 text-sm text-[var(--pp-muted)]">{description}</p> : null}
          </div>
          {action ? <div>{action}</div> : null}
        </div>
      ) : null}
      <div className={cn(noPadding ? '' : 'p-5', title || description || action ? '' : 'p-5')}>{children}</div>
    </section>
  );
}
