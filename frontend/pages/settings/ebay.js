import { useEffect, useMemo, useState } from 'react';
import { RefreshCcw, Search, ShieldCheck, Store, Wrench } from 'lucide-react';
import toast from 'react-hot-toast';

import AppShell from '../../components/layout/AppShell';
import Button from '../../components/ui/button';
import MetricCard from '../../components/ui/metric-card';
import PageHeader from '../../components/ui/page-header';
import SectionPanel from '../../components/ui/section-panel';
import StatusPill from '../../components/ui/status-pill';
import { buildEbayOperationsPreviewCsv, formatDomesticServiceOptions } from '../../lib/ebayOperationsReport.mjs';
import { useAuth } from '../../contexts/AuthContext';
import { useEbayAuth } from '../../hooks/useEbayAuth';
import {
  createEbayMerchantLocation,
  fetchEbayAccountReadiness,
  fetchEbayPolicies,
  previewEbayShippingHandlingOperations,
  previewFailedEbayBatchRecovery,
  searchFailedEbayBatchCategoryCandidates,
  fetchSettingsPanels,
  syncEbayPolicySettings,
  verifyEbayMerchantLocation,
} from '../../lib/api';

const DEFAULT_LOCATION = {
  merchant_location_key: 'posterpro-default',
  merchant_location_location_name: 'PosterPro Default Location',
  merchant_location_postal_code: '95125',
  merchant_location_country: 'US',
  merchant_location_city: 'San Jose',
  merchant_location_state_or_province: 'CA',
  merchant_location_phone: '',
};

function exportShippingHandlingPreview(preview) {
  const blob = new Blob([buildEbayOperationsPreviewCsv(preview)], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `posterpro-ebay-shipping-handling-preview-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function EbaySettingsPage() {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [settingsPanels, setSettingsPanels] = useState(null);
  const [ebayAccountReadiness, setEbayAccountReadiness] = useState(null);
  const [ebayPolicyCatalog, setEbayPolicyCatalog] = useState(null);
  const [syncingPolicies, setSyncingPolicies] = useState(false);
  const [verifyingLocation, setVerifyingLocation] = useState(false);
  const [creatingLocation, setCreatingLocation] = useState(false);
  const [operationsPreview, setOperationsPreview] = useState(null);
  const [loadingOperationsPreview, setLoadingOperationsPreview] = useState(false);
  const [failedBatchPreview, setFailedBatchPreview] = useState(null);
  const [loadingFailedBatch, setLoadingFailedBatch] = useState(false);
  const [categorySearchQueries, setCategorySearchQueries] = useState({});
  const [categoryCandidateResults, setCategoryCandidateResults] = useState({});
  const [selectedCategoryCandidates, setSelectedCategoryCandidates] = useState({});
  const [loadingCategoryListingId, setLoadingCategoryListingId] = useState(null);

  const reload = async () => {
    if (!user?.id) return;
    setLoading(true);
    try {
      const [panels, readiness, policies] = await Promise.all([
        fetchSettingsPanels(),
        fetchEbayAccountReadiness().catch(() => null),
        fetchEbayPolicies().catch(() => null),
      ]);
      setSettingsPanels(panels);
      setEbayAccountReadiness(readiness);
      setEbayPolicyCatalog(policies);
    } catch (error) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, [user?.id]);

  const ebayConnection = settingsPanels?.ebay || {};
  const ebayRedirectUri = ebayConnection.runame || ebayConnection.redirect_uri;
  const { loading: connecting, error: connectError, connect } = useEbayAuth(user?.id, ebayRedirectUri);
  const readyMetrics = useMemo(
    () => [
      {
        label: 'Connected',
        value: settingsPanels?.ebay?.connected ? 'Yes' : 'No',
        tone: settingsPanels?.ebay?.connected ? 'success' : 'warning',
      },
      {
        label: 'Import ready',
        value: ebayConnection.import_ready ? 'Yes' : 'No',
        tone: ebayConnection.import_ready ? 'success' : 'warning',
      },
      {
        label: 'Publish ready',
        value: ebayAccountReadiness?.publish_ready ? 'Yes' : 'No',
        tone: ebayAccountReadiness?.publish_ready ? 'success' : 'warning',
      },
    ],
    [ebayAccountReadiness?.publish_ready, ebayConnection.import_ready, settingsPanels?.ebay?.connected],
  );

  const syncPolicies = async (createMissingDefaults = false) => {
    setSyncingPolicies(true);
    try {
      const report = await syncEbayPolicySettings({
        marketplace_id: 'EBAY_US',
        create_missing_defaults: createMissingDefaults,
      });
      const syncStatus = String(report?.status || '').toLowerCase();
      if (!['updated', 'synced', 'created_defaults'].includes(syncStatus)) {
        const detail = report?.sync_error || report?.policy_settings?.policy_sync_error || '';
        if (/not eligible for business policy/i.test(detail)) {
          throw new Error('eBay OAuth is connected, but this seller account is not enrolled in eBay Business Policies. Merchant location is separate and may still be verified. Enable Business Policies in eBay Seller Hub, then sync again.');
        }
        throw new Error(detail || `eBay policy sync did not complete (status: ${syncStatus || 'unknown'}).`);
      }
      setEbayPolicyCatalog(report);
      await reload();
      toast.success(createMissingDefaults ? 'Default policies created or synced.' : 'eBay policies synced.');
    } catch (error) {
      toast.error(error.message);
    } finally {
      setSyncingPolicies(false);
    }
  };

  const verifyLocation = async () => {
    setVerifyingLocation(true);
    try {
      const report = await verifyEbayMerchantLocation({ marketplace_id: 'EBAY_US' });
      const locationStatus = String(report?.status || '').toLowerCase();
      if (!['verified', 'created'].includes(locationStatus)) {
        throw new Error(report?.error || report?.settings_updates?.merchant_location_error || `eBay merchant location was not verified (status: ${locationStatus || 'unknown'}).`);
      }
      await reload();
      toast.success('Merchant location verified.');
    } catch (error) {
      toast.error(error.message);
    } finally {
      setVerifyingLocation(false);
    }
  };

  const createLocation = async () => {
    setCreatingLocation(true);
    try {
      const report = await createEbayMerchantLocation({
        marketplace_id: 'EBAY_US',
        ...DEFAULT_LOCATION,
      });
      const locationStatus = String(report?.status || '').toLowerCase();
      if (!['verified', 'created'].includes(locationStatus)) {
        throw new Error(report?.error || report?.settings_updates?.merchant_location_error || `eBay merchant location was not saved (status: ${locationStatus || 'unknown'}).`);
      }
      await reload();
      toast.success('Merchant location saved.');
    } catch (error) {
      toast.error(error.message);
    } finally {
      setCreatingLocation(false);
    }
  };

  const loadOperationsPreview = async () => {
    setLoadingOperationsPreview(true);
    try {
      const preview = await previewEbayShippingHandlingOperations(user?.id);
      setOperationsPreview(preview);
      toast.success('Remote eBay preview refreshed. No listings were changed.');
    } catch (error) {
      toast.error(error.message);
    } finally {
      setLoadingOperationsPreview(false);
    }
  };

  const loadFailedBatchPreview = async () => {
    setLoadingFailedBatch(true);
    try {
      setFailedBatchPreview(await previewFailedEbayBatchRecovery('approve-09dfdc7ccb9d4f6088f0883365ac8c89'));
    } catch (error) {
      toast.error(error.message);
    } finally {
      setLoadingFailedBatch(false);
    }
  };

  const loadCategoryCandidates = async (row) => {
    const listingId = Number(row.listing_id);
    const query = String(categorySearchQueries[listingId] ?? row.title ?? '').trim();
    if (query.length < 2) {
      toast.error('Enter at least two characters to search eBay categories.');
      return;
    }
    setLoadingCategoryListingId(listingId);
    try {
      const result = await searchFailedEbayBatchCategoryCandidates(
        'approve-09dfdc7ccb9d4f6088f0883365ac8c89', listingId, query,
      );
      setCategoryCandidateResults((current) => ({ ...current, [listingId]: result }));
      if (result.suggestions?.length) toast.success('Current eBay taxonomy candidates loaded. No category was changed.');
      else toast.error('No current eBay category candidates were returned.');
    } catch (error) {
      toast.error(error.message);
    } finally {
      setLoadingCategoryListingId(null);
    }
  };

  return (
    <AppShell
      active="/settings"
      title="eBay setup"
      autonomousConfig={null}
    >
      <PageHeader
        title="eBay setup"
        description="Connect the seller account first, then sync policies and verify the merchant location. Keep the advanced settings on the full settings page."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" href="/settings">
              Back to settings
            </Button>
            <Button variant="outline" onClick={reload} disabled={loading || refreshing}>
              <RefreshCcw size={16} />
              Refresh
            </Button>
          </div>
        }
      />

      <div className="mx-auto max-w-[1180px] px-4 pb-12">
        <div className="space-y-6">
          <section className="grid gap-4 md:grid-cols-3">
            {readyMetrics.map((item) => (
              <MetricCard key={item.label} label={item.label} value={item.value} detail="Current eBay readiness state." />
            ))}
          </section>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(320px,0.95fr)]">
            <SectionPanel title="Connect eBay" description="This is the operator action that should be obvious and one-click.">
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill status={settingsPanels?.ebay?.connected ? 'success' : 'warning'} label={settingsPanels?.ebay?.connected ? 'Connected' : 'Not connected'} />
                  <StatusPill status={ebayConnection.import_ready ? 'success' : 'warning'} label={ebayConnection.import_ready ? 'Import ready' : 'Import blocked'} />
                  <StatusPill status={ebayAccountReadiness?.publish_ready ? 'success' : 'warning'} label={ebayAccountReadiness?.publish_ready ? 'Publish ready' : 'Publish blocked'} />
                </div>
                <p className="text-sm leading-6 text-[var(--pp-muted)]">
                  If the eBay account was disconnected or the token expired, use the button below to reconnect the correct seller account.
                </p>
                <Button
                  size="lg"
                  className="w-full"
                  onClick={connect}
                  disabled={connecting || !settingsPanels?.ebay?.oauth_ready}
                >
                  <Store size={16} />
                  {connecting ? 'Opening OAuth...' : settingsPanels?.ebay?.connected ? 'Reconnect eBay now' : 'Connect eBay now'}
                </Button>
                {connectError ? <p className="text-sm text-[var(--pp-danger)]">{connectError}</p> : null}
                {!settingsPanels?.ebay?.oauth_ready ? (
                  <p className="text-sm text-[var(--pp-danger)]">OAuth app credentials are not fully configured yet. Open full settings to finish the server-side eBay app setup.</p>
                ) : null}
              </div>
            </SectionPanel>

            <SectionPanel title="Policy and location" description="Sync policy IDs and verify the merchant location in one place.">
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <MetricCard
                    label="Policy sync"
                    value={ebayAccountReadiness?.policy_sync_status || 'uninitialized'}
                    detail={ebayAccountReadiness?.policy_sync_error || 'Sync the saved policy IDs from eBay.'}
                  />
                  <MetricCard
                    label="Merchant location"
                    value={ebayAccountReadiness?.merchant_location_status || 'unverified'}
                    detail={ebayAccountReadiness?.merchant_location_error || 'Verify the inventory origin key before publishing.'}
                  />
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" onClick={() => syncPolicies(false)} disabled={syncingPolicies}>
                    <Wrench size={16} />
                    {syncingPolicies ? 'Syncing...' : 'Sync policies from eBay'}
                  </Button>
                  <Button variant="secondary" onClick={() => syncPolicies(true)} disabled={syncingPolicies}>
                    Create default policies
                  </Button>
                  <Button variant="outline" onClick={verifyLocation} disabled={verifyingLocation}>
                    <ShieldCheck size={16} />
                    {verifyingLocation ? 'Verifying...' : 'Verify merchant location'}
                  </Button>
                  <Button variant="outline" onClick={createLocation} disabled={creatingLocation}>
                    {creatingLocation ? 'Saving...' : 'Save default location'}
                  </Button>
                </div>
                <div className="rounded-2xl border border-[var(--pp-border)] bg-[var(--pp-surface)] p-4 text-sm text-[var(--pp-muted)]">
                  <p className="font-medium text-[var(--pp-text)]">Current policy IDs</p>
                  <p className="mt-2">Payment: {ebayConnection?.policy_settings?.payment_policy_id || 'Missing'}</p>
                  <p>Fulfillment: {ebayConnection?.policy_settings?.fulfillment_policy_id || 'Missing'}</p>
                  <p>Return: {ebayConnection?.policy_settings?.return_policy_id || 'Missing'}</p>
                  <p className="mt-3 font-medium text-[var(--pp-text)]">Location key</p>
                  <p>{ebayConnection?.policy_settings?.merchant_location_key || 'Missing'}</p>
                  <p className="mt-3 text-xs uppercase tracking-[0.12em] text-[var(--pp-shell-soft-copy)]">Advanced app fields and import controls live on the full settings page.</p>
                </div>
              </div>
            </SectionPanel>
          </div>

          <SectionPanel title="Revenue protection preview" description="Reads active eBay offers and prepares shipping and handling actions. This does not change any remote listing.">
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={loadOperationsPreview} disabled={loadingOperationsPreview || !settingsPanels?.ebay?.connected}>
                  <RefreshCcw size={16} />
                  {loadingOperationsPreview ? 'Reading active offers…' : 'Preview shipping & handling'}
                </Button>
                {operationsPreview ? <Button variant="outline" onClick={() => exportShippingHandlingPreview(operationsPreview)}>Export preview CSV</Button> : null}
                {operationsPreview ? <StatusPill status="warning" label="Approval required before changes" /> : null}
              </div>
              {operationsPreview ? (
                <>
                  <div className="grid gap-3 md:grid-cols-4">
                    <MetricCard label="Remote active offers" value={operationsPreview.remote_active_count} detail="Fresh read-only eBay result." />
                    <MetricCard label="Below policy threshold" value={operationsPreview.under_threshold_count} detail={`Below $${Number(operationsPreview.shipping_policy?.shipping_price_threshold || 25).toFixed(2)}.`} />
                    <MetricCard label="Remote offers without local match" value={operationsPreview.identity_reconciliation?.remote_active_unmapped_count ?? 'Unknown'} detail="Identity must be reconciled before a bulk revision." />
                    <MetricCard label="Handling rows" value={(operationsPreview.handling_rows || []).length} detail="Each proposes one extra business day from its current value." />
                  </div>
                  {operationsPreview.handling_capability?.values_days ? (
                    <p className="rounded-xl border border-[var(--pp-border)] bg-[var(--pp-surface)] p-3 text-sm text-[var(--pp-muted)]">
                      eBay-supported handling values: {operationsPreview.handling_capability.values_days.join(', ')} business days · source {operationsPreview.handling_capability.source || 'provider'}{operationsPreview.handling_capability.updated_at ? ` · capability updated ${operationsPreview.handling_capability.updated_at}` : ''}. Each result is checked against this list.
                    </p>
                  ) : operationsPreview.handling_capability ? (
                    <p role="alert" className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">Marketplace-supported handling values could not be confirmed: {operationsPreview.handling_capability.error || 'unknown provider response'}.</p>
                  ) : null}
                  {operationsPreview.identity_reconciliation?.blocker ? <p className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">{operationsPreview.identity_reconciliation.blocker}</p> : null}
                  <div className="max-h-96 overflow-auto rounded-2xl border border-[var(--pp-border)]">
                    {(operationsPreview.shipping_rows || []).map((row) => (
                      <div key={row.remote_listing_id} className="border-b border-[var(--pp-border)] p-4 last:border-b-0">
                        <p className="font-medium text-[var(--pp-text)]">{row.title || `eBay ${row.remote_listing_id}`} <span className="font-normal text-[var(--pp-muted)]">· item {row.listing_id ?? 'unmapped'} · eBay {row.remote_listing_id}</span></p>
                        <p className="mt-1 text-sm text-[var(--pp-muted)]">${Number(row.current_price || 0).toFixed(2)} · current: {row.current_shipping?.free_shipping ? 'free shipping' : row.current_shipping?.buyer_pays_shipping ? 'buyer pays' : 'seller responsibility unknown'} · {row.current_shipping?.calculated ? 'calculated' : 'not calculated'} · {[row.current_shipping?.carrier, row.current_shipping?.carrier_service].filter(Boolean).join(' ') || 'service not returned'}{formatDomesticServiceOptions(row.current_shipping?.available_services) ? ` · ${formatDomesticServiceOptions(row.current_shipping?.available_services)}` : ''}</p>
                        <p className="mt-1 text-sm text-[var(--pp-muted)]">Requested: buyer-paid calculated USPS Ground Advantage · packed measurements: {row.parcel?.ready ? 'verified' : `missing or unsafe (${(row.parcel?.missing_fields || []).join(', ') || (row.parcel?.eligibility_restrictions || []).join(', ') || 'provenance not verified'})`}</p>
                        <p className="mt-1 text-sm text-[var(--pp-muted)]">Price evidence: {row.price_evidence?.provenance || 'unknown'}{row.price_evidence?.research_date ? ` · researched ${row.price_evidence.research_date}` : ''} · {row.price_evidence?.sold_comparable_count ?? 'unknown'} sold / {row.price_evidence?.active_comparable_count ?? 'unknown'} active asking comps · {row.price_evidence?.recommendation_eligible ? 'supported evidence' : 'not evidenced'}{row.price_evidence?.remote_price_matches_local === false ? ` · local price $${Number(row.price_evidence.local_approved_price || 0).toFixed(2)} differs from remote` : ''}</p>
                        <p className="mt-1 text-sm text-[var(--pp-muted)]">Economics estimate: {row.profitability?.estimated_net_proceeds_before_shipping_label == null ? 'proceeds unavailable' : `$${Number(row.profitability.estimated_net_proceeds_before_shipping_label).toFixed(2)} before shipping-label cost`} · {row.profitability?.estimated_profit == null ? 'profit not established from complete cost data' : `estimated profit $${Number(row.profitability.estimated_profit).toFixed(2)}`}</p>
                        <p className="mt-1 text-sm text-[var(--pp-muted)]">Remote evidence: {row.remote_evidence?.shipping_source || 'unknown'} · inspected {row.remote_evidence?.inspected_at || 'not confirmed'}{row.proposal?.fulfillment_policy_id ? ` · policy ${row.proposal.fulfillment_policy_name || row.proposal.fulfillment_policy_id} (${row.proposal.active_listings_using_policy_in_snapshot} active offer(s) in snapshot)` : ''}</p>
                        <p className="mt-1 text-sm text-[var(--pp-danger)]">{(row.blockers || []).join(' ')}</p>
                        <p className="mt-1 text-xs text-[var(--pp-muted)]">{(row.safe_options || []).join(' · ')}</p>
                        {row.listing_id && row.parcel?.ready !== true ? <Button className="mt-2" href={`/listings/${row.listing_id}`} variant="outline">Open listing to record packed measurements</Button> : null}
                      </div>
                    ))}
                  </div>
                  <div>
                    <h3 className="mb-2 text-sm font-semibold text-[var(--pp-text)]">Handling time · current → proposed</h3>
                    <div className="max-h-96 overflow-auto rounded-2xl border border-[var(--pp-border)]">
                      {(operationsPreview.handling_rows || []).map((row) => (
                        <div key={row.remote_listing_id} className="border-b border-[var(--pp-border)] p-4 last:border-b-0">
                          <p className="font-medium text-[var(--pp-text)]">{row.title || `eBay ${row.remote_listing_id}`} <span className="font-normal text-[var(--pp-muted)]">· eBay {row.remote_listing_id}</span></p>
                          <p className="mt-1 text-sm text-[var(--pp-muted)]">{row.current_handling_time_days == null ? 'Current value unavailable' : `${row.current_handling_time_days} business day(s)`} → {row.exact_increment_supported === true ? `${row.resulting_handling_time_days} business day(s) (supported)` : row.proposed_handling_time_days == null ? 'result unavailable' : `${row.proposed_handling_time_days} proposed, not supported`} · requested +{row.requested_additional_business_days} business day</p>
                          {row.exact_increment_supported === false && row.nearest_higher_supported_alternative_days != null ? <p className="mt-1 text-sm text-amber-800">Nearest higher supported choice: {row.nearest_higher_supported_alternative_days} days; operator choice required.</p> : null}
                          <p className="mt-1 text-xs text-[var(--pp-muted)]">Evidence: {row.evidence?.source || 'unknown'} · {row.fulfillment_policy_name ? `shared policy ${row.fulfillment_policy_name}, ${row.active_listings_using_policy_in_snapshot} active offer(s) in snapshot` : row.shared_policy_impact}</p>
                          <p className="mt-1 text-sm text-[var(--pp-danger)]">{(row.blockers || []).join(' ')}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              ) : <p className="text-sm text-[var(--pp-muted)]">Use the preview to distinguish listings needing measurements, verified calculated-rate candidates, and listings needing a separate deactivation decision.</p>}
            </div>
          </SectionPanel>

          <SectionPanel title="Failed eBay publication batch" description="Read the retained provider errors and current taxonomy/aspect preflight for the known 25-item batch. This report cannot retry or publish listings.">
            <div className="space-y-4">
              <Button variant="outline" onClick={loadFailedBatchPreview} disabled={loadingFailedBatch}>
                <RefreshCcw size={16} />
                {loadingFailedBatch ? 'Reading failure evidence…' : 'Preview failed batch recovery'}
              </Button>
              {failedBatchPreview ? (
                <>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <MetricCard label="Batch state" value={failedBatchPreview.batch_status || 'Unknown'} detail={failedBatchPreview.batch_id} />
                    <MetricCard label="Failed items" value={failedBatchPreview.row_count} detail="Per-item provider response retained." />
                    <MetricCard label="Automatic retries" value="Disabled" detail={failedBatchPreview.execution_blocker} />
                  </div>
                  <div className="max-h-[32rem] overflow-auto rounded-2xl border border-[var(--pp-border)]">
                    {(failedBatchPreview.rows || []).map((row) => (
                      <div key={row.job_id} className="border-b border-[var(--pp-border)] p-4 last:border-b-0">
                        <p className="font-medium text-[var(--pp-text)]">#{row.listing_id} · {row.title || 'Untitled listing'} <span className="font-normal text-[var(--pp-muted)]">· {row.classification} · {row.job_status}</span></p>
                        <p className="mt-1 text-sm text-[var(--pp-danger)]">{row.provider_error_code}: {row.provider_message}</p>
                        <p className="mt-1 text-xs text-[var(--pp-muted)]">Provider request: {row.provider_method || 'method not retained'} {row.provider_endpoint || 'endpoint not retained'}</p>
                        <p className="mt-1 text-sm text-[var(--pp-muted)]">Next step: {row.corrective_action}</p>
                        {row.taxonomy_preflight ? (
                          <div className="mt-2 rounded-xl bg-[var(--pp-surface)] p-3 text-sm text-[var(--pp-muted)]">
                            <p>Current taxonomy check: {row.taxonomy_preflight.status} · category {row.taxonomy_preflight.category_id || 'missing'} · leaf {row.taxonomy_preflight.leaf == null ? 'unknown' : row.taxonomy_preflight.leaf ? 'yes' : 'no'} · required aspects missing: {(row.taxonomy_preflight.missing_required_aspects || []).join(', ') || 'none reported'}</p>
                            <p className="mt-1">Provider-required fields missing: {(row.taxonomy_preflight.provider_required_fields_missing || []).join(', ') || 'none'} · condition validation: {row.taxonomy_preflight.condition_validation || 'not available'}</p>
                            {row.taxonomy_preflight.condition_policy ? <p className="mt-1">Current eBay condition policy: {row.taxonomy_preflight.condition_policy.status} · required: {row.taxonomy_preflight.condition_policy.condition_required == null ? 'unknown' : row.taxonomy_preflight.condition_policy.condition_required ? 'yes' : 'no'} · saved condition: {row.taxonomy_preflight.condition_policy.current_listing_condition || 'not set'}{row.taxonomy_preflight.condition_policy.current_condition_bucket ? ` (${row.taxonomy_preflight.condition_policy.current_condition_bucket})` : ''} · provider choices: {(row.taxonomy_preflight.condition_policy.supported_conditions || []).map((condition) => `${condition.condition_id}: ${condition.description || 'unnamed'}`).join(', ') || 'unavailable'}</p> : null}
                            {row.taxonomy_preflight.error ? <p className="mt-1 text-[var(--pp-danger)]">{row.taxonomy_preflight.error}</p> : null}
                          </div>
                        ) : null}
                        {row.deterministic_offer_reconciliation ? (
                          <div className="mt-2 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
                            <p className="font-medium">Current exact-SKU offer evidence (read-only): {row.deterministic_offer_reconciliation.status}</p>
                            {(row.deterministic_offer_reconciliation.offers || []).map((offer) => (
                              <div key={offer.offer_id || `${offer.marketplace_id}-${offer.remote_category_id}`} className="mt-2 border-t border-amber-200 pt-2">
                                <p>Offer {offer.offer_id || 'ID unavailable'} · {offer.status} · marketplace {offer.marketplace_id} · remote listing {offer.remote_listing_id || 'not linked'}</p>
                                <p className="mt-1">Remote category {offer.remote_category_path || offer.remote_category_id || 'unknown'}{offer.category_mismatch ? ` differs from local draft ${offer.local_category_path || offer.local_category_id}` : ''}</p>
                                <p className="mt-1">Remote item specifics: {(offer.remote_aspect_names || []).join(', ') || 'none returned'} · current remote-category required specifics missing: {(offer.remote_required_aspects_missing || []).join(', ') || (offer.remote_required_aspects_status === 'provider_confirmed' ? 'none reported' : 'not verified')}</p>
                                <p className="mt-1">Local draft specifics: {(offer.local_aspect_names || []).join(', ') || 'none'} · missing locally for the current remote category: {(offer.remote_required_aspects_missing_locally || []).join(', ') || 'none reported'}</p>
                                <p className="mt-1">Remote package fields: {(offer.remote_package_fields_present || []).join(', ') || 'none returned'} · condition: {offer.remote_condition || 'not returned'}</p>
                              </div>
                            ))}
                            {row.deterministic_offer_reconciliation.provider_http_status ? <p className="mt-2">Exact-SKU provider lookup: HTTP {row.deterministic_offer_reconciliation.provider_http_status} · error(s) {(row.deterministic_offer_reconciliation.provider_error_ids || []).join(', ') || 'not supplied'}</p> : null}
                            {(row.deterministic_offer_reconciliation.metadata_errors || []).map((error, index) => <p key={`remote-aspect-error-${index}`} className="mt-1 text-[var(--pp-danger)]">Remote category aspect lookup: {error}</p>)}
                            {row.deterministic_offer_reconciliation.meaning ? <p className="mt-2">{row.deterministic_offer_reconciliation.meaning}</p> : null}
                            {row.deterministic_offer_reconciliation.error ? <p className="mt-2 text-[var(--pp-danger)]">{row.deterministic_offer_reconciliation.error}</p> : null}
                          </div>
                        ) : null}
                        {row.active_offer_reconciliation ? (
                          <p className="mt-1 text-sm text-[var(--pp-muted)]">
                            Active-offer reconciliation: {row.active_offer_reconciliation.status}
                            {row.active_offer_reconciliation.remote_listing_id ? ` · remote listing ${row.active_offer_reconciliation.remote_listing_id}` : ''}
                            {row.active_offer_reconciliation.checked_at ? ` · checked ${row.active_offer_reconciliation.checked_at}` : ''}
                            {row.active_offer_reconciliation.meaning ? ` · ${row.active_offer_reconciliation.meaning}` : ''}
                            {row.active_offer_reconciliation.error ? ` · ${row.active_offer_reconciliation.error}` : ''}
                          </p>
                        ) : null}
                        {row.sku_offer_reconciliation ? (
                          <div className="mt-1 rounded-xl border border-[var(--pp-border)] p-3 text-sm text-[var(--pp-muted)]">
                            <p>Exact-SKU offer lookup: {row.sku_offer_reconciliation.status}
                              {row.sku_offer_reconciliation.provider_http_status ? ` · HTTP ${row.sku_offer_reconciliation.provider_http_status}` : ''}
                              {(row.sku_offer_reconciliation.provider_error_ids || []).length ? ` · provider code(s) ${(row.sku_offer_reconciliation.provider_error_ids || []).join(', ')}` : ''}
                            </p>
                            {(row.sku_offer_reconciliation.offers || []).map((offer) => (
                              <p key={offer.offer_id || `${offer.marketplace_id}-${offer.listing_status}`} className="mt-1">Remote offer {offer.offer_id || 'ID unavailable'} · {offer.listing_status} · listing {offer.listing_id || 'not linked'}</p>
                            ))}
                            {row.sku_offer_reconciliation.meaning ? <p className="mt-1">{row.sku_offer_reconciliation.meaning}</p> : null}
                            {row.sku_offer_reconciliation.error ? <p className="mt-1 text-[var(--pp-danger)]">{row.sku_offer_reconciliation.error}</p> : null}
                          </div>
                        ) : null}
                        {row.inventory_item_reconciliation ? (
                          <div className="mt-1 rounded-xl border border-[var(--pp-border)] p-3 text-sm text-[var(--pp-muted)]">
                            <p>Exact-SKU Inventory item: {row.inventory_item_reconciliation.status}
                              {row.inventory_item_reconciliation.provider_http_status ? ` · HTTP ${row.inventory_item_reconciliation.provider_http_status}` : ''}
                              {(row.inventory_item_reconciliation.provider_error_ids || []).length ? ` · provider code(s) ${(row.inventory_item_reconciliation.provider_error_ids || []).join(', ')}` : ''}
                            </p>
                            {row.inventory_item_reconciliation.remote_title ? <p className="mt-1">Remote item: {row.inventory_item_reconciliation.remote_title} · condition {row.inventory_item_reconciliation.condition || 'unknown'} · aspects {(row.inventory_item_reconciliation.aspect_names || []).join(', ') || 'none'}</p> : null}
                            {row.inventory_item_reconciliation.package_fields_present ? <p className="mt-1">Package fields present: {(row.inventory_item_reconciliation.package_fields_present || []).join(', ') || 'none'}; measurement source remains unverified.</p> : null}
                            {row.inventory_item_reconciliation.meaning ? <p className="mt-1">{row.inventory_item_reconciliation.meaning}</p> : null}
                            {row.inventory_item_reconciliation.error ? <p className="mt-1 text-[var(--pp-danger)]">{row.inventory_item_reconciliation.error}</p> : null}
                          </div>
                        ) : null}
                        {row.local_recovery_preflight ? (
                          <div className="mt-1 rounded-xl border border-[var(--pp-border)] p-3 text-sm text-[var(--pp-muted)]">
                            <p>Current local eBay readiness: {row.local_recovery_preflight.status || 'unknown'} · this is not remote confirmation and does not enable retry.</p>
                            {row.local_recovery_preflight.category_summary ? <p className="mt-1">Saved category: {row.local_recovery_preflight.category_summary.category_path || row.local_recovery_preflight.category_summary.category_name || row.local_recovery_preflight.category_summary.category_id || 'unknown'}{(row.local_recovery_preflight.category_summary.semanticFlags || row.local_recovery_preflight.category_summary.semantic_flags || []).length ? ` · semantic flags: ${(row.local_recovery_preflight.category_summary.semanticFlags || row.local_recovery_preflight.category_summary.semantic_flags || []).join(', ')}` : ''}</p> : null}
                            {(row.local_recovery_preflight.blockers || []).map((issue, index) => (
                              <p key={`${issue.code || 'blocker'}-${index}`} className="mt-1 text-[var(--pp-danger)]">{issue.code || 'BLOCKED'}: {issue.message || 'Readiness blocker'}{issue.fix_hint ? ` · ${issue.fix_hint}` : ''}</p>
                            ))}
                            {(row.local_recovery_preflight.warnings || []).map((issue, index) => (
                              <p key={`${issue.code || 'warning'}-${index}`} className="mt-1">Warning: {issue.message || issue.code}</p>
                            ))}
                            <div className="mt-3 border-t border-[var(--pp-border)] pt-3">
                              <p>Search current eBay taxonomy for a category candidate. Suggestions are evidence only; nothing is automatically selected or applied.</p>
                              <div className="mt-2 flex flex-wrap gap-2">
                                <input
                                  aria-label={`Category search for listing ${row.listing_id}`}
                                  className="min-w-[16rem] flex-1 rounded-lg border border-[var(--pp-border)] bg-[var(--pp-background)] px-3 py-2 text-sm text-[var(--pp-text)]"
                                  value={categorySearchQueries[row.listing_id] ?? row.title ?? ''}
                                  onChange={(event) => setCategorySearchQueries((current) => ({ ...current, [row.listing_id]: event.target.value }))}
                                />
                                <Button variant="outline" onClick={() => loadCategoryCandidates(row)} disabled={loadingCategoryListingId === Number(row.listing_id)}>
                                  <Search size={16} />
                                  {loadingCategoryListingId === Number(row.listing_id) ? 'Searching…' : 'Search taxonomy'}
                                </Button>
                              </div>
                              {(categoryCandidateResults[row.listing_id]?.suggestions || []).map((candidate) => (
                                <div key={candidate.category_id} className="mt-2 rounded-lg bg-[var(--pp-surface)] p-3">
                                  <p className="font-medium text-[var(--pp-text)]">{candidate.category_name} · {candidate.category_id} · {candidate.leaf_verified && candidate.leaf ? 'verified leaf' : 'not verified as a leaf'}</p>
                                  {candidate.breadcrumb ? <p className="mt-1">{candidate.breadcrumb}</p> : null}
                                  <p className="mt-1">Required specifics: {candidate.required_aspects_status || 'not checked'} · missing: {(candidate.missing_required_aspects || []).join(', ') || (candidate.required_aspects_status === 'confirmed' ? 'none' : 'unknown')}</p>
                                  {candidate.condition_policy ? <p className="mt-1">Condition policy: {candidate.condition_policy.status} · condition required: {candidate.condition_policy.condition_required == null ? 'unknown' : candidate.condition_policy.condition_required ? 'yes' : 'no'} · supported: {(candidate.condition_policy.supported_conditions || []).map((condition) => `${condition.condition_id}: ${condition.description || 'unnamed'}`).join(', ') || 'unavailable'}</p> : null}
                                  {candidate.aspect_error ? <p className="mt-1 text-[var(--pp-danger)]">{candidate.aspect_error}</p> : null}
                                  <Button
                                    className="mt-2"
                                    variant="outline"
                                    disabled={candidate.leaf_verified !== true || candidate.leaf !== true || candidate.required_aspects_status !== 'confirmed'}
                                    onClick={() => setSelectedCategoryCandidates((current) => ({ ...current, [row.listing_id]: candidate }))}
                                  >
                                    Preview this category correction
                                  </Button>
                                </div>
                              ))}
                              {selectedCategoryCandidates[row.listing_id] ? (
                                <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
                                  <p className="font-medium">Read-only correction preview · no category changed</p>
                                  <p className="mt-1">Current: {categoryCandidateResults[row.listing_id]?.current_category?.full_path || `category ${categoryCandidateResults[row.listing_id]?.current_category_id || 'unset'}`} → Proposed: {selectedCategoryCandidates[row.listing_id].category_name} ({selectedCategoryCandidates[row.listing_id].category_id})</p>
                                  <p className="mt-1">Taxonomy: {categoryCandidateResults[row.listing_id]?.marketplace || 'unknown'} tree {categoryCandidateResults[row.listing_id]?.taxonomy_tree_id || 'unknown'} · candidate leaf: {selectedCategoryCandidates[row.listing_id].leaf_verified && selectedCategoryCandidates[row.listing_id].leaf ? 'provider-verified' : 'not verified'} · aspects version: {selectedCategoryCandidates[row.listing_id].category_tree_version || 'not returned'}</p>
                                  <p className="mt-1">Current saved condition: {categoryCandidateResults[row.listing_id]?.current_condition?.listing_condition || 'unset'}{categoryCandidateResults[row.listing_id]?.current_condition?.condition_bucket ? ` (${categoryCandidateResults[row.listing_id].current_condition.condition_bucket})` : ''} · candidate condition choices: {(selectedCategoryCandidates[row.listing_id].condition_policy?.supported_conditions || []).map((condition) => `${condition.condition_id}: ${condition.description || 'unnamed'}`).join(', ') || 'not confirmed'}</p>
                                  <p className="mt-1">Current specifics: {JSON.stringify(categoryCandidateResults[row.listing_id]?.current_item_specifics || {})}</p>
                                  <p className="mt-1">Candidate required specifics: {(selectedCategoryCandidates[row.listing_id].required_aspects || []).join(', ') || (selectedCategoryCandidates[row.listing_id].required_aspects_status === 'confirmed' ? 'none reported' : 'unknown')} · missing: {(selectedCategoryCandidates[row.listing_id].missing_required_aspects || []).join(', ') || (selectedCategoryCandidates[row.listing_id].required_aspects_status === 'confirmed' ? 'none reported' : 'unknown')}.</p>
                                  <p className="mt-1">Shipping/readiness blockers: {(row.local_recovery_preflight?.blockers || []).map((issue) => issue.message || issue.code).join(' · ') || 'see original provider error and shipping preflight'}.</p>
                                  <p className="mt-1">This candidate is taxonomy evidence, not a semantic match guarantee. Review product fit, specifics, condition, and shipping setup. No data is edited and no retry is enabled.</p>
                                </div>
                              ) : null}
                              {categoryCandidateResults[row.listing_id]?.message ? <p className="mt-1">{categoryCandidateResults[row.listing_id].message}</p> : null}
                            </div>
                          </div>
                        ) : null}
                        <p className="mt-2 text-xs text-[var(--pp-muted)]">Remote outcome: {row.remote_outcome} · retry eligible: no</p>
                      </div>
                    ))}
                  </div>
                </>
              ) : <p className="text-sm text-[var(--pp-muted)]">No recovery report loaded yet.</p>}
            </div>
          </SectionPanel>

          <SectionPanel title="Advanced controls" description="Use the full settings page if you need server OAuth app credentials, token import, or import-existing-listings tools.">
            <div className="flex flex-wrap gap-2">
              <Button href="/settings" variant="outline">
                Open full settings
              </Button>
              <Button href="/jobs" variant="outline">
                Open jobs console
              </Button>
            </div>
          </SectionPanel>
        </div>
      </div>
    </AppShell>
  );
}
