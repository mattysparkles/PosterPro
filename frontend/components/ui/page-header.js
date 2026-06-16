import { cn } from '../../lib/utils';

export default function PageHeader({ title, description, actions, className, eyebrow, breadcrumbs }) {
  return (
    <section className={cn('rounded-[28px] border border-[var(--pp-border)] bg-white p-5 shadow-[0_12px_30px_rgba(15,23,42,0.05)] sm:p-6', className)}>
      {eyebrow ? <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--pp-shell-soft-copy)]">{eyebrow}</p> : null}
      {breadcrumbs?.length ? (
        <nav aria-label="Breadcrumb" className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[var(--pp-muted)]">
          {breadcrumbs.map((item, index) => (
            <span key={`${item.label}-${index}`} className="inline-flex items-center gap-2">
              {index > 0 ? <span>/</span> : null}
              <span className={item.active ? 'font-semibold text-[var(--pp-text)]' : ''}>{item.label}</span>
            </span>
          ))}
        </nav>
      ) : null}
      <div className="mt-3 flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="max-w-4xl">
          <h1 className="font-[var(--pp-heading-font)] text-[32px] font-semibold tracking-[-0.05em] text-[var(--pp-text)]">{title}</h1>
          {description ? <p className="mt-2 text-sm leading-6 text-[var(--pp-muted)]">{description}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </section>
  );
}
