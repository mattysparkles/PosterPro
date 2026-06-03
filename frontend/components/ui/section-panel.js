import { cn } from '../../lib/utils';

export default function SectionPanel({ title, description, action, children, className, noPadding = false, ...props }) {
  return (
    <section className={cn('pp-card', className)} {...props}>
      {(title || description || action) ? (
        <div className={cn('flex items-start justify-between gap-4 border-b border-[#eaecf0] px-5 py-4', noPadding ? '' : '')}>
          <div>
            {title ? <h2 className="text-base font-semibold text-[#101828]">{title}</h2> : null}
            {description ? <p className="mt-1 text-sm text-[#667085]">{description}</p> : null}
          </div>
          {action ? <div>{action}</div> : null}
        </div>
      ) : null}
      <div className={cn(noPadding ? '' : 'p-5', title || description || action ? '' : 'p-5')}>{children}</div>
    </section>
  );
}
