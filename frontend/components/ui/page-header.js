import { cn } from '../../lib/utils';

export default function PageHeader({ title, description, actions, className, eyebrow, breadcrumbs }) {
  return (
    <section className={cn('space-y-3', className)}>
      {eyebrow ? <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{eyebrow}</p> : null}
      {breadcrumbs?.length ? (
        <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-2 text-xs text-[#667085]">
          {breadcrumbs.map((item, index) => (
            <span key={`${item.label}-${index}`} className="inline-flex items-center gap-2">
              {index > 0 ? <span>/</span> : null}
              <span className={item.active ? 'font-semibold text-[#101828]' : ''}>{item.label}</span>
            </span>
          ))}
        </nav>
      ) : null}
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="max-w-4xl">
          <h1 className="text-[30px] font-semibold tracking-[-0.03em] text-[#101828]">{title}</h1>
          {description ? <p className="mt-1.5 text-sm leading-6 text-[#667085]">{description}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </section>
  );
}
