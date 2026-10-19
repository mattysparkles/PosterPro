import MetricCard from '../../ui/metric-card';
import SectionPanel from '../../ui/section-panel';

export default function ListingsReportPanel({
  title,
  description,
  metrics,
  items,
  itemKeyPrefix,
  renderMarketStatus,
  renderFooter,
}) {
  if (!items && !metrics) {
    return null;
  }

  return (
    <SectionPanel title={title} description={description}>
      {Array.isArray(metrics) && metrics.length ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {metrics.map((metric) => (
            <MetricCard key={metric.label} label={metric.label} value={metric.value} detail={metric.detail} />
          ))}
        </div>
      ) : null}
      {Array.isArray(items) && items.length ? (
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {items.map((item) => (
            <div key={`${itemKeyPrefix}-${item.listing_id}`} className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
              <p className="text-sm font-semibold text-[#101828]">{item.title || `Listing #${item.listing_id}`}</p>
              <p className="mt-1 text-xs text-[#667085]">#{item.listing_id}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {Object.entries(item.marketplaces || {}).map(([market, data]) => renderMarketStatus(item, market, data))}
              </div>
              {renderFooter ? renderFooter(item) : null}
            </div>
          ))}
        </div>
      ) : null}
    </SectionPanel>
  );
}
