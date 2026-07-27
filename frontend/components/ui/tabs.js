import { cn } from '../../lib/utils';

export function Tabs({ items, value, onChange, className }) {
  return (
    <div className={cn('pp-tab-shell flex flex-wrap gap-2 p-2', className)} role="tablist">
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(item.value)}
            className={cn(
              'pp-tab-item inline-flex h-10 items-center px-4 text-sm font-semibold transition',
              active
                ? 'is-active bg-[var(--pp-primary)] text-white shadow-[0_10px_22px_rgba(23,58,99,0.18)]'
                : 'text-[var(--pp-muted)] hover:bg-white hover:text-[var(--pp-text)]',
            )}
          >
            {item.label}
            {item.count !== undefined ? (
              <span className={cn(
                'ml-2 rounded-full px-2 py-0.5 text-[11px] font-semibold',
                active ? 'bg-white/18 text-white' : 'bg-[var(--pp-surface-strong)] text-[var(--pp-muted)]',
              )}
              >
                {item.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
