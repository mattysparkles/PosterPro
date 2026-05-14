import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { RefreshCcw } from 'lucide-react';
import { useRouter } from 'next/router';
import toast from 'react-hot-toast';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import Drawer from '../components/ui/drawer';
import EmptyState from '../components/ui/empty-state';
import HelpTip from '../components/ui/help-tip';
import Input from '../components/ui/input';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import StatusPill from '../components/ui/status-pill';
import { useAuth } from '../contexts/AuthContext';
import { useEbayAuth } from '../hooks/useEbayAuth';
import useDashboardData from '../hooks/useDashboardData';
import {
  fetchAccountSetupSummary,
  fetchSaleDetectionSettings,
  fetchSettingsPanels,
  toggleAutonomousMode,
  updateCurrentUser,
  updateMarketplaceConnection,
  updatePlatformConfig,
  updateSaleDetectionSettings,
  updateServerSettings,
} from '../lib/api';

const SETTINGS_TABS = [
  { value: 'overview', label: 'Overview' },
  { value: 'profile', label: 'Profile' },
  { value: 'workflow', label: 'Workflow' },
  { value: 'ebay', label: 'eBay' },
  { value: 'amazon', label: 'Amazon' },
  { value: 'marketplaces', label: 'Marketplaces' },
  { value: 'automation', label: 'Automation' },
  { value: 'api-keys', label: 'API Keys' },
  { value: 'email', label: 'Email' },
  { value: 'server', label: 'Server' },
];

const SETTINGS_GROUPS = [
  { label: 'Account', tabs: ['overview', 'profile', 'workflow'] },
  { label: 'Channels', tabs: ['ebay', 'amazon', 'marketplaces'] },
  { label: 'Admin', tabs: ['automation', 'api-keys', 'email', 'server'] },
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

const MARKETPLACE_GUIDES = {
  ebay: {
    summary: 'Use eBay OAuth for the real account connection, then enable publishing and sales sync from the same workspace.',
    tooltip: 'PosterPro needs the server eBay app first, then each operator connects their own eBay account.',
    prerequisites: ['Admin saves App ID, Cert ID, and redirect URI', 'Operator signs into the correct eBay seller account', 'Publishing policy data is ready in the eBay app'],
    steps: [
      'Open the eBay tab and save the server OAuth credentials.',
      'Click Connect eBay and complete the eBay consent flow in the seller account you want tied to this workspace.',
      'Return here and confirm the account shows Ready before enabling publishing or sales sync.',
    ],
  },
  amazon: {
    summary: 'Amazon support is currently for Vine import and media lookup rather than direct marketplace publishing.',
    tooltip: 'These settings unlock Vine ingestion and Amazon image/media enrichment, not a full Amazon seller connector.',
    prerequisites: ['Admin decides whether Vine import is enabled', 'Optional PA-API credentials are available for media lookup', 'Marketplace region and rate limits are defined'],
    steps: [
      'Enable the Vine importer only for the roles or plans that should access it.',
      'Add PA-API credentials if you want PosterPro to pull Amazon media automatically.',
      'Choose the fetch mode and rate limit that match the hosting environment.',
    ],
  },
  mercari: {
    summary: 'Mercari currently uses an operator-managed workflow. PosterPro stores the account details and readiness state for the team.',
    tooltip: 'This is a guided manual setup today, not a direct OAuth connector.',
    prerequisites: ['Store or closet name', 'Mercari handle', 'Internal posting notes for the operator'],
    steps: [
      'Save the storefront name and account handle for the operator.',
      'Document any posting or shipping rules in Workflow notes.',
      'Mark the workflow Ready only when the human posting process is actually ready to use.',
    ],
  },
  poshmark: {
    summary: 'Poshmark is modeled as a guided manual channel so the workspace can still control readiness and workflow quality.',
    tooltip: 'Operators can prepare the account metadata and process notes before enabling the channel.',
    prerequisites: ['Closet name', 'Poshmark username', 'Sharing, pricing, or closet-maintenance notes'],
    steps: [
      'Add the closet display name and the account handle.',
      'Capture the exact manual process the team should follow for listing and maintenance.',
      'Move the channel to Ready after the operator workflow has been reviewed.',
    ],
  },
  facebook: {
    summary: 'Facebook Marketplace is tracked as an operator workflow so setup is consistent even without a direct API integration.',
    tooltip: 'PosterPro can store the process and account context even when publishing still involves manual work.',
    prerequisites: ['Marketplace profile name', 'Internal account notes', 'Local shipping or meetup rules'],
    steps: [
      'Record which Facebook profile or business page owns the channel.',
      'Add any policy or handoff notes the operator needs before publishing.',
      'Mark the workflow Ready only after the real account has been reviewed.',
    ],
  },
  depop: {
    summary: 'Depop setup is handled as a guided manual process with saved account context and workflow notes.',
    tooltip: 'Use this to standardize onboarding now, even before a fuller connector exists.',
    prerequisites: ['Shop name', 'Depop handle', 'Operator-specific listing notes'],
    steps: [
      'Save the Depop shop identity for this workspace.',
      'Add internal notes for shipping, style, or negotiation expectations.',
      'Mark the channel Ready once a real operator can execute the process cleanly.',
    ],
  },
  whatnot: {
    summary: 'Whatnot setup helps operators capture live-selling workflow details inside the account record.',
    tooltip: 'This is useful for team onboarding even before direct automation is introduced.',
    prerequisites: ['Seller handle', 'Show format or cadence notes', 'Internal prep checklist'],
    steps: [
      'Save the seller handle and show-facing account name.',
      'Document the prep, promo, and post-show workflow in notes.',
      'Mark the workflow Ready once the team can run it consistently.',
    ],
  },
  vinted: {
    summary: 'Vinted is represented as a guided manual channel with stored account metadata and readiness.',
    tooltip: 'Use the saved notes to reduce handoff mistakes between operators.',
    prerequisites: ['Closet name', 'Vinted username', 'Country or shipping caveats'],
    steps: [
      'Add the Vinted account identity used by the operator.',
      'Record any platform-specific notes that affect listings or fulfillment.',
      'Mark the channel Ready after the process is actually ready for production use.',
    ],
  },
};

const SERVICE_GUIDES = {
  openai: {
    title: 'OpenAI',
    tooltip: 'Used for listing copy, pricing help, and AI-assisted product enrichment.',
    steps: [
      'Create an API key in the OpenAI account that should fund PosterPro usage.',
      'Paste it into the API Keys tab and save it from an admin session.',
      'Recheck the setup center to confirm the server now reports OpenAI ready.',
    ],
  },
  photoroom: {
    title: 'PhotoRoom',
    tooltip: 'Used for background removal and photo cleanup flows.',
    steps: [
      'Generate a PhotoRoom API key in the production PhotoRoom account.',
      'Paste it into the API Keys tab and save it from an admin session.',
      'Validate the photo tools workflow from a real listing after saving.',
    ],
  },
  security: {
    title: 'Secret storage',
    tooltip: 'PosterPro stores runtime secrets encrypted at rest and never returns them to the browser after save.',
    steps: [
      'Keep a strong SESSION_SECRET configured on the server before saving credentials.',
      'Enter keys only from the admin settings panels, not directly in browser code.',
      'Rotate a provider key here whenever a credential is replaced upstream.',
    ],
  },
};

const WORKFLOW_PREVIEW_OPTIONS = [
  { value: 'marketplace', label: 'Marketplace preview' },
  { value: 'editor', label: 'Editor first' },
];

const CREDENTIAL_INSTRUCTIONS = {
  openai: [
    { field: 'OpenAI API key', where: 'OpenAI dashboard -> API keys', how: 'Create a project or organization key with billing enabled.', purpose: 'Powers title, description, enrichment, and AI pricing assistance.' },
  ],
  photoroom: [
    { field: 'PhotoRoom API key', where: 'PhotoRoom developer or API dashboard', how: 'Create an API key for the production workspace that will handle background removal.', purpose: 'Enables background removal and photo cleanup tools from the listing editor.' },
  ],
  ebay: [
    { field: 'App ID', where: 'eBay Developers Program -> Application Keys', how: 'Create or open the production app, then copy the App ID exactly as shown.', purpose: 'Identifies the PosterPro app during eBay OAuth.' },
    { field: 'Cert ID', where: 'eBay Developers Program -> Application Keys', how: 'Copy the production Cert ID from the same eBay app and save it here.', purpose: 'Authenticates PosterPro when it exchanges eBay OAuth tokens.' },
    { field: 'Redirect URI', where: 'eBay Developers Program -> User Tokens / RuName', how: 'Register the live callback URL and paste the exact same value here.', purpose: 'Lets eBay return the operator back to PosterPro after consent.' },
  ],
  amazon: [
    { field: 'PA-API access key', where: 'Amazon Associates / Product Advertising API console', how: 'Generate access credentials for the account that will handle media lookup.', purpose: 'Lets PosterPro request Amazon product metadata and media.' },
    { field: 'PA-API secret key', where: 'Amazon Associates / Product Advertising API console', how: 'Copy the matching secret key immediately after generation.', purpose: 'Authenticates the PA-API calls used for enrichment.' },
    { field: 'Partner tag', where: 'Amazon Associates account settings', how: 'Copy the tracking tag for the approved associates account.', purpose: 'Required by Amazon PA-API request signing.' },
  ],
  email: [
    { field: 'SMTP host', where: 'Your mail provider admin panel', how: 'Use the provider SMTP hostname, such as the transactional email relay host.', purpose: 'Determines where PosterPro sends password reset mail.' },
    { field: 'SMTP username', where: 'Your mail provider account or SMTP credentials panel', how: 'Create a sending credential or use the provider-issued SMTP username.', purpose: 'Authenticates the mail session when the relay requires login.' },
    { field: 'SMTP password', where: 'Your mail provider account or SMTP credentials panel', how: 'Generate an app password or SMTP secret and store it here.', purpose: 'Secures the SMTP login used for reset delivery.' },
    { field: 'From address', where: 'Verified sending domain or mailbox in your mail provider', how: 'Use a verified sender, for example noreply@yourdomain.com.', purpose: 'Controls the address reset emails appear to come from.' },
  ],
};

function GuideCard({ title, description, tooltip, prerequisites = [], steps = [], tone = 'blue' }) {
  const toneClass =
    tone === 'amber'
      ? 'border-amber-200 bg-amber-50/80'
      : tone === 'slate'
      ? 'border-slate-200 bg-slate-50'
      : 'border-[#dbe7ff] bg-[#f6f9ff]';

  return (
    <div className={`rounded-[16px] border ${toneClass} p-4`}>
      <div className="flex items-start justify-between gap-3">
        <div className="max-w-[1024px]">
          <p className="text-sm font-semibold text-[#101828]">{title}</p>
          {description ? <p className="mt-1 text-sm text-[#475467]">{description}</p> : null}
        </div>
        {tooltip ? <HelpTip label={`${title} help`}>{tooltip}</HelpTip> : null}
      </div>
      {prerequisites.length ? (
        <div className="mt-4">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">Before you start</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {prerequisites.map((item) => (
              <span key={item} className="rounded-full border border-white/80 bg-white/80 px-3 py-1 text-xs font-medium text-[#344054]">
                {item}
              </span>
            ))}
          </div>
        </div>
      ) : null}
      {steps.length ? (
        <ol className="mt-4 space-y-2">
          {steps.map((step, index) => (
            <li key={step} className="flex gap-3 text-sm text-[#344054]">
              <span className="mt-[1px] flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white text-xs font-semibold text-[#1d4ed8]">
                {index + 1}
              </span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function InstructionTable({ title, rows }) {
  return (
    <div className="overflow-hidden rounded-[16px] border border-[#e5e7eb] bg-white">
      <div className="border-b border-[#e5e7eb] bg-[#f8fafc] px-4 py-3">
        <p className="text-sm font-semibold text-[#101828]">{title}</p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-[#fcfcfd] text-[#667085]">
            <tr>
              <th className="px-4 py-3 font-medium">Field</th>
              <th className="px-4 py-3 font-medium">Where to get it</th>
              <th className="px-4 py-3 font-medium">How to obtain it</th>
              <th className="px-4 py-3 font-medium">What it does</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.field} className="border-t border-[#e5e7eb] align-top">
                <td className="px-4 py-3 font-medium text-[#101828]">{row.field}</td>
                <td className="px-4 py-3 text-[#475467]">{row.where}</td>
                <td className="px-4 py-3 text-[#475467]">{row.how}</td>
                <td className="px-4 py-3 text-[#475467]">{row.purpose}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const router = useRouter();
  const { user, refreshUser, changePassword, setViewAsRegular } = useAuth();
  const { autonomousConfig, reload: reloadDashboard } = useDashboardData(user?.id);
  const { loading: connectingEbay, error: ebayConnectError, connect: connectEbay } = useEbayAuth(user?.id);
  const [activeTab, setActiveTab] = useState('overview');
  const [setupSummary, setSetupSummary] = useState(null);
  const [settingsPanels, setSettingsPanels] = useState(null);
  const [salePlatforms, setSalePlatforms] = useState([]);
  const [profileName, setProfileName] = useState('');
  const [loading, setLoading] = useState(true);
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingPassword, setSavingPassword] = useState(false);
  const [savingViewMode, setSavingViewMode] = useState(false);
  const [savingServer, setSavingServer] = useState(false);
  const [savingPublishing, setSavingPublishing] = useState(false);
  const [savingSales, setSavingSales] = useState(false);
  const [savingMarketplace, setSavingMarketplace] = useState(false);
  const [selectedMarketplace, setSelectedMarketplace] = useState('');
  const [marketplaceForm, setMarketplaceForm] = useState({
    display_name: '',
    account_handle: '',
    notes: '',
    workflow_state: 'draft',
  });
  const [ebayForm, setEbayForm] = useState({ ebay_client_id: '', ebay_client_secret: '', ebay_redirect_uri: '' });
  const [apiKeyForm, setApiKeyForm] = useState({ openai_api_key: '', photoroom_api_key: '' });
  const [workflowForm, setWorkflowForm] = useState({
    review_before_publish: true,
    auto_publish_after_approval: false,
    bulk_approval_enabled: true,
    listing_preview_mode: 'marketplace',
  });
  const [automationForm, setAutomationForm] = useState({
    autonomous_dry_run: false,
    autonomous_crosspost_enabled: false,
    sale_detection_enabled: false,
    sale_detection_dry_run: false,
    sale_detection_poll_minutes: 15,
  });
  const [serverForm, setServerForm] = useState({ app_base_url: '', environment: '', storage_root: '' });
  const [emailForm, setEmailForm] = useState({
    smtp_host: '',
    smtp_port: 587,
    smtp_username: '',
    smtp_password: '',
    smtp_from_email: '',
    smtp_from_name: 'PosterPro',
    smtp_use_tls: true,
  });
  const [passwordForm, setPasswordForm] = useState({
    current_password: '',
    new_password: '',
    confirm_password: '',
  });
  const [amazonForm, setAmazonForm] = useState({
    amazon_vine_import_enabled: false,
    amazon_vine_import_premium_only: false,
    amazon_media_lookup_enabled: false,
    amazon_media_page_fallback_enabled: false,
    amazon_marketplace_region: 'US',
    amazon_media_fetch_mode: 'manual_only',
    amazon_media_rate_limit_per_minute: 12,
    amazon_paapi_access_key: '',
    amazon_paapi_secret_key: '',
    amazon_paapi_partner_tag: '',
  });
  const canManageServer = !!settingsPanels?.server?.can_manage;
  const visibleTabs = SETTINGS_TABS.filter((tab) => {
    if (canManageServer) return true;
    return ['overview', 'profile', 'workflow', 'ebay', 'marketplaces'].includes(tab.value);
  });
  const visibleTabGroups = SETTINGS_GROUPS.map((group) => ({
    ...group,
    tabs: group.tabs
      .map((value) => visibleTabs.find((tab) => tab.value === value))
      .filter(Boolean),
  })).filter((group) => group.tabs.length);

  const reload = async () => {
    if (!user?.id) return;
    setLoading(true);
    try {
      const [summary, salesConfig, panels] = await Promise.all([
        fetchAccountSetupSummary(user.id),
        fetchSaleDetectionSettings(user.id),
        fetchSettingsPanels(),
      ]);
      setSetupSummary(summary);
      setSalePlatforms(salesConfig.marketplaces || []);
      setSettingsPanels(panels);
      setProfileName(panels.profile.full_name || '');
      setEbayForm({
        ebay_client_id: '',
        ebay_client_secret: '',
        ebay_redirect_uri: panels.ebay.redirect_uri || '',
      });
      setApiKeyForm({
        openai_api_key: '',
        photoroom_api_key: '',
      });
      setWorkflowForm({
        review_before_publish: panels.workflow?.review_before_publish ?? true,
        auto_publish_after_approval: panels.workflow?.auto_publish_after_approval ?? false,
        bulk_approval_enabled: panels.workflow?.bulk_approval_enabled ?? true,
        listing_preview_mode: panels.workflow?.listing_preview_mode || 'marketplace',
      });
      setAutomationForm({
        autonomous_dry_run: !!panels.automation.autonomous_dry_run,
        autonomous_crosspost_enabled: !!panels.automation.autonomous_crosspost_enabled,
        sale_detection_enabled: !!panels.automation.sale_detection_enabled,
        sale_detection_dry_run: !!panels.automation.sale_detection_dry_run,
        sale_detection_poll_minutes: Number(panels.automation.sale_detection_poll_minutes || 15),
      });
      setServerForm({
        app_base_url: panels.server.app_base_url || '',
        environment: panels.server.environment || '',
        storage_root: panels.server.storage_root || '',
      });
      setEmailForm({
        smtp_host: panels.email?.host || '',
        smtp_port: Number(panels.email?.port || 587),
        smtp_username: panels.email?.username || '',
        smtp_password: '',
        smtp_from_email: panels.email?.from_email || '',
        smtp_from_name: panels.email?.from_name || 'PosterPro',
        smtp_use_tls: panels.email?.use_tls ?? true,
      });
      setAmazonForm({
        amazon_vine_import_enabled: !!panels.amazon?.vine_import_enabled,
        amazon_vine_import_premium_only: !!panels.amazon?.vine_import_premium_only,
        amazon_media_lookup_enabled: !!panels.amazon?.media_lookup_enabled,
        amazon_media_page_fallback_enabled: !!panels.amazon?.media_page_fallback_enabled,
        amazon_marketplace_region: panels.amazon?.marketplace_region || 'US',
        amazon_media_fetch_mode: panels.amazon?.media_fetch_mode || 'manual_only',
        amazon_media_rate_limit_per_minute: Number(panels.amazon?.media_rate_limit_per_minute || 12),
        amazon_paapi_access_key: '',
        amazon_paapi_secret_key: '',
        amazon_paapi_partner_tag: '',
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, [user?.id]);

  useEffect(() => {
    if (!router.isReady) return;
    const tab = typeof router.query.tab === 'string' ? router.query.tab : '';
    const marketplace = typeof router.query.marketplace === 'string' ? router.query.marketplace : '';
    if (tab && SETTINGS_TABS.some((item) => item.value === tab)) {
      setActiveTab(tab);
    }
    if (marketplace) {
      setSelectedMarketplace(marketplace.toLowerCase());
    }
  }, [router.isReady, router.query.marketplace, router.query.tab]);

  useEffect(() => {
    if (visibleTabs.some((tab) => tab.value === activeTab)) return;
    setActiveTab(visibleTabs[0]?.value || 'profile');
  }, [activeTab, visibleTabs]);

  const publishingPlatforms = useMemo(
    () => setupSummary?.marketplace_connections?.filter((item) => item.enabled_for_publishing).map((item) => item.marketplace) || [],
    [setupSummary],
  );

  const configuredMarketplace = useMemo(
    () => (setupSummary?.marketplace_connections || []).find((item) => item.marketplace === selectedMarketplace) || null,
    [selectedMarketplace, setupSummary],
  );

  const openMarketplaceDrawer = (marketplace) => {
    if (!marketplace) return;
    setSelectedMarketplace(marketplace.marketplace);
    setMarketplaceForm({
      display_name: marketplace.display_name || '',
      account_handle: marketplace.account_handle || '',
      notes: marketplace.notes || '',
      workflow_state: marketplace.workflow_state || 'draft',
    });
  };

  const activeMarketplaceGuide =
    MARKETPLACE_GUIDES[selectedMarketplace] ||
    MARKETPLACE_GUIDES[configuredMarketplace?.marketplace] ||
    null;
  const workflowCards = useMemo(() => {
    const serverReadiness = setupSummary?.server_readiness || {};
    return [
      {
        label: 'Account setup',
        value: setupSummary?.account_profile_complete ? 'Ready' : 'Needs profile',
        detail: 'Operator identity and workspace profile.',
      },
      {
        label: 'Connected channels',
        value: setupSummary?.connected_marketplaces ?? 0,
        detail: 'Marketplace accounts currently connected.',
      },
      {
        label: 'Workflow mode',
        value: workflowForm.review_before_publish ? 'Review first' : 'Direct publish',
        detail: 'Draft approval policy for this account.',
      },
      {
        label: 'Server blockers',
        value: [serverReadiness.openai_configured, serverReadiness.photoroom_configured, serverReadiness.ebay_oauth_configured].filter(Boolean).length + '/3',
        detail: 'Core provider readiness across AI, photo tools, and eBay.',
      },
    ];
  }, [setupSummary, workflowForm.review_before_publish]);

  return (
    <AppShell
      active="/settings"
      title="Settings"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reloadDashboard();
        await reload();
      }}
    >
      <PageHeader
        title="Settings"
        description="Control account profile, marketplace connections, automation, and deployment-level credentials."
        actions={
          <Button variant="outline" onClick={reload} disabled={loading}>
            <RefreshCcw size={16} />
            Refresh
          </Button>
        }
      />

      <div className="grid gap-5 xl:grid-cols-[220px_minmax(0,1fr)]">
        <nav className="pp-card p-3">
          <div className="space-y-4">
            {visibleTabGroups.map((group) => (
              <div key={group.label}>
                <p className="mb-2 px-3 text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{group.label}</p>
                <div className="space-y-1">
                  {group.tabs.map((tab) => (
                    <button
                      key={tab.value}
                      type="button"
                      onClick={() => setActiveTab(tab.value)}
                      className={`flex h-10 w-full items-center rounded-[10px] px-3 text-left text-sm font-medium transition-colors ${
                        activeTab === tab.value ? 'bg-[#eef4ff] text-[#2563eb]' : 'text-[#475467] hover:bg-[#f9fafb]'
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </nav>

        <div>
          {activeTab === 'overview' ? (
            <SectionPanel title="Settings Overview" description="A clean summary of operator-level choices, channel setup, and admin credentials that still need attention.">
              <div className="space-y-6">
                <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  {workflowCards.map((card) => (
                    <MetricCard key={card.label} label={card.label} value={card.value} detail={card.detail} />
                  ))}
                </section>

                <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.9fr)]">
                  <SectionPanel
                    className="border-none bg-transparent p-0 shadow-none"
                    title="User-level settings"
                    description="Controls that belong to the signed-in operator rather than the server."
                  >
                    <div className="grid gap-3 md:grid-cols-2">
                      <button type="button" onClick={() => setActiveTab('profile')} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
                        <p className="text-sm font-semibold text-[#101828]">Profile</p>
                        <p className="mt-1 text-sm text-[#667085]">Operator name, password changes, and account-level identity.</p>
                      </button>
                      <button type="button" onClick={() => setActiveTab('workflow')} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
                        <p className="text-sm font-semibold text-[#101828]">Workflow</p>
                        <p className="mt-1 text-sm text-[#667085]">Review-before-publish, bulk approvals, and preview layout.</p>
                      </button>
                      <button type="button" onClick={() => setActiveTab('ebay')} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
                        <p className="text-sm font-semibold text-[#101828]">eBay account</p>
                        <p className="mt-1 text-sm text-[#667085]">Server OAuth setup plus the current user connection state.</p>
                      </button>
                      <button type="button" onClick={() => setActiveTab('marketplaces')} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
                        <p className="text-sm font-semibold text-[#101828]">Channel onboarding</p>
                        <p className="mt-1 text-sm text-[#667085]">Manual and connected marketplace readiness for this workspace.</p>
                      </button>
                    </div>
                  </SectionPanel>

                  <SectionPanel
                    className="border-none bg-transparent p-0 shadow-none"
                    title="Admin credentials"
                    description="Deployment-level credentials and tokens that should live only in admin settings."
                  >
                    <div className="space-y-3">
                      {[
                        ['API Keys', 'OpenAI and PhotoRoom secrets for AI enrichment and photo tooling.', 'api-keys'],
                        ['Email Delivery', 'SMTP relay settings for real forgot-password delivery.', 'email'],
                        ['Server', 'Public URL, storage root, and deployment-wide environment values.', 'server'],
                        ['Automation', 'Global publish and polling behavior that affects every user.', 'automation'],
                      ].map(([title, note, tab]) => (
                        <button key={tab} type="button" onClick={() => setActiveTab(tab)} className="w-full rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
                          <p className="text-sm font-semibold text-[#101828]">{title}</p>
                          <p className="mt-1 text-sm text-[#667085]">{note}</p>
                        </button>
                      ))}
                    </div>
                  </SectionPanel>
                </div>

                <InstructionTable title="Remaining production inputs" rows={[...CREDENTIAL_INSTRUCTIONS.openai, ...CREDENTIAL_INSTRUCTIONS.photoroom, ...CREDENTIAL_INSTRUCTIONS.ebay, ...CREDENTIAL_INSTRUCTIONS.email]} />
              </div>
            </SectionPanel>
          ) : null}

          {activeTab === 'profile' ? (
            <SectionPanel title="Profile" description="This is the operator identity used across the workspace.">
              <div className="space-y-6">
                <form
                  className="space-y-4"
                  onSubmit={async (event) => {
                    event.preventDefault();
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
                  <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
                    <div className="space-y-4">
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">Operator or business name</label>
                        <Input value={profileName} onChange={(event) => setProfileName(event.target.value)} placeholder="Sparkles Resale Ops" />
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">Email</label>
                        <Input value={settingsPanels?.profile?.email || user?.email || ''} disabled />
                      </div>
                      <div className="flex flex-wrap items-center gap-3">
                        <Button type="submit" disabled={savingProfile}>
                          {savingProfile ? 'Saving...' : 'Save profile'}
                        </Button>
                        <StatusPill
                          status={setupSummary?.account_profile_complete ? 'success' : 'warning'}
                          label={setupSummary?.account_profile_complete ? 'Profile complete' : 'Profile incomplete'}
                        />
                        {user?.is_admin ? (
                          <StatusPill
                            status={user?.view_as_regular ? 'warning' : 'info'}
                            label={user?.view_as_regular ? 'Viewing as regular user' : 'Admin mode'}
                          />
                        ) : null}
                      </div>
                    </div>
                    <GuideCard
                      title="Account hygiene"
                      description="A complete profile and tested sign-in flow reduce onboarding friction for every operator added later."
                      tooltip="This section controls the identity and access behavior tied to the current operator account."
                      prerequisites={['Real operator name or business name', 'Reachable account email', 'Strong password policy']}
                      steps={[
                        'Save the operator or business name that should appear throughout the workspace.',
                        'Use Change password below to rotate credentials without involving server access.',
                        'If you are an admin, toggle regular-user preview before reviewing the onboarding flow.',
                      ]}
                      tone="slate"
                    />
                  </div>
                </form>

                <div className="grid gap-4 xl:grid-cols-2">
                  <form
                    className="rounded-[16px] border border-[#e5e7eb] bg-[#fcfcfd] p-5"
                    onSubmit={async (event) => {
                      event.preventDefault();
                      if (passwordForm.new_password !== passwordForm.confirm_password) {
                        toast.error('The new password confirmation does not match.');
                        return;
                      }
                      setSavingPassword(true);
                      try {
                        await changePassword({
                          current_password: passwordForm.current_password,
                          new_password: passwordForm.new_password,
                        });
                        setPasswordForm({
                          current_password: '',
                          new_password: '',
                          confirm_password: '',
                        });
                        toast.success('Password changed.');
                      } catch (error) {
                        toast.error(error.message);
                      } finally {
                        setSavingPassword(false);
                      }
                    }}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-[#101828]">Password</p>
                        <p className="mt-1 text-sm text-[#667085]">Store passwords as salted PBKDF2 hashes and let operators rotate them from inside the app.</p>
                      </div>
                      <HelpTip label="Password help">
                        Passwords are never stored in plain text. PosterPro verifies them against salted PBKDF2 password hashes.
                      </HelpTip>
                    </div>
                    <div className="mt-4 space-y-3">
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">Current password</label>
                        <Input
                          type="password"
                          value={passwordForm.current_password}
                          onChange={(event) => setPasswordForm((current) => ({ ...current, current_password: event.target.value }))}
                          placeholder="Enter current password"
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">New password</label>
                        <Input
                          type="password"
                          value={passwordForm.new_password}
                          onChange={(event) => setPasswordForm((current) => ({ ...current, new_password: event.target.value }))}
                          placeholder="Create a stronger password"
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">Confirm new password</label>
                        <Input
                          type="password"
                          value={passwordForm.confirm_password}
                          onChange={(event) => setPasswordForm((current) => ({ ...current, confirm_password: event.target.value }))}
                          placeholder="Repeat the new password"
                        />
                      </div>
                    </div>
                    <div className="mt-4 flex flex-wrap items-center gap-3">
                      <Button type="submit" disabled={savingPassword}>
                        {savingPassword ? 'Updating...' : 'Change password'}
                      </Button>
                      <Link href="/forgot-password" className="text-sm font-medium text-[#2563eb]">
                        Open forgot-password flow
                      </Link>
                    </div>
                  </form>

                  {user?.is_admin ? (
                    <div className="rounded-[16px] border border-[#dbe7ff] bg-[#f7faff] p-5">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[#101828]">Admin preview mode</p>
                          <p className="mt-1 text-sm text-[#667085]">
                            View the product as a normal operator so onboarding, settings visibility, and permission-sensitive flows can be reviewed without creating a second account.
                          </p>
                        </div>
                        <HelpTip label="Admin preview help">
                          Regular-user preview keeps the same account signed in but suppresses admin-only settings and permissions for this session.
                        </HelpTip>
                      </div>
                      <div className="mt-4 flex flex-wrap items-center gap-3">
                        <StatusPill
                          status={user?.view_as_regular ? 'warning' : 'success'}
                          label={user?.view_as_regular ? 'Regular-user preview on' : 'Full admin mode'}
                        />
                        <Button
                          type="button"
                          variant="outline"
                          disabled={savingViewMode}
                          onClick={async () => {
                            setSavingViewMode(true);
                            try {
                              await setViewAsRegular(!user?.view_as_regular);
                              await reload();
                              toast.success(user?.view_as_regular ? 'Admin mode restored.' : 'Regular-user preview enabled.');
                            } catch (error) {
                              toast.error(error.message);
                            } finally {
                              setSavingViewMode(false);
                            }
                          }}
                        >
                          {savingViewMode ? 'Updating...' : user?.view_as_regular ? 'Return to admin mode' : 'View as regular user'}
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <GuideCard
                      title="Password recovery"
                      description="If the current operator loses access, the public forgot-password flow can issue a recovery token for the reset form."
                      steps={[
                        'Open the forgot-password screen from sign-in or this profile page.',
                        'Request a reset for the account email.',
                        'Complete the reset form with the recovery token and choose a new password.',
                      ]}
                      tone="amber"
                    />
                  )}
                </div>
              </div>
            </SectionPanel>
          ) : null}

          {activeTab === 'workflow' ? (
            <SectionPanel title="Workflow" description="Control how drafts move from AI generation into human review and publish approval.">
              <div className="space-y-6">
                <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
                  <form
                    className="space-y-4"
                    onSubmit={async (event) => {
                      event.preventDefault();
                      setSavingProfile(true);
                      try {
                        await updateCurrentUser(workflowForm);
                        await refreshUser();
                        await reload();
                        toast.success('Workflow preferences saved.');
                      } catch (error) {
                        toast.error(error.message);
                      } finally {
                        setSavingProfile(false);
                      }
                    }}
                  >
                    <div className="grid gap-3">
                      <label className="flex items-center justify-between rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-4 text-sm text-[#101828]">
                        <div>
                          <p className="font-semibold text-[#101828]">Require review before publish</p>
                          <p className="mt-1 text-sm text-[#667085]">Default to a draft-review queue so AI fills the listing, then a human approves it before any live publish call.</p>
                        </div>
                        <input
                          type="checkbox"
                          checked={workflowForm.review_before_publish}
                          onChange={(event) => setWorkflowForm((current) => ({ ...current, review_before_publish: event.target.checked }))}
                        />
                      </label>
                      <label className="flex items-center justify-between rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-4 text-sm text-[#101828]">
                        <div>
                          <p className="font-semibold text-[#101828]">Allow auto-publish after approval</p>
                          <p className="mt-1 text-sm text-[#667085]">If enabled, approved drafts can move directly into queueing when the operator confirms them.</p>
                        </div>
                        <input
                          type="checkbox"
                          checked={workflowForm.auto_publish_after_approval}
                          onChange={(event) => setWorkflowForm((current) => ({ ...current, auto_publish_after_approval: event.target.checked }))}
                        />
                      </label>
                      <label className="flex items-center justify-between rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-4 text-sm text-[#101828]">
                        <div>
                          <p className="font-semibold text-[#101828]">Enable bulk approvals</p>
                          <p className="mt-1 text-sm text-[#667085]">Lets the operator select many drafts and approve them together after checking the queue.</p>
                        </div>
                        <input
                          type="checkbox"
                          checked={workflowForm.bulk_approval_enabled}
                          onChange={(event) => setWorkflowForm((current) => ({ ...current, bulk_approval_enabled: event.target.checked }))}
                        />
                      </label>
                      <div className="rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-4">
                        <label className="text-sm font-semibold text-[#101828]">Default review layout</label>
                        <p className="mt-1 text-sm text-[#667085]">Choose whether the review drawer should open as a marketplace-style preview or a direct editor first.</p>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {WORKFLOW_PREVIEW_OPTIONS.map((option) => (
                            <button
                              key={option.value}
                              type="button"
                              onClick={() => setWorkflowForm((current) => ({ ...current, listing_preview_mode: option.value }))}
                              className={`rounded-[10px] border px-3 py-2 text-sm font-medium ${
                                workflowForm.listing_preview_mode === option.value
                                  ? 'border-[#bfd2ff] bg-[#eef4ff] text-[#2563eb]'
                                  : 'border-[#e5e7eb] bg-white text-[#475467]'
                              }`}
                            >
                              {option.label}
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-3">
                      <Button type="submit" disabled={savingProfile}>
                        {savingProfile ? 'Saving...' : 'Save workflow'}
                      </Button>
                      <StatusPill status={workflowForm.review_before_publish ? 'success' : 'warning'} label={workflowForm.review_before_publish ? 'Review gate on' : 'Direct publish allowed'} />
                      <StatusPill status={workflowForm.bulk_approval_enabled ? 'info' : 'default'} label={workflowForm.bulk_approval_enabled ? 'Bulk actions enabled' : 'Single approvals only'} />
                    </div>
                  </form>

                  <GuideCard
                    title="Recommended operator flow"
                    description="This is the closest PosterPro can currently get to the ideal workflow you described without overpromising the unfinished deep vision and sold-comps automation."
                    tooltip="Review-first mode is the safe default because some AI and marketplace integrations are still partial."
                    prerequisites={['Import a photo batch', 'Let the draft pipeline generate listing data', 'Open drafts from the Listings review queue']}
                    steps={[
                      'Leave review-before-publish enabled so every draft lands in a review queue first.',
                      'Use the pricing reasoning and marketplace preview in Listings to approve or correct each draft.',
                      'Use bulk approval only after the queue has been spot-checked for a batch quality pass.',
                    ]}
                    tone="slate"
                  />
                </div>
              </div>
            </SectionPanel>
          ) : null}

          {activeTab === 'ebay' ? (
            <SectionPanel title="eBay" description="Store the OAuth app settings on the server, then connect the current account.">
              <form
                className="space-y-4"
                onSubmit={async (event) => {
                  event.preventDefault();
                  if (!canManageServer) {
                    toast.error('Admin access is required to change server credentials.');
                    return;
                  }
                  const payload = {};
                  if (ebayForm.ebay_client_id.trim()) payload.ebay_client_id = ebayForm.ebay_client_id.trim();
                  if (ebayForm.ebay_client_secret.trim()) payload.ebay_client_secret = ebayForm.ebay_client_secret.trim();
                  payload.ebay_redirect_uri = ebayForm.ebay_redirect_uri.trim();
                  setSavingServer(true);
                  try {
                    await updateServerSettings(payload);
                    await reload();
                    setEbayForm((current) => ({ ...current, ebay_client_id: '', ebay_client_secret: '' }));
                    toast.success('eBay server settings saved.');
                  } catch (error) {
                    toast.error(error.message);
                  } finally {
                    setSavingServer(false);
                  }
                }}
              >
                <GuideCard
                  title="eBay onboarding"
                  description={MARKETPLACE_GUIDES.ebay.summary}
                  tooltip={MARKETPLACE_GUIDES.ebay.tooltip}
                  prerequisites={MARKETPLACE_GUIDES.ebay.prerequisites}
                  steps={MARKETPLACE_GUIDES.ebay.steps}
                />
                <InstructionTable title="eBay credentials" rows={CREDENTIAL_INSTRUCTIONS.ebay} />
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <label className="flex items-center gap-2 text-sm font-medium text-[#101828]">
                      App ID
                      <HelpTip label="eBay App ID help">This is the public application identifier from the eBay developer dashboard.</HelpTip>
                    </label>
                    <Input
                      value={ebayForm.ebay_client_id}
                      onChange={(event) => setEbayForm((current) => ({ ...current, ebay_client_id: event.target.value }))}
                      placeholder={settingsPanels?.ebay?.client_id_configured ? 'Configured on server' : 'Your eBay App ID'}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="flex items-center gap-2 text-sm font-medium text-[#101828]">
                      Cert ID
                      <HelpTip label="eBay Cert ID help">PosterPro stores the eBay client secret encrypted at rest after you save it.</HelpTip>
                    </label>
                    <Input
                      value={ebayForm.ebay_client_secret}
                      onChange={(event) => setEbayForm((current) => ({ ...current, ebay_client_secret: event.target.value }))}
                      placeholder={settingsPanels?.ebay?.client_secret_configured ? 'Configured on server' : 'Your eBay Cert ID'}
                    />
                  </div>
                </div>
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-sm font-medium text-[#101828]">
                    Redirect URI
                    <HelpTip label="eBay Redirect URI help">This must match the exact callback URL configured in the eBay developer app.</HelpTip>
                  </label>
                  <Input
                    value={ebayForm.ebay_redirect_uri}
                    onChange={(event) => setEbayForm((current) => ({ ...current, ebay_redirect_uri: event.target.value }))}
                    placeholder="https://posterpro.sparkleserver.site/api/ebay/callback"
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill status={settingsPanels?.ebay?.oauth_ready ? 'success' : 'warning'} label={settingsPanels?.ebay?.oauth_ready ? 'OAuth ready' : 'Credentials incomplete'} />
                  <StatusPill status={settingsPanels?.ebay?.connected ? 'success' : 'default'} label={settingsPanels?.ebay?.connected ? 'Connected' : 'Not connected'} />
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button type="submit" disabled={savingServer || !canManageServer}>
                    {savingServer ? 'Saving...' : 'Save eBay settings'}
                  </Button>
                  <Button type="button" variant="outline" onClick={connectEbay} disabled={connectingEbay || !settingsPanels?.ebay?.oauth_ready}>
                    {connectingEbay ? 'Opening OAuth...' : 'Connect eBay'}
                  </Button>
                </div>
                {!canManageServer ? <p className="text-sm text-[#667085]">Only the bootstrap admin can change server-side credentials.</p> : null}
                {ebayConnectError ? <p className="text-sm text-[#b42318]">{ebayConnectError}</p> : null}
              </form>
            </SectionPanel>
          ) : null}

          {activeTab === 'amazon' ? (
            <SectionPanel title="Amazon Vine + Media" description="Control the private Vine importer and optional Amazon media lookup for authorized users.">
              <form
                className="space-y-4"
                onSubmit={async (event) => {
                  event.preventDefault();
                  if (!canManageServer) {
                    toast.error('Admin access is required to change Amazon settings.');
                    return;
                  }
                  const payload = {
                    amazon_vine_import_enabled: amazonForm.amazon_vine_import_enabled,
                    amazon_vine_import_premium_only: amazonForm.amazon_vine_import_premium_only,
                    amazon_media_lookup_enabled: amazonForm.amazon_media_lookup_enabled,
                    amazon_media_page_fallback_enabled: amazonForm.amazon_media_page_fallback_enabled,
                    amazon_marketplace_region: amazonForm.amazon_marketplace_region,
                    amazon_media_fetch_mode: amazonForm.amazon_media_fetch_mode,
                    amazon_media_rate_limit_per_minute: amazonForm.amazon_media_rate_limit_per_minute,
                  };
                  if (amazonForm.amazon_paapi_access_key.trim()) payload.amazon_paapi_access_key = amazonForm.amazon_paapi_access_key.trim();
                  if (amazonForm.amazon_paapi_secret_key.trim()) payload.amazon_paapi_secret_key = amazonForm.amazon_paapi_secret_key.trim();
                  if (amazonForm.amazon_paapi_partner_tag.trim()) payload.amazon_paapi_partner_tag = amazonForm.amazon_paapi_partner_tag.trim();
                  setSavingServer(true);
                  try {
                    await updateServerSettings(payload);
                    await reload();
                    setAmazonForm((current) => ({
                      ...current,
                      amazon_paapi_access_key: '',
                      amazon_paapi_secret_key: '',
                      amazon_paapi_partner_tag: '',
                    }));
                    toast.success('Amazon settings saved.');
                  } catch (error) {
                    toast.error(error.message);
                  } finally {
                    setSavingServer(false);
                  }
                }}
              >
                <GuideCard
                  title="Amazon setup"
                  description={MARKETPLACE_GUIDES.amazon.summary}
                  tooltip={MARKETPLACE_GUIDES.amazon.tooltip}
                  prerequisites={MARKETPLACE_GUIDES.amazon.prerequisites}
                  steps={MARKETPLACE_GUIDES.amazon.steps}
                />
                <InstructionTable title="Amazon PA-API credentials" rows={CREDENTIAL_INSTRUCTIONS.amazon} />
                <div className="grid gap-3 md:grid-cols-2">
                  <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm text-[#101828]">
                    Enable Vine importer
                    <input
                      type="checkbox"
                      checked={amazonForm.amazon_vine_import_enabled}
                      onChange={(event) => setAmazonForm((current) => ({ ...current, amazon_vine_import_enabled: event.target.checked }))}
                    />
                  </label>
                  <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm text-[#101828]">
                    Premium-only gate
                    <input
                      type="checkbox"
                      checked={amazonForm.amazon_vine_import_premium_only}
                      onChange={(event) => setAmazonForm((current) => ({ ...current, amazon_vine_import_premium_only: event.target.checked }))}
                    />
                  </label>
                  <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm text-[#101828]">
                    Enable Amazon media lookup
                    <input
                      type="checkbox"
                      checked={amazonForm.amazon_media_lookup_enabled}
                      onChange={(event) => setAmazonForm((current) => ({ ...current, amazon_media_lookup_enabled: event.target.checked }))}
                    />
                  </label>
                  <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm text-[#101828]">
                    Page metadata fallback
                    <input
                      type="checkbox"
                      checked={amazonForm.amazon_media_page_fallback_enabled}
                      onChange={(event) => setAmazonForm((current) => ({ ...current, amazon_media_page_fallback_enabled: event.target.checked }))}
                    />
                  </label>
                </div>
                <div className="grid gap-4 md:grid-cols-3">
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Marketplace region</label>
                    <Input value={amazonForm.amazon_marketplace_region} onChange={(event) => setAmazonForm((current) => ({ ...current, amazon_marketplace_region: event.target.value.toUpperCase() }))} placeholder="US" />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Fetch mode</label>
                    <select
                      value={amazonForm.amazon_media_fetch_mode}
                      onChange={(event) => setAmazonForm((current) => ({ ...current, amazon_media_fetch_mode: event.target.value }))}
                      className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                    >
                      <option value="api_only">API only</option>
                      <option value="api_then_page_fallback">API then page metadata fallback</option>
                      <option value="manual_only">Manual only</option>
                    </select>
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Rate limit / minute</label>
                    <Input type="number" min="1" value={amazonForm.amazon_media_rate_limit_per_minute} onChange={(event) => setAmazonForm((current) => ({ ...current, amazon_media_rate_limit_per_minute: Number(event.target.value || 1) }))} />
                  </div>
                </div>
                <div className="grid gap-4 md:grid-cols-3">
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">PA-API access key</label>
                    <Input
                      value={amazonForm.amazon_paapi_access_key}
                      onChange={(event) => setAmazonForm((current) => ({ ...current, amazon_paapi_access_key: event.target.value }))}
                      placeholder={settingsPanels?.amazon?.paapi_access_key_configured ? settingsPanels.amazon.paapi_access_key_masked : 'Access key'}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">PA-API secret key</label>
                    <Input
                      value={amazonForm.amazon_paapi_secret_key}
                      onChange={(event) => setAmazonForm((current) => ({ ...current, amazon_paapi_secret_key: event.target.value }))}
                      placeholder={settingsPanels?.amazon?.paapi_secret_key_configured ? 'Configured on server' : 'Secret key'}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Partner tag</label>
                    <Input
                      value={amazonForm.amazon_paapi_partner_tag}
                      onChange={(event) => setAmazonForm((current) => ({ ...current, amazon_paapi_partner_tag: event.target.value }))}
                      placeholder={settingsPanels?.amazon?.paapi_partner_tag_configured ? settingsPanels.amazon.paapi_partner_tag_masked : 'partner-tag-20'}
                    />
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill status={settingsPanels?.amazon?.vine_import_enabled ? 'success' : 'default'} label={settingsPanels?.amazon?.vine_import_enabled ? 'Importer enabled' : 'Importer disabled'} />
                  <StatusPill status={settingsPanels?.amazon?.media_lookup_enabled ? 'success' : 'default'} label={settingsPanels?.amazon?.media_lookup_enabled ? 'Media lookup on' : 'Media lookup off'} />
                  <StatusPill status={settingsPanels?.amazon?.paapi_access_key_configured && settingsPanels?.amazon?.paapi_secret_key_configured ? 'success' : 'warning'} label={settingsPanels?.amazon?.paapi_access_key_configured && settingsPanels?.amazon?.paapi_secret_key_configured ? 'PA-API configured' : 'PA-API incomplete'} />
                </div>
                <Button type="submit" disabled={savingServer || !canManageServer}>
                  {savingServer ? 'Saving...' : 'Save Amazon settings'}
                </Button>
              </form>
            </SectionPanel>
          ) : null}

          {activeTab === 'marketplaces' ? (
            <SectionPanel title="Marketplaces" description="Control which channels are active for publishing and sales sync.">
              <div className="space-y-4">
                <GuideCard
                  title="Channel onboarding flow"
                  description="Every marketplace should move through the same sequence: confirm prerequisites, save account details, mark the workflow ready, then enable publishing or sales sync."
                  tooltip="This is meant to make marketplace onboarding feel structured rather than improvised."
                  prerequisites={['Server-level credentials saved where required', 'Real seller account chosen', 'Operator process documented']}
                  steps={[
                    'Open the marketplace setup and complete the account-specific instructions.',
                    'Confirm the readiness badge turns into Ready before enabling publishing.',
                    'Enable sales sync only on channels where PosterPro can truly monitor post-sale activity.',
                  ]}
                />
                {(setupSummary?.marketplace_connections || []).map((marketplace) => {
                  const publishingEnabled = publishingPlatforms.includes(marketplace.marketplace);
                  const salesEnabled = salePlatforms.includes(marketplace.marketplace);
                  const manualMode = marketplace.connection_mode === 'manual';
                  const guide = MARKETPLACE_GUIDES[marketplace.marketplace];
                  return (
                    <div key={marketplace.marketplace} className="rounded-[10px] border border-[#e5e7eb] bg-white p-4">
                      <div className="flex items-center justify-between gap-4">
                        <div>
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-semibold text-[#101828]">{MARKETPLACE_LABELS[marketplace.marketplace] || marketplace.marketplace}</p>
                            {guide?.tooltip ? <HelpTip label={`${marketplace.marketplace} setup help`}>{guide.tooltip}</HelpTip> : null}
                          </div>
                          <p className="mt-1 text-sm text-[#667085]">{marketplace.status_note}</p>
                          {guide?.summary ? <p className="mt-2 text-sm text-[#475467]">{guide.summary}</p> : null}
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusPill status={marketplace.available ? 'info' : 'warning'} label={marketplace.connection_mode === 'oauth' ? 'OAuth' : 'Manual'} />
                          <StatusPill status={marketplace.connected ? 'success' : 'default'} label={marketplace.connected ? 'Ready' : 'Not ready'} />
                        </div>
                      </div>
                      {(marketplace.display_name || marketplace.account_handle || marketplace.external_account_id) ? (
                        <div className="mt-3 rounded-[10px] bg-[#f8fafc] px-3 py-2 text-sm text-[#475467]">
                          <span className="font-medium text-[#101828]">Account:</span>{' '}
                          {marketplace.display_name || marketplace.account_handle || marketplace.external_account_id}
                        </div>
                      ) : null}
                      <div className="mt-4 flex flex-wrap gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            if (marketplace.marketplace === 'ebay') {
                              setActiveTab('ebay');
                              return;
                            }
                            openMarketplaceDrawer(marketplace);
                          }}
                        >
                          {marketplace.marketplace === 'ebay' ? 'Open eBay setup' : manualMode ? 'Configure account' : 'Review setup'}
                        </Button>
                        <Button
                          variant={publishingEnabled ? 'default' : 'outline'}
                          size="sm"
                          disabled={savingPublishing || !marketplace.can_publish}
                          onClick={async () => {
                            const next = publishingEnabled
                              ? publishingPlatforms.filter((name) => name !== marketplace.marketplace)
                              : [...publishingPlatforms, marketplace.marketplace];
                            setSavingPublishing(true);
                            try {
                              await updatePlatformConfig(user.id, next);
                              await reload();
                            } catch (error) {
                              toast.error(error.message);
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
                          disabled={savingSales || !marketplace.can_sync_sales}
                          onClick={async () => {
                            const next = salesEnabled
                              ? salePlatforms.filter((name) => name !== marketplace.marketplace)
                              : [...salePlatforms, marketplace.marketplace];
                            setSavingSales(true);
                            try {
                              await updateSaleDetectionSettings(user.id, next);
                              setSalePlatforms(next);
                              await reload();
                            } catch (error) {
                              toast.error(error.message);
                            } finally {
                              setSavingSales(false);
                            }
                          }}
                        >
                          {salesEnabled ? 'Sales sync on' : 'Enable sales sync'}
                        </Button>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <StatusPill status={marketplace.can_publish ? 'success' : 'warning'} label={marketplace.can_publish ? 'Publishing ready' : 'Publishing blocked'} />
                        <StatusPill status={marketplace.can_sync_sales ? 'success' : 'default'} label={marketplace.can_sync_sales ? 'Sales sync ready' : 'Sales sync unavailable'} />
                      </div>
                      {guide?.steps?.length ? (
                        <div className="mt-4 rounded-[12px] bg-[#f8fafc] p-3">
                          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">Setup steps</p>
                          <ol className="mt-2 space-y-2">
                            {guide.steps.map((step, index) => (
                              <li key={step} className="flex gap-3 text-sm text-[#475467]">
                                <span className="mt-[1px] flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white text-[11px] font-semibold text-[#2563eb]">
                                  {index + 1}
                                </span>
                                <span>{step}</span>
                              </li>
                            ))}
                          </ol>
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </SectionPanel>
          ) : null}

          {activeTab === 'automation' ? (
            <SectionPanel title="Automation" description="Tune how the server publishes, crossposts, and polls marketplace activity.">
              <form
                className="space-y-4"
                onSubmit={async (event) => {
                  event.preventDefault();
                  if (!canManageServer) {
                    toast.error('Admin access is required to change server automation.');
                    return;
                  }
                  setSavingServer(true);
                  try {
                    await updateServerSettings(automationForm);
                    await reloadDashboard();
                    await reload();
                    toast.success('Automation settings saved.');
                  } catch (error) {
                    toast.error(error.message);
                  } finally {
                    setSavingServer(false);
                  }
                }}
              >
                <div className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-[#f9fafb] px-4 py-3">
                  <div>
                    <p className="text-sm font-medium text-[#101828]">Automation mode</p>
                    <p className="mt-1 text-sm text-[#667085]">Global publish automation switch.</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusPill status={autonomousConfig?.autonomous_mode ? 'success' : 'default'} label={autonomousConfig?.autonomous_mode ? 'Enabled' : 'Disabled'} />
                    <Button
                      type="button"
                      variant="outline"
                      onClick={async () => {
                        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
                        await reloadDashboard();
                        await reload();
                      }}
                    >
                      Toggle
                    </Button>
                  </div>
                </div>
                <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm text-[#101828]">
                  Dry run mode
                  <input
                    type="checkbox"
                    checked={automationForm.autonomous_dry_run}
                    onChange={(event) => setAutomationForm((current) => ({ ...current, autonomous_dry_run: event.target.checked }))}
                  />
                </label>
                <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm text-[#101828]">
                  Crosspost automation
                  <input
                    type="checkbox"
                    checked={automationForm.autonomous_crosspost_enabled}
                    onChange={(event) => setAutomationForm((current) => ({ ...current, autonomous_crosspost_enabled: event.target.checked }))}
                  />
                </label>
                <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm text-[#101828]">
                  Sale detection enabled
                  <input
                    type="checkbox"
                    checked={automationForm.sale_detection_enabled}
                    onChange={(event) => setAutomationForm((current) => ({ ...current, sale_detection_enabled: event.target.checked }))}
                  />
                </label>
                <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm text-[#101828]">
                  Sale detection dry run
                  <input
                    type="checkbox"
                    checked={automationForm.sale_detection_dry_run}
                    onChange={(event) => setAutomationForm((current) => ({ ...current, sale_detection_dry_run: event.target.checked }))}
                  />
                </label>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">Sale polling interval (minutes)</label>
                  <Input
                    type="number"
                    min="1"
                    value={automationForm.sale_detection_poll_minutes}
                    onChange={(event) => setAutomationForm((current) => ({ ...current, sale_detection_poll_minutes: Number(event.target.value || 1) }))}
                  />
                </div>
                <Button type="submit" disabled={savingServer || !canManageServer}>
                  {savingServer ? 'Saving...' : 'Save automation'}
                </Button>
              </form>
            </SectionPanel>
          ) : null}

          {activeTab === 'api-keys' ? (
            <SectionPanel title="API Keys" description="Save runtime API keys on the server. Keys are never returned to the browser after save.">
              <form
                className="space-y-4"
                onSubmit={async (event) => {
                  event.preventDefault();
                  if (!canManageServer) {
                    toast.error('Admin access is required to change server credentials.');
                    return;
                  }
                  const payload = {};
                  if (apiKeyForm.openai_api_key.trim()) payload.openai_api_key = apiKeyForm.openai_api_key.trim();
                  if (apiKeyForm.photoroom_api_key.trim()) payload.photoroom_api_key = apiKeyForm.photoroom_api_key.trim();
                  setSavingServer(true);
                  try {
                    await updateServerSettings(payload);
                    await reload();
                    setApiKeyForm({ openai_api_key: '', photoroom_api_key: '' });
                    toast.success('API keys saved.');
                  } catch (error) {
                    toast.error(error.message);
                  } finally {
                    setSavingServer(false);
                  }
                }}
              >
                <div className="grid gap-4 lg:grid-cols-3">
                  <GuideCard
                    title={SERVICE_GUIDES.openai.title}
                    tooltip={SERVICE_GUIDES.openai.tooltip}
                    steps={SERVICE_GUIDES.openai.steps}
                  />
                  <GuideCard
                    title={SERVICE_GUIDES.photoroom.title}
                    tooltip={SERVICE_GUIDES.photoroom.tooltip}
                    steps={SERVICE_GUIDES.photoroom.steps}
                  />
                  <GuideCard
                    title={SERVICE_GUIDES.security.title}
                    tooltip={SERVICE_GUIDES.security.tooltip}
                    steps={SERVICE_GUIDES.security.steps}
                    tone="amber"
                  />
                </div>
                <div className="grid gap-4 xl:grid-cols-2">
                  <InstructionTable title="OpenAI credentials" rows={CREDENTIAL_INSTRUCTIONS.openai} />
                  <InstructionTable title="PhotoRoom credentials" rows={CREDENTIAL_INSTRUCTIONS.photoroom} />
                </div>
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-sm font-medium text-[#101828]">
                    OpenAI API key
                    <HelpTip label="OpenAI API key help">Saved keys are encrypted at rest and are not echoed back to the browser after save.</HelpTip>
                  </label>
                  <Input
                    value={apiKeyForm.openai_api_key}
                    onChange={(event) => setApiKeyForm((current) => ({ ...current, openai_api_key: event.target.value }))}
                    placeholder={settingsPanels?.api_keys?.openai_configured ? 'Configured on server' : 'sk-...'}
                  />
                </div>
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-sm font-medium text-[#101828]">
                    PhotoRoom API key
                    <HelpTip label="PhotoRoom API key help">Photo tools rely on this key for background removal and cleanup features.</HelpTip>
                  </label>
                  <Input
                    value={apiKeyForm.photoroom_api_key}
                    onChange={(event) => setApiKeyForm((current) => ({ ...current, photoroom_api_key: event.target.value }))}
                    placeholder={settingsPanels?.api_keys?.photoroom_configured ? 'Configured on server' : 'PhotoRoom key'}
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill status={settingsPanels?.api_keys?.openai_configured ? 'success' : 'warning'} label={settingsPanels?.api_keys?.openai_configured ? 'OpenAI ready' : 'OpenAI missing'} />
                  <StatusPill status={settingsPanels?.api_keys?.photoroom_configured ? 'success' : 'warning'} label={settingsPanels?.api_keys?.photoroom_configured ? 'PhotoRoom ready' : 'PhotoRoom missing'} />
                  <StatusPill status={settingsPanels?.server?.session_secret_configured ? 'success' : 'warning'} label={settingsPanels?.server?.session_secret_configured ? 'Encryption key ready' : 'Set SESSION_SECRET'} />
                </div>
                <Button type="submit" disabled={savingServer || !canManageServer}>
                  {savingServer ? 'Saving...' : 'Save API keys'}
                </Button>
                {!settingsPanels?.server?.session_secret_configured ? (
                  <p className="text-sm text-[#b42318]">
                    Configure a strong server `SESSION_SECRET` so runtime secrets stay protected if this install is self-hosted elsewhere.
                  </p>
                ) : null}
              </form>
            </SectionPanel>
          ) : null}

          {activeTab === 'server' ? (
            <SectionPanel title="Server" description="Deployment-level values that affect the whole app.">
              <form
                className="space-y-4"
                onSubmit={async (event) => {
                  event.preventDefault();
                  if (!canManageServer) {
                    toast.error('Admin access is required to change server settings.');
                    return;
                  }
                  setSavingServer(true);
                  try {
                    await updateServerSettings(serverForm);
                    await reload();
                    toast.success('Server settings saved.');
                  } catch (error) {
                    toast.error(error.message);
                  } finally {
                    setSavingServer(false);
                  }
                }}
              >
                <GuideCard
                  title="Server readiness"
                  description="Use this panel for deployment-wide values. It affects every account and every background worker on this install."
                  tooltip="This is the correct place for environment-wide storage and runtime settings, not per-user marketplace credentials."
                  prerequisites={['Admin session', 'Known storage path', 'Known deployment environment label']}
                  steps={[
                    'Set the environment label and storage root that match the live deployment.',
                    'Confirm Database, Redis, and SESSION_SECRET are configured before expanding automation.',
                    'Return to the setup center after changes to make sure readiness indicators updated cleanly.',
                  ]}
                  tone="slate"
                />
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2 md:col-span-2">
                    <label className="text-sm font-medium text-[#101828]">Public app URL</label>
                    <Input value={serverForm.app_base_url} onChange={(event) => setServerForm((current) => ({ ...current, app_base_url: event.target.value }))} placeholder="https://posterpro.sparkleserver.site" />
                    <p className="text-sm text-[#667085]">Used when PosterPro generates password reset links and other browser-return URLs.</p>
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Environment</label>
                    <Input value={serverForm.environment} onChange={(event) => setServerForm((current) => ({ ...current, environment: event.target.value }))} placeholder="production" />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Storage root</label>
                    <Input value={serverForm.storage_root} onChange={(event) => setServerForm((current) => ({ ...current, storage_root: event.target.value }))} placeholder="./storage" />
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-[#f9fafb] px-4 py-3">
                    <span className="text-sm font-medium text-[#101828]">Database</span>
                    <StatusPill status={settingsPanels?.server?.database_url_configured ? 'success' : 'error'} label={settingsPanels?.server?.database_url_configured ? 'Configured' : 'Missing'} />
                  </div>
                  <div className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-[#f9fafb] px-4 py-3">
                    <span className="text-sm font-medium text-[#101828]">Redis</span>
                    <StatusPill status={settingsPanels?.server?.redis_url_configured ? 'success' : 'error'} label={settingsPanels?.server?.redis_url_configured ? 'Configured' : 'Missing'} />
                  </div>
                  <div className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-[#f9fafb] px-4 py-3 md:col-span-2">
                    <span className="text-sm font-medium text-[#101828]">Session secret</span>
                    <StatusPill status={settingsPanels?.server?.session_secret_configured ? 'success' : 'error'} label={settingsPanels?.server?.session_secret_configured ? 'Configured' : 'Missing'} />
                  </div>
                </div>
                <Button type="submit" disabled={savingServer || !canManageServer}>
                  {savingServer ? 'Saving...' : 'Save server settings'}
                </Button>
                {!canManageServer ? <EmptyState title="Admin required" description="Only the bootstrap admin can change deployment settings from this panel." /> : null}
              </form>
            </SectionPanel>
          ) : null}

          {activeTab === 'email' ? (
            <SectionPanel title="Email Delivery" description="Configure SMTP so forgot-password becomes a complete email-based flow instead of a token-only utility.">
              <form
                className="space-y-4"
                onSubmit={async (event) => {
                  event.preventDefault();
                  if (!canManageServer) {
                    toast.error('Admin access is required to change email delivery.');
                    return;
                  }
                  const payload = {
                    smtp_host: emailForm.smtp_host.trim(),
                    smtp_port: Number(emailForm.smtp_port || 587),
                    smtp_username: emailForm.smtp_username.trim(),
                    smtp_from_email: emailForm.smtp_from_email.trim(),
                    smtp_from_name: emailForm.smtp_from_name.trim(),
                    smtp_use_tls: !!emailForm.smtp_use_tls,
                  };
                  if (emailForm.smtp_password.trim()) payload.smtp_password = emailForm.smtp_password.trim();
                  setSavingServer(true);
                  try {
                    await updateServerSettings(payload);
                    await reload();
                    setEmailForm((current) => ({ ...current, smtp_password: '' }));
                    toast.success('Email delivery settings saved.');
                  } catch (error) {
                    toast.error(error.message);
                  } finally {
                    setSavingServer(false);
                  }
                }}
              >
                <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(340px,0.95fr)]">
                  <div className="space-y-4">
                    <GuideCard
                      title="Forgot-password delivery"
                      description="This panel turns reset-token generation into a real operator email flow. Once SMTP is configured, PosterPro can email reset links directly from the production app."
                      tooltip="Use a transactional mail provider or mailbox that allows SMTP relay with a verified sender."
                      prerequisites={['Public app URL saved in Server settings', 'Verified sender email address', 'SMTP relay credentials from your mail provider']}
                      steps={[
                        'Save the public app URL in the Server tab first so reset links point to the right domain.',
                        'Enter the SMTP relay host, port, username, password, and verified sender address here.',
                        'Use the forgot-password page with a real account to confirm an email arrives and opens the reset form.',
                      ]}
                    />
                    <InstructionTable title="SMTP credentials" rows={CREDENTIAL_INSTRUCTIONS.email} />
                  </div>

                  <div className="space-y-4">
                    <div className="rounded-[16px] border border-[#e5e7eb] bg-white p-5">
                      <div className="grid gap-4">
                        <div className="space-y-2">
                          <label className="text-sm font-medium text-[#101828]">SMTP host</label>
                          <Input value={emailForm.smtp_host} onChange={(event) => setEmailForm((current) => ({ ...current, smtp_host: event.target.value }))} placeholder="smtp.mailgun.org" />
                        </div>
                        <div className="grid gap-4 md:grid-cols-2">
                          <div className="space-y-2">
                            <label className="text-sm font-medium text-[#101828]">SMTP port</label>
                            <Input type="number" value={emailForm.smtp_port} onChange={(event) => setEmailForm((current) => ({ ...current, smtp_port: Number(event.target.value || 587) }))} />
                          </div>
                          <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-[#fcfcfd] px-4 py-3 text-sm text-[#101828]">
                            Use TLS
                            <input type="checkbox" checked={emailForm.smtp_use_tls} onChange={(event) => setEmailForm((current) => ({ ...current, smtp_use_tls: event.target.checked }))} />
                          </label>
                        </div>
                        <div className="space-y-2">
                          <label className="text-sm font-medium text-[#101828]">SMTP username</label>
                          <Input value={emailForm.smtp_username} onChange={(event) => setEmailForm((current) => ({ ...current, smtp_username: event.target.value }))} placeholder="postmaster@mg.yourdomain.com" />
                        </div>
                        <div className="space-y-2">
                          <label className="text-sm font-medium text-[#101828]">SMTP password</label>
                          <Input type="password" value={emailForm.smtp_password} onChange={(event) => setEmailForm((current) => ({ ...current, smtp_password: event.target.value }))} placeholder={settingsPanels?.email?.password_configured ? 'Configured on server' : 'SMTP password or app password'} />
                        </div>
                        <div className="grid gap-4 md:grid-cols-2">
                          <div className="space-y-2">
                            <label className="text-sm font-medium text-[#101828]">From email</label>
                            <Input value={emailForm.smtp_from_email} onChange={(event) => setEmailForm((current) => ({ ...current, smtp_from_email: event.target.value }))} placeholder="noreply@yourdomain.com" />
                          </div>
                          <div className="space-y-2">
                            <label className="text-sm font-medium text-[#101828]">From name</label>
                            <Input value={emailForm.smtp_from_name} onChange={(event) => setEmailForm((current) => ({ ...current, smtp_from_name: event.target.value }))} placeholder="PosterPro" />
                          </div>
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusPill status={settingsPanels?.email?.configured ? 'success' : 'warning'} label={settingsPanels?.email?.configured ? 'Email delivery ready' : 'Email delivery incomplete'} />
                      <StatusPill status={settingsPanels?.server?.app_base_url ? 'success' : 'warning'} label={settingsPanels?.server?.app_base_url ? 'Public URL set' : 'Public URL missing'} />
                    </div>
                    <Button type="submit" disabled={savingServer || !canManageServer}>
                      {savingServer ? 'Saving...' : 'Save email settings'}
                    </Button>
                  </div>
                </div>
              </form>
            </SectionPanel>
          ) : null}
        </div>
      </div>

      <Drawer
        open={activeTab === 'marketplaces' && !!configuredMarketplace && configuredMarketplace.connection_mode === 'manual'}
        onClose={() => setSelectedMarketplace('')}
        title={configuredMarketplace ? `${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} account setup` : 'Marketplace setup'}
        description="Save the operator-facing account details for this channel, then mark it ready when this user can work it from PosterPro."
        widthClassName="xl:w-[520px]"
      >
        {configuredMarketplace ? (
          <form
            className="space-y-4"
            onSubmit={async (event) => {
              event.preventDefault();
              setSavingMarketplace(true);
              try {
                await updateMarketplaceConnection(user.id, configuredMarketplace.marketplace, marketplaceForm);
                await reload();
                toast.success(`${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} setup saved.`);
                setSelectedMarketplace('');
              } catch (error) {
                toast.error(error.message);
              } finally {
                setSavingMarketplace(false);
              }
            }}
          >
            {activeMarketplaceGuide ? (
              <GuideCard
                title={`${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} setup`}
                description={activeMarketplaceGuide.summary}
                tooltip={activeMarketplaceGuide.tooltip}
                prerequisites={activeMarketplaceGuide.prerequisites}
                steps={activeMarketplaceGuide.steps}
                tone="slate"
              />
            ) : null}
            <div className="space-y-2">
              <label className="text-sm font-medium text-[#101828]">Store or closet name</label>
              <Input
                value={marketplaceForm.display_name}
                onChange={(event) => setMarketplaceForm((current) => ({ ...current, display_name: event.target.value }))}
                placeholder="Main resale storefront"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-[#101828]">Account handle or username</label>
              <Input
                value={marketplaceForm.account_handle}
                onChange={(event) => setMarketplaceForm((current) => ({ ...current, account_handle: event.target.value }))}
                placeholder="@sparklescloset"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-[#101828]">Workflow notes</label>
              <textarea
                value={marketplaceForm.notes}
                onChange={(event) => setMarketplaceForm((current) => ({ ...current, notes: event.target.value }))}
                placeholder="Capture anything the operator needs to know: shipping profile, posting cadence, manual review steps, or account caveats."
                className="min-h-28 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-[#101828]">Workflow state</label>
              <select
                value={marketplaceForm.workflow_state}
                onChange={(event) => setMarketplaceForm((current) => ({ ...current, workflow_state: event.target.value }))}
                className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
              >
                <option value="draft">Save only</option>
                <option value="ready">Ready for operator workflow</option>
              </select>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill status={configuredMarketplace.connected ? 'success' : 'default'} label={configuredMarketplace.connected ? 'Ready now' : 'Not ready'} />
              <StatusPill status={configuredMarketplace.can_publish ? 'success' : 'warning'} label={configuredMarketplace.can_publish ? 'Publishing enabled after save' : 'Publishing blocked until ready'} />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={savingMarketplace}>
                {savingMarketplace ? 'Saving...' : 'Save marketplace setup'}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  setMarketplaceForm({
                    display_name: '',
                    account_handle: '',
                    notes: '',
                    workflow_state: 'draft',
                  })
                }
              >
                Clear form
              </Button>
            </div>
          </form>
        ) : null}
      </Drawer>
    </AppShell>
  );
}

SettingsPage.requireAuth = true;
