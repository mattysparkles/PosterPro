import { cn } from '../../lib/utils';

export default function SettingsNav({ groups = [], activeTab, onSelect }) {
  return (
    <div className="space-y-4">
      <div className="rounded-3xl border border-[var(--pp-border)] bg-white p-4 shadow-[0_8px_24px_rgba(16,24,40,0.06)]">
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--pp-shell-soft-copy)]">Settings</p>
        <p className="mt-2 text-lg font-semibold tracking-[-0.02em] text-[var(--pp-text)]">Account, channels, and system controls.</p>
        <p className="mt-2 text-sm leading-6 text-[var(--pp-muted)]">Use the left rail to move through setup in a predictable order: profile, marketplace setup, then system tools.</p>
      </div>

      {groups.map((group) => (
        <section key={group.label} className="space-y-2 rounded-3xl border border-[var(--pp-border)] bg-[var(--pp-shell-hover)]/60 p-3">
          <p className="px-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--pp-shell-soft-copy)]">{group.label}</p>
          <div className="space-y-1.5">
            {group.tabs.map((tab) => {
              const selected = activeTab === tab.value;
              return (
                <button
                  key={tab.value}
                  type="button"
                  onClick={() => onSelect(tab.value)}
                  className={cn(
                    'flex w-full items-center justify-between rounded-2xl border px-3 py-3 text-left transition',
                    selected
                      ? 'border-[var(--pp-primary-soft)] bg-[var(--pp-shell-active)] text-[var(--pp-primary)] shadow-[var(--pp-card-shadow)]'
                      : 'border-transparent bg-white text-[var(--pp-shell-copy)] hover:border-[var(--pp-border)] hover:bg-[var(--pp-shell-hover)]'
                  )}
                >
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold">{tab.label}</span>
                    {tab.description ? <span className="mt-1 block text-xs leading-5 text-[var(--pp-muted)]">{tab.description}</span> : null}
                  </span>
                  <span className="ml-3 rounded-full border border-[var(--pp-border)] bg-[var(--pp-surface)] px-2 py-0.5 text-[11px] font-semibold text-[var(--pp-muted)]">
                    {selected ? 'Open' : 'Go'}
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
