import { cn } from '../../lib/utils';

export function Tabs({ items, value, onChange, className }) {
  return (
    <div className={cn('flex flex-wrap gap-2 rounded-xl border border-[#eaecf0] bg-white p-1.5', className)} role="tablist">
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
              'inline-flex h-9 items-center rounded-lg px-3.5 text-sm font-medium transition',
              active ? 'bg-[#eff4ff] text-[#175cd3]' : 'text-[#475467] hover:bg-[#f2f4f7] hover:text-[#101828]',
            )}
          >
            {item.label}
            {item.count !== undefined ? <span className="ml-2 rounded-md bg-white/80 px-1.5 py-0.5 text-[11px] font-semibold text-[#667085]">{item.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
