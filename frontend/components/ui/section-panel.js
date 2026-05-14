import { cn } from '../../lib/utils';

export default function SectionPanel({ title, description, action, children, className }) {
  return (
    <section className={cn('pp-card p-4 md:p-5', className)}>
      {(title || description || action) ? (
        <div className="flex items-start justify-between gap-4 border-b border-[#e5e7eb] pb-4">
          <div>
            {title ? <h2 className="text-base font-semibold text-[#101828]">{title}</h2> : null}
            {description ? <p className="mt-1 text-sm text-[#667085]">{description}</p> : null}
          </div>
          {action ? <div>{action}</div> : null}
        </div>
      ) : null}
      <div className={title || description || action ? 'pt-4' : ''}>{children}</div>
    </section>
  );
}
