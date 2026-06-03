import { cn } from '../../lib/utils';

export default function SettingsNav({ groups = [], activeTab, onSelect }) {
  return (
    <div className="space-y-4">
      {groups.map((group) => (
        <div key={group.label} className="space-y-1.5">
          <p className="px-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#667085]">{group.label}</p>
          {group.tabs.map((tab) => (
            <button
              key={tab.value}
              type="button"
              onClick={() => onSelect(tab.value)}
              className={cn(
                'w-full rounded-[10px] px-3 py-2 text-left text-sm',
                activeTab === tab.value ? 'bg-[#eef4ff] font-semibold text-[#1d4ed8]' : 'text-[#475467] hover:bg-[#f8fafc]'
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}
