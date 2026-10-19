import { cn } from '../../lib/utils';

export default function PageHeader({ title, description, actions, className, compact = false }) {
  return (
    <section className={cn('rounded-[20px] border border-[#e5e7eb] bg-white px-5 py-5 shadow-[0_16px_34px_rgba(15,23,42,0.05)]', compact && 'px-4 py-4', className)}>
      <div className={cn('flex flex-col gap-4', !compact && 'lg:flex-row lg:items-center lg:justify-between')}>
        <div className={cn('min-w-0', compact ? 'max-w-2xl' : 'max-w-3xl')}>
          <h1 className={cn('font-[var(--pp-heading-font)] tracking-[-0.04em] text-[#172033]', compact ? 'text-2xl' : 'text-[1.875rem] leading-[1.1]')}>
            {title}
          </h1>
          {description ? (
            <p className={cn('mt-2 text-[#667085]', compact ? 'text-sm leading-6' : 'text-[0.95rem] leading-6')}>
              {description}
            </p>
          ) : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </section>
  );
}
