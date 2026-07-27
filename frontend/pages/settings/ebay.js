import { useEffect, useMemo, useState } from 'react';
import { RefreshCcw, ShieldCheck, Store, Wrench } from 'lucide-react';
import toast from 'react-hot-toast';

import AppShell from '../../components/layout/AppShell';
import Button from '../../components/ui/button';
import MetricCard from '../../components/ui/metric-card';
import PageHeader from '../../components/ui/page-header';
import SectionPanel from '../../components/ui/section-panel';
import StatusPill from '../../components/ui/status-pill';
import { useAuth } from '../../contexts/AuthContext';
import { useEbayAuth } from '../../hooks/useEbayAuth';
import {
  createEbayMerchantLocation,
  fetchEbayAccountReadiness,
  fetchEbayPolicies,
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
        throw new Error(report?.sync_error || report?.policy_settings?.policy_sync_error || `eBay policy sync did not complete (status: ${syncStatus || 'unknown'}).`);
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
