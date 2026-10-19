import test from 'node:test';
import assert from 'node:assert/strict';

import { buildEbayOperationsPreviewCsv, formatDomesticServiceOptions } from './ebayOperationsReport.mjs';

test('shipping/handling preview CSV carries proposal blockers and escapes formulas', () => {
  const csv = buildEbayOperationsPreviewCsv({
    shipping_rows: [{
      remote_listing_id: '123', listing_id: 7, title: '=HYPERLINK("https://evil.invalid")', current_price: 19.99,
      current_shipping: { buyer_pays_shipping: false, free_shipping: true, calculated: true, carrier: 'USPS', carrier_service: 'USPSParcel', available_services: [
        { carrier: 'USPS', service: 'USPSParcel' }, { carrier: 'UPS', service: 'Ground' },
      ] },
      proposal: { requested: 'buyer_paid_calculated_usps_ground_advantage', fulfillment_policy_id: 'policy-1', active_listings_using_policy_in_snapshot: 3 },
      price_evidence: { provenance: 'fallback_estimate', recommendation_eligible: false, local_approved_price: 21, remote_price_matches_local: false },
      profitability: { estimated_net_proceeds_before_shipping_label: 17.2, estimated_profit: null, profit_estimate_complete: false },
      parcel: { ready: false, missing_fields: ['measured package weight', 'dimensions'] },
      blockers: ['Packed measurements are missing, dimensions'],
    }],
    handling_rows: [{
      remote_listing_id: '123', listing_id: 7, current_handling_time_days: 0,
      requested_additional_business_days: 1, proposed_handling_time_days: 1,
      exact_increment_supported: true, resulting_handling_time_days: 1,
      fulfillment_policy_id: 'policy-1', active_listings_using_policy_in_snapshot: 3,
      blockers: ['Policy is shared'],
    }],
  });

  assert.match(csv, /^\uFEFF"remote_listing_id"/);
  assert.match(csv, /"'=HYPERLINK\(""https:\/\/evil\.invalid""\)"/);
  assert.match(csv, /"measured package weight; dimensions"/);
  assert.match(csv, /"0","1","1","true","1"/);
  assert.match(csv, /"Packed measurements are missing, dimensions"/);
  assert.match(csv, /"Policy is shared"/);
  assert.match(csv, /"fallback_estimate"/);
  assert.match(csv, /"17\.2"/);
  assert.match(csv, /"false"/);
  assert.match(csv, /"USPS","USPSParcel","USPS USPSParcel; UPS Ground"/);
});

test('domestic service formatter displays only explicit multi-service domestic options', () => {
  assert.equal(formatDomesticServiceOptions([{ carrier: 'USPS', service: 'GroundAdvantage' }]), '');
  assert.equal(formatDomesticServiceOptions([
    { carrier: 'USPS', service: 'GroundAdvantage' },
    { carrier: 'UPS', service: 'Ground' },
    { carrier: 'USPS', service: 'GroundAdvantage' },
  ]), 'Domestic options: USPS GroundAdvantage, UPS Ground');
});
