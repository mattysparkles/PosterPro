import { Tabs } from '../../ui/tabs';

export default function ListingsQueueTabs({ listingTabs, tabCounts, activeTab, selectTab }) {
  return (
    <section aria-label="Listing queues">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-[#101828]">Listing Queues</h2>
        <p className="text-xs text-[#667085]">Review, draft, ready, published, failed, and Vine workflows.</p>
      </div>
      <Tabs
        className="hidden md:flex"
        items={listingTabs.map((tab) => ({ ...tab, count: tabCounts[tab.value] || 0 }))}
        value={activeTab}
        onChange={selectTab}
      />
    </section>
  );
}
