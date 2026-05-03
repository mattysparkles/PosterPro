import { useEffect, useMemo, useState } from 'react';
import { RefreshCcw } from 'lucide-react';
import { toast } from 'sonner';

import AppShell from '../components/layout/AppShell';
import Badge from '../components/ui/badge';
import Button from '../components/ui/button';
import EmptyState from '../components/ui/empty-state';
import Input from '../components/ui/input';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import SectionCard from '../components/ui/section-card';
import { Tabs } from '../components/ui/tabs';
import { useAuth } from '../contexts/AuthContext';
import { useEbayAuth } from '../hooks/useEbayAuth';
import useDashboardData from '../hooks/useDashboardData';
import {
  fetchAccountSetupSummary,
  fetchSaleDetectionSettings,
  toggleAutonomousMode,
  updateCurrentUser,
  updatePlatformConfig,
  updateSaleDetectionSettings,
} from '../lib/api';

const SETTINGS_TABS = [
  { value: 'profile', label: 'Profile' },
  { value: 'ebay', label: 'eBay' },
  { value: 'marketplaces', label: 'Marketplaces' },
  { value: 'automation', label: 'Automation' },
  { value: 'api-keys', label: 'API Keys' },
  { value: 'server-status', label: 'Server status' },
];

const MARKETPLACE_LABELS = {
  ebay: 'eBay',
  etsy: 'Etsy',
  facebook: 'Facebook Marketplace',
  mercari: 'Mercari',
  poshmark: 'Poshmark',
  depop: 'Depop',
  whatnot: 'Whatnot',
  vinted: 'Vinted',
};

export default function SettingsPage({ theme, setTheme }) {
  const { user, refreshUser } = useAuth();
  const { autonomousConfig, reload: reloadDashboard } = useDashboardData(user?.id);
  const [activeTab, setActiveTab] = useState('profile');
  const [setupSummary, setSetupSummary] = useState(null);
  const [salePlatforms, setSalePlatforms] = useState([]);
  const [profileName, setProfileName] = useState('');
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingPublishing, setSavingPublishing] = useState(false);
  const [savingSales, setSavingSales] = useState(false);
  const [loading, setLoading] = useState(true);
  const { loading: connectingEbay, error: ebayConnectError, connect: connectEbay } = useEbayAuth(user?.id);

  const reload = async () => {
    if (!user?.id) return;
    setLoading(true);
    try {
      const [summary, salesConfig] = await Promise.all([
        fetchAccountSetupSummary(user.id),
        fetchSaleDetectionSettings(user.id),
      ]);
      setSetupSummary(summary);
      setSalePlatforms(salesConfig.marketplaces || []);
      setProfileName(summary.user.full_name || '');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, [user?.id]);

  const publishingPlatforms = useMemo(
    () => setupSummary?.marketplace_connections.filter((item) => item.enabled_for_publishing).map((item) => item.marketplace) || [],
    [setupSummary],
  );

  return (
    <AppShell
      active="/settings"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reloadDashboard();
        await reload();
      }}
      theme={theme}
      onToggleTheme={() => {
        const next = theme === 'dark' ? 'light' : 'dark';
        setTheme(next);
        localStorage.setItem('posterpro-theme', next);
        document.documentElement.classList.toggle('dark', next === 'dark');
      }}
    >
      <PageHeader
        eyebrow="Setup"
        title="Settings"
        description="Manage profile, marketplaces, automation, and server readiness."
        actions={
          <Button variant="outline" onClick={reload} disabled={loading}>
            <RefreshCcw size={16} />
            Refresh
          </Button>
        }
      />

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Profile" value={setupSummary?.account_profile_complete ? 'Ready' : 'Missing'} detail="Operator profile completion." />
        <MetricCard
          label="Connected marketplaces"
          value={setupSummary?.marketplace_connections.filter((item) => item.connected).length || 0}
          detail="Marketplace accounts attached to this user."
        />
        <MetricCard label="Ready to publish" value={setupSummary?.ready_to_publish_count || 0} detail="Listings passing publish checks." />
        <MetricCard
          label="Server blockers"
          value={
            [
              setupSummary?.server_readiness?.openai_configured,
              setupSummary?.server_readiness?.photoroom_configured,
              setupSummary?.server_readiness?.ebay_oauth_configured,
              setupSummary?.server_readiness?.storage_root_configured,
            ].filter((value) => value === false).length || 0
          }
          detail="Missing global requirements."
        />
      </section>

      <div className="grid gap-5 xl:grid-cols-[220px_minmax(0,1fr)]">
        <SectionCard title="Sections" description="Choose a settings area.">
          <Tabs items={SETTINGS_TABS} value={activeTab} onChange={setActiveTab} className="flex-col" />
        </SectionCard>

        <div className="space-y-5">
          {activeTab === 'profile' ? (
            <SectionCard title="Profile" description="Manage the operator identity for this workspace.">
              <form
                className="space-y-4"
                onSubmit={async (event) => {
                  event.preventDefault();
                  if (!user?.id) return;
                  setSavingProfile(true);
                  try {
                    await updateCurrentUser({ full_name: profileName });
                    await refreshUser();
                    await reload();
                    toast.success('Profile updated.');
                  } catch (error) {
                    toast.error(error.message);
                  } finally {
                    setSavingProfile(false);
                  }
                }}
              >
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#111827]">Operator or business name</label>
                  <Input value={profileName} onChange={(event) => setProfileName(event.target.value)} placeholder="Sparkles Resale Ops" />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#111827]">Email</label>
                  <Input value={user?.email || ''} disabled />
                </div>
                <Button type="submit" disabled={savingProfile}>
                  {savingProfile ? 'Saving...' : 'Save profile'}
                </Button>
              </form>
            </SectionCard>
          ) : null}

          {activeTab === 'ebay' ? (
            <SectionCard title="eBay" description="Manage the current eBay connection for this account.">
              {setupSummary?.marketplace_connections?.find((item) => item.marketplace === 'ebay') ? (
                <div className="space-y-4">
                  <div className="flex items-center justify-between rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] px-4 py-3">
                    <span className="text-sm font-medium text-[#111827]">Connection status</span>
                    <Badge tone={setupSummary.marketplace_connections.find((item) => item.marketplace === 'ebay').connected ? 'success' : 'default'}>
                      {setupSummary.marketplace_connections.find((item) => item.marketplace === 'ebay').connected ? 'Connected' : 'Not connected'}
                    </Badge>
                  </div>
                  <Button onClick={connectEbay} disabled={connectingEbay}>
                    {connectingEbay ? 'Opening OAuth...' : 'Connect eBay'}
                  </Button>
                  {ebayConnectError ? <p className="text-sm text-[#b42318]">{ebayConnectError}</p> : null}
                </div>
              ) : (
                <EmptyState title="No eBay settings available" description="eBay connection details will appear once the account summary loads." />
              )}
            </SectionCard>
          ) : null}

          {activeTab === 'marketplaces' ? (
            <SectionCard title="Marketplaces" description="Choose which marketplaces are enabled for publishing and sales sync.">
              <div className="space-y-3">
                {(setupSummary?.marketplace_connections || []).map((marketplace) => {
                  const publishingEnabled = publishingPlatforms.includes(marketplace.marketplace);
                  const salesEnabled = salePlatforms.includes(marketplace.marketplace);
                  return (
                    <div key={marketplace.marketplace} className="rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] p-4">
                      <div className="flex items-center justify-between gap-4">
                        <div>
                          <p className="text-sm font-semibold text-[#111827]">{MARKETPLACE_LABELS[marketplace.marketplace] || marketplace.marketplace}</p>
                          <p className="mt-1 text-sm text-[#667085]">{marketplace.status_note}</p>
                        </div>
                        <Badge tone={marketplace.connected ? 'success' : 'default'}>
                          {marketplace.connected ? 'Connected' : 'Not connected'}
                        </Badge>
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        <Button
                          variant={publishingEnabled ? 'default' : 'outline'}
                          size="sm"
                          disabled={savingPublishing}
                          onClick={async () => {
                            const next = publishingEnabled
                              ? publishingPlatforms.filter((name) => name !== marketplace.marketplace)
                              : [...publishingPlatforms, marketplace.marketplace];
                            setSavingPublishing(true);
                            try {
                              await updatePlatformConfig(user.id, next);
                              await reload();
                            } finally {
                              setSavingPublishing(false);
                            }
                          }}
                        >
                          {publishingEnabled ? 'Publishing on' : 'Enable publishing'}
                        </Button>
                        <Button
                          variant={salesEnabled ? 'default' : 'outline'}
                          size="sm"
                          disabled={savingSales}
                          onClick={async () => {
                            const next = salesEnabled
                              ? salePlatforms.filter((name) => name !== marketplace.marketplace)
                              : [...salePlatforms, marketplace.marketplace];
                            setSavingSales(true);
                            try {
                              await updateSaleDetectionSettings(user.id, next);
                              setSalePlatforms(next);
                            } finally {
                              setSavingSales(false);
                            }
                          }}
                        >
                          {salesEnabled ? 'Sales sync on' : 'Enable sales sync'}
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </SectionCard>
          ) : null}

          {activeTab === 'automation' ? (
            <SectionCard title="Automation" description="Publishing mode and dry run visibility.">
              <div className="space-y-3">
                <div className="flex items-center justify-between rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] px-4 py-3">
                  <span className="text-sm font-medium text-[#111827]">Automation mode</span>
                  <Badge tone={autonomousConfig?.autonomous_mode ? 'success' : 'default'}>
                    {autonomousConfig?.autonomous_mode ? 'Enabled' : 'Disabled'}
                  </Badge>
                </div>
                <div className="flex items-center justify-between rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] px-4 py-3">
                  <span className="text-sm font-medium text-[#111827]">Dry run</span>
                  <Badge tone={autonomousConfig?.autonomous_dry_run ? 'info' : 'success'}>
                    {autonomousConfig?.autonomous_dry_run ? 'Enabled' : 'Live mode'}
                  </Badge>
                </div>
                <Button onClick={async () => {
                  await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
                  await reloadDashboard();
                  await reload();
                }}>
                  Toggle automation
                </Button>
              </div>
            </SectionCard>
          ) : null}

          {activeTab === 'api-keys' ? (
            <SectionCard title="API keys" description="Global credentials currently available to this deployment.">
              <div className="space-y-3">
                {[
                  ['OpenAI API key', setupSummary?.server_readiness?.openai_configured],
                  ['PhotoRoom API key', setupSummary?.server_readiness?.photoroom_configured],
                  ['eBay OAuth credentials', setupSummary?.server_readiness?.ebay_oauth_configured],
                ].map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] px-4 py-3">
                    <span className="text-sm font-medium text-[#111827]">{label}</span>
                    <Badge tone={value ? 'success' : 'danger'}>{value ? 'Configured' : 'Missing'}</Badge>
                  </div>
                ))}
              </div>
            </SectionCard>
          ) : null}

          {activeTab === 'server-status' ? (
            <SectionCard title="Server status" description="Global configuration status that affects the entire app.">
              <div className="space-y-3">
                {[
                  ['Storage root', setupSummary?.server_readiness?.storage_root_configured],
                  ['OpenAI', setupSummary?.server_readiness?.openai_configured],
                  ['PhotoRoom', setupSummary?.server_readiness?.photoroom_configured],
                  ['eBay OAuth', setupSummary?.server_readiness?.ebay_oauth_configured],
                ].map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] px-4 py-3">
                    <span className="text-sm font-medium text-[#111827]">{label}</span>
                    <Badge tone={value ? 'success' : 'danger'}>{value ? 'Ready' : 'Blocked'}</Badge>
                  </div>
                ))}
              </div>
            </SectionCard>
          ) : null}
        </div>
      </div>
    </AppShell>
  );
}

SettingsPage.requireAuth = true;
