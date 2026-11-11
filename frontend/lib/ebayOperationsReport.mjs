const columns = [
  'remote_listing_id', 'local_listing_id', 'title', 'current_price',
  'current_buyer_pays_shipping', 'current_free_shipping', 'current_calculated',
  'current_carrier', 'current_service', 'domestic_service_options', 'shipping_requested', 'parcel_ready', 'parcel_missing',
  'price_provenance', 'price_research_date', 'sold_comparable_count', 'active_comparable_count', 'price_evidence_supported', 'local_price', 'remote_price_matches_local',
  'estimated_net_proceeds_before_shipping_label', 'estimated_profit', 'profit_estimate_complete',
  'shipping_policy_id', 'active_policy_references', 'shipping_blockers',
  'handling_current_days', 'handling_additional_days', 'handling_proposed_days',
  'handling_result_supported', 'handling_result_days', 'nearest_supported_alternative_days',
  'handling_policy_blockers',
];

function escapeCell(value) {
  const raw = value == null ? '' : String(value);
  const safe = /^[\s]*[=+@-]/.test(raw) ? `'${raw}` : raw;
  return `"${safe.replaceAll('"', '""')}"`;
}

export function formatDomesticServiceOptions(services) {
  if (!Array.isArray(services) || services.length < 2) return '';
  const labels = [...new Set(services.map((service) => [service?.carrier, service?.service]
    .filter((part) => typeof part === 'string' && part.trim())
    .join(' ')).filter(Boolean))];
  return labels.length > 1 ? `Domestic options: ${labels.join(', ')}` : '';
}

export function buildEbayOperationsPreviewCsv(preview) {
  const handlingById = new Map((preview?.handling_rows || []).map((row) => [String(row.remote_listing_id), row]));
  const shippingById = new Map((preview?.shipping_rows || []).map((row) => [String(row.remote_listing_id), row]));
  const allIds = new Set([...handlingById.keys(), ...shippingById.keys()]);
  const rows = [...allIds].map((id) => {
    const shipping = shippingById.get(id) || {};
    const handling = handlingById.get(id) || {};
    const current = shipping.current_shipping || {};
    const evidence = shipping.price_evidence || {};
    const economics = shipping.profitability || {};
    return [
      id, shipping.listing_id ?? handling.listing_id, shipping.title || handling.title,
      shipping.current_price, current.buyer_pays_shipping, current.free_shipping,
      current.calculated, current.carrier, current.carrier_service,
      (current.available_services || []).map((service) => [service.carrier, service.service].filter(Boolean).join(' ')).join('; '),
      shipping.proposal?.requested,
      shipping.parcel?.ready, (shipping.parcel?.missing_fields || []).join('; '),
      evidence.provenance, evidence.research_date,
      evidence.sold_comparable_count, evidence.active_comparable_count,
      evidence.recommendation_eligible,
      evidence.local_approved_price, evidence.remote_price_matches_local,
      economics.estimated_net_proceeds_before_shipping_label, economics.estimated_profit,
      economics.profit_estimate_complete,
      handling.fulfillment_policy_id || shipping.proposal?.fulfillment_policy_id,
      handling.active_listings_using_policy_in_snapshot ?? shipping.proposal?.active_listings_using_policy_in_snapshot,
      (shipping.blockers || []).join('; '), handling.current_handling_time_days,
      handling.requested_additional_business_days, handling.proposed_handling_time_days,
      handling.exact_increment_supported, handling.resulting_handling_time_days,
      handling.nearest_higher_supported_alternative_days, (handling.blockers || []).join('; '),
    ];
  });
  return `\uFEFF${[columns, ...rows].map((row) => row.map(escapeCell).join(',')).join('\r\n')}`;
}
