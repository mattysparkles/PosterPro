import { Tabs } from '../../ui/tabs';

export default function ListingsQueueTabs({
  listingTabs,
  tabCounts,
  activeTab,
  selectTab,
  sourceOptions = [],
  sourceFilter = 'all',
  selectSource,
}) {
  return (
    <section aria-label="Listing queues">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-[#101828]">Listing Queues</h2>
        <p className="text-xs text-[#667085]">Compose source filters with lifecycle queues.</p>
      </div>
      {sourceOptions.length ? (
        <div className="mb-3 rounded-[18px] border border-[#dbe5f5] bg-[#f8fbff] px-3 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#667085]">Source</span>
            {sourceOptions.map((option) => {
              const active = sourceFilter === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => selectSource?.(option.value)}
                  className={[
                    'rounded-full border px-3 py-1.5 text-xs font-semibold transition',
                    active
                      ? 'border-[#bfd4ef] bg-white text-[#2563eb] shadow-[0_10px_20px_rgba(37,99,235,0.08)]'
                      : 'border-transparent bg-white/75 text-[#475467] hover:border-[#dbe5f5] hover:bg-white',
                  ].join(' ')}
                  aria-pressed={active}
                  title={option.description || option.label}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
      <Tabs
        className="hidden md:flex"
        items={listingTabs.map((tab) => ({ ...tab, count: tabCounts[tab.value] || 0, title: tab.description }))}
        value={activeTab}
        onChange={selectTab}
      />
    </section>
  );
}
