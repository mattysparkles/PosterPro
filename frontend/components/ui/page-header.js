import { cn } from '../../lib/utils';

export default function PageHeader({ title, description, actions, className, eyebrow, breadcrumbs }) {
  return (
    <section className={cn('pp-page-hero overflow-hidden rounded-[30px] border border-[var(--pp-card-border)] p-6 shadow-[0_24px_64px_rgba(15,23,42,0.08)] sm:p-7', className)}>
      {eyebrow ? <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--pp-page-hero-kicker)]">{eyebrow}</p> : null}
      {breadcrumbs?.length ? (
        <nav aria-label="Breadcrumb" className="mt-3 flex flex-wrap items-center gap-2 text-xs text-[var(--pp-page-hero-copy)]">
          {breadcrumbs.map((item, index) => (
            <span key={`${item.label}-${index}`} className="inline-flex items-center gap-2">
              {index > 0 ? <span>/</span> : null}
              <span className={item.active ? 'font-semibold text-[var(--pp-page-hero-title)]' : ''}>{item.label}</span>
            </span>
          ))}
        </nav>
      ) : null}
      <div className="relative mt-4 flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
        <div className="max-w-4xl">
          <h1 className="font-[var(--pp-heading-font)] text-[2.15rem] font-semibold leading-[1.02] tracking-[-0.05em] text-[var(--pp-page-hero-title)] sm:text-[2.45rem]">{title}</h1>
          {description ? <p className="mt-3 max-w-3xl text-sm leading-7 text-[var(--pp-page-hero-copy)] sm:text-[0.98rem]">{description}</p> : null}
        </div>
        {actions ? <div className="pp-page-hero-actions flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </section>
  );
}
