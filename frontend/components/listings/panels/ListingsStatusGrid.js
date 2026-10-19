import MetricCard from '../../ui/metric-card';
import SectionPanel from '../../ui/section-panel';

export default function ListingsStatusGrid({ workflowPreferences, listingMetrics, formatMarketplace }) {
  return (
    <>
      <section id="listing-status" className="grid gap-4 xl:grid-cols-3">
        <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
          <p className="text-sm font-semibold text-[#101828]">Approval mode</p>
          <p className="mt-2 text-sm text-[#667085]">
            {workflowPreferences.review_before_publish
              ? 'Drafts must be approved before they can be published.'
              : 'Direct draft publishing is allowed for this workspace.'}
          </p>
        </div>
        <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
          <p className="text-sm font-semibold text-[#101828]">Bulk review</p>
          <p className="mt-2 text-sm text-[#667085]">
            {workflowPreferences.bulk_approval_enabled
              ? 'Use row selection to approve many reviewed drafts together.'
              : 'Operators should approve listings one at a time.'}
          </p>
        </div>
        <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
          <p className="text-sm font-semibold text-[#101828]">Preview layout</p>
          <p className="mt-2 text-sm text-[#667085]">
            {workflowPreferences.listing_preview_mode === 'marketplace'
              ? 'Review opens in a marketplace-style preview first.'
              : 'Review opens in editor-first mode.'}
          </p>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Draft value" value={`$${Math.round(listingMetrics.draftAmount).toLocaleString()}`} detail={`${listingMetrics.draftCount} drafts pending review/publish`} />
        <MetricCard label="Review queue value" value={`$${Math.round(listingMetrics.reviewAmount).toLocaleString()}`} detail={`${listingMetrics.reviewCount} listings needing approval`} />
        <MetricCard label="Ready value" value={`$${Math.round(listingMetrics.readyAmount).toLocaleString()}`} detail={`${listingMetrics.readyCount} listings ready to publish`} />
        <MetricCard
          label="Marketplace split"
          value={Object.keys(listingMetrics.byMarket).length || 0}
          detail={Object.entries(listingMetrics.byMarket)
            .sort((a, b) => b[1].count - a[1].count)
            .slice(0, 2)
            .map(([market, stats]) => `${formatMarketplace(market)} ${stats.count} · $${Math.round(stats.amount).toLocaleString()}`)
            .join(' | ') || 'No market targets yet'}
        />
      </section>

      <SectionPanel
        title="Listing pipeline triage"
        description="Use these counts to push high-volume draft review toward pricing, shipping, and publish readiness."
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Blocked" value={listingMetrics.blockedCount} detail="Drafts with publish blockers still unresolved." />
          <MetricCard label="Weak pricing" value={listingMetrics.weakPricingCount} detail="Drafts with low confidence or weak comps." />
          <MetricCard label="Stale pricing" value={listingMetrics.stalePricingCount} detail="Drafts that should be repriced before publish." />
          <MetricCard label="Ready for eBay" value={listingMetrics.readyForEbayCount} detail="Drafts with pricing and shipping ready for eBay queueing." />
        </div>
      </SectionPanel>
    </>
  );
}
