import { cn } from '../../lib/utils';

export function Tabs({ items, value, onChange, className }) {
  return (
    <div className={cn('flex flex-wrap gap-2', className)}>
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            onClick={() => onChange(item.value)}
            className={cn(
              'inline-flex h-10 items-center rounded-[10px] border px-4 text-sm font-medium transition-colors',
              active
                ? 'border-[#dbe7ff] bg-[#eef4ff] text-[#2563eb]'
                : 'border-[#e5e7eb] bg-white text-[#475467] hover:bg-[#f9fafb] hover:text-[#101828]',
            )}
          >
            {item.label}
            {item.count !== undefined ? <span className="ml-2 text-xs opacity-80">{item.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
