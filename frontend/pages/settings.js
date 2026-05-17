import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { RefreshCcw } from 'lucide-react';
import { useRouter } from 'next/router';
import toast from 'react-hot-toast';

import AppShell from '../components/layout/AppShell';
import CmsTemplateWorkspace from '../components/CmsTemplateWorkspace';
import Button from '../components/ui/button';
import Drawer from '../components/ui/drawer';
import EmptyState from '../components/ui/empty-state';
import FormSection from '../components/ui/form-section';
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
  createMarketplaceImportJob,
  fetchMarketplaceImportJob,
  fetchAccountSetupSummary,
  fetchBridgeAccounts,
  fetchSaleDetectionSettings,
  fetchSettingsPanels,
  importHostedPageTheme,
  importEbayTokens,
  publishHostedPages,
  runAutomationBridgeSmokeTest,
  toggleAutonomousMode,
  updateBridgeAccountSession,
  updateCurrentUser,
  updateHostedPages,
  updateMarketplaceConnection,
  updatePlatformConfig,
  updateSaleDetectionSettings,
  updateServerSettings,
  upsertBridgeAccount,
} from '../lib/api';
import { buildTemplateDraftForPage, CMS_PAGE_CONFIG, CMS_PAGE_KEYS, createDefaultCmsPages } from '../lib/cmsTemplates';

const SETTINGS_TABS = [
  { value: 'overview', label: 'Overview' },
  { value: 'profile', label: 'Profile' },
  { value: 'workflow', label: 'Workflow' },
  { value: 'ebay', label: 'eBay' },
  { value: 'amazon', label: 'Amazon' },
  { value: 'marketplaces', label: 'Marketplaces' },
  { value: 'automation', label: 'Automation' },
  { value: 'api-keys', label: 'API Keys' },
  { value: 'hosted-pages', label: 'CMS + Themes' },
  { value: 'email', label: 'Email' },
  { value: 'server', label: 'Server' },
];

const SETTINGS_GROUPS = [
  { label: 'Account', tabs: ['overview', 'profile', 'workflow'] },
  { label: 'Channels', tabs: ['ebay', 'amazon', 'marketplaces'] },
  { label: 'Admin', tabs: ['automation', 'api-keys', 'hosted-pages', 'email', 'server'] },
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

const BRIDGE_MARKETPLACE_OPTIONS = ['facebook', 'etsy', 'mercari', 'poshmark', 'depop', 'whatnot', 'vinted'];
const BROWSER_CONNECT_MARKETPLACES = ['facebook', 'mercari', 'poshmark', 'etsy', 'depop', 'whatnot', 'vinted'];
const BROWSER_IMPORT_MARKETPLACES = ['facebook'];

const MARKETPLACE_GUIDES = {
  ebay: {
    summary: 'Use the eBay app credentials plus the RuName-backed hosted pages to connect each operator account cleanly.',
    tooltip: 'PosterPro stores the server app settings, generates the three public URLs eBay asks for, and then lets each operator connect their own account.',
    prerequisites: ['Admin saves App ID, Cert ID, and RuName', 'Admin pastes the generated privacy, accepted, and declined URLs into the eBay developer RuName config', 'Operator signs into the correct eBay seller account'],
    steps: [
      'Open the eBay tab and save the server OAuth credentials, especially the RuName value from eBay.',
      'Open Hosted Pages and confirm the privacy policy and eBay landing pages are published at the generated URLs.',
      'Paste those three generated URLs into the matching eBay RuName fields, then click Connect eBay from the operator account you want tied to this workspace.',
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
  etsy: {
    summary: 'Etsy is modeled as a catalog-first manual/provider-assisted channel with stronger product-specific prep than a casual resale marketplace.',
    tooltip: 'Use this setup to capture shop identity, fulfillment defaults, and handmade or vintage listing notes before the team publishes.',
    prerequisites: ['Shop name', 'Etsy seller handle', 'Production or fulfillment notes', 'Category and attribute expectations for handmade or vintage items'],
    steps: [
      'Save the Etsy shop identity and operator handle that will own the listings.',
      'Document fulfillment, production, and attribute expectations in notes so repeatable product data is not improvised.',
      'Move the channel to Ready only after the team has validated the listing template and shipping workflow for Etsy.',
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
    { field: 'RuName / redirect_uri value', where: 'eBay Developers Program -> User Tokens / RuName', how: 'Copy the OAuth-enabled RuName exactly as eBay generated it, for example matthew_ruderma-matthewr-poster-cyatix.', purpose: 'This is the exact redirect_uri value eBay expects in the OAuth authorize and token-exchange flow.' },
    { field: 'Privacy Policy URL', where: 'eBay Developers Program -> User Tokens / RuName', how: 'Use the generated Hosted Pages privacy policy URL from PosterPro and paste it into the RuName settings.', purpose: 'Required by eBay for user-token OAuth applications.' },
    { field: 'Auth Accepted URL', where: 'eBay Developers Program -> User Tokens / RuName', how: 'Use the generated Hosted Pages accepted URL from PosterPro and paste it into the RuName settings.', purpose: 'This is where eBay sends the operator after they approve PosterPro access.' },
    { field: 'Auth Declined URL', where: 'eBay Developers Program -> User Tokens / RuName', how: 'Use the generated Hosted Pages declined URL from PosterPro and paste it into the RuName settings.', purpose: 'This is where eBay sends the operator if they decline or cancel the consent flow.' },
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

const DEFAULT_THEME_IMPORT_TEMPLATE = JSON.stringify(
  {
    activate_theme_id: 'boardroom-ink',
    themes: [
      {
        id: 'boardroom-ink',
        name: 'Boardroom Ink',
        description: 'A restrained corporate theme with a darker hero and high-clarity policy pages.',
        hero_eyebrow: 'Investor-grade operations',
        hero_title: 'Clean public handoff pages',
        hero_body: 'Use this theme when the hosted CMS should feel more like a polished SaaS trust center than a default product install.',
        layout: {
          align: 'left',
          content_width: '920px',
          hero_style: 'split-band',
          card_style: 'elevated',
          show_brand_badge: true,
        },
        palette: {
          page_background: 'linear-gradient(180deg, #edf2f8 0%, #ffffff 45%, #eef2f7 100%)',
          hero_background: 'linear-gradient(135deg, #111827 0%, #1f2937 46%, #2563eb 100%)',
          hero_foreground: '#f8fafc',
          surface_background: 'rgba(255, 255, 255, 0.97)',
          surface_foreground: '#0f172a',
          surface_muted: '#475467',
          border_color: '#d0d9e5',
          accent_color: '#1d4ed8',
          accent_soft: '#dbeafe',
          success_color: '#166534',
          warning_color: '#b54708',
          danger_color: '#b42318',
        },
        typography: {
          font_family: "'Plus Jakarta Sans', 'Segoe UI', sans-serif",
          heading_family: "'Plus Jakarta Sans', 'Segoe UI', sans-serif",
          base_size: '15px',
        },
        chrome: {
          footer_note: 'Boardroom Ink is intended as a clean baseline import for PosterPro hosted CMS pages.',
          primary_cta_label: 'Open PosterPro',
          secondary_cta_label: 'Close window',
        },
      },
    ],
  },
  null,
  2,
);

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
  const [testingBridge, setTestingBridge] = useState(false);
  const [bridgeSmokeResult, setBridgeSmokeResult] = useState(null);
  const [bridgeAccounts, setBridgeAccounts] = useState([]);
  const [bridgeAccountForm, setBridgeAccountForm] = useState({
    marketplace: 'facebook',
    account_key: '',
    display_name: '',
    login_handle: '',
    credential_secret: '',
    notes: '',
    provider_enabled: false,
    browser_enabled: true,
    session_state: 'draft',
    session_payload_text: '',
    expires_at: '',
  });
  const [savingBridgeAccount, setSavingBridgeAccount] = useState(false);
  const [savingPublishing, setSavingPublishing] = useState(false);
  const [savingSales, setSavingSales] = useState(false);
  const [savingMarketplace, setSavingMarketplace] = useState(false);
  const [savingBrowserSession, setSavingBrowserSession] = useState(false);
  const [launchingBrowserWorkspace, setLaunchingBrowserWorkspace] = useState(false);
  const [runningMarketplaceImport, setRunningMarketplaceImport] = useState(false);
  const [selectedMarketplace, setSelectedMarketplace] = useState('');
  const [marketplaceForm, setMarketplaceForm] = useState({
    display_name: '',
    account_handle: '',
    notes: '',
    workflow_state: 'draft',
    import_mode: 'manual',
    publish_mode: 'manual_review',
    shipping_scope: 'local_only',
    renewal_mode: 'manual',
    support_url: '',
    bridge_account_key: '',
    import_listing_limit: 10,
    bridge_session_state: 'draft',
    bridge_session_payload_text: '',
  });
  const activeBridgeConnectSession = setupSummary?.active_bridge_connect_session || null;
  const activeBridgeConnectMarketplace = String(activeBridgeConnectSession?.marketplace || '').toLowerCase();
  const [ebayForm, setEbayForm] = useState({ ebay_client_id: '', ebay_client_secret: '', ebay_redirect_uri: '' });
  const [hostedPagesForm, setHostedPagesForm] = useState({
    brand_name: 'PosterPro',
    active_theme_id: 'corporate-sky',
    pages: createDefaultCmsPages(),
  });
  const [themeImportForm, setThemeImportForm] = useState({
    theme_pack_json: DEFAULT_THEME_IMPORT_TEMPLATE,
    replace_existing: false,
    activate_imported: true,
  });
  const [activeCmsPreview, setActiveCmsPreview] = useState(CMS_PAGE_KEYS[0]);
  const [ebayTokenForm, setEbayTokenForm] = useState({
    access_token: '',
    refresh_token: '',
    expires_in_seconds: 7200,
    external_account_id: '',
  });
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
    automation_bridge_enabled: false,
    automation_bridge_url: '',
    automation_bridge_timeout_seconds: 30,
    automation_bridge_api_key: '',
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
  const selectTab = (nextTab) => {
    setActiveTab(nextTab);
    if (!router.isReady) return;
    const nextQuery = { ...router.query, tab: nextTab };
    router.replace({ pathname: router.pathname, query: nextQuery }, undefined, { shallow: true });
  };
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
  const settingsSubnav = useMemo(
    () => ({
      eyebrow: 'Settings CMS',
      title: 'Configuration',
      description: 'Move through account, channel, and platform configuration from a dedicated admin rail instead of a single stacked settings page.',
      sections: visibleTabGroups.map((group) => ({
        label: group.label,
        items: group.tabs.map((tab) => ({
          key: tab.value,
          label: tab.label,
          active: activeTab === tab.value,
          description:
            tab.value === 'overview'
              ? 'Control center'
              : tab.value === 'ebay'
              ? 'OAuth and account connection'
              : tab.value === 'hosted-pages'
              ? 'CMS themes and public pages'
              : tab.value === 'server'
              ? 'Deployment settings'
              : tab.value === 'workflow'
              ? 'Operator rules'
              : tab.value === 'marketplaces'
              ? 'Channel readiness'
              : undefined,
          onClick: () => selectTab(tab.value),
        })),
      })),
    }),
    [activeTab, visibleTabGroups],
  );

  const reload = async () => {
    if (!user?.id) return;
    setLoading(true);
    try {
      const [summary, salesConfig, panels, bridgeAccountData] = await Promise.all([
        fetchAccountSetupSummary(user.id),
        fetchSaleDetectionSettings(user.id),
        fetchSettingsPanels(),
        fetchBridgeAccounts().catch(() => ({ accounts: [] })),
      ]);
      setSetupSummary(summary);
      setSalePlatforms(salesConfig.marketplaces || []);
      setSettingsPanels(panels);
      setBridgeAccounts(bridgeAccountData?.accounts || []);
      setProfileName(panels.profile.full_name || '');
      setEbayForm({
        ebay_client_id: '',
        ebay_client_secret: '',
        ebay_redirect_uri: panels.ebay.runame || panels.ebay.redirect_uri || '',
      });
      setHostedPagesForm({
        brand_name: panels.hosted_pages?.brand_name || 'PosterPro',
        active_theme_id: panels.hosted_pages?.active_theme_id || 'corporate-sky',
        pages: panels.hosted_pages?.pages || {},
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
        automation_bridge_enabled: !!panels.automation.automation_bridge_enabled,
        automation_bridge_url: panels.automation.automation_bridge_url || '',
        automation_bridge_timeout_seconds: Number(panels.automation.automation_bridge_timeout_seconds || 30),
        automation_bridge_api_key: '',
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
    const handleStorage = (event) => {
      if (!String(event.key || '').startsWith('posterpro-marketplace-connect-complete:') || !event.newValue) return;
      let payload = null;
      try {
        payload = JSON.parse(event.newValue);
      } catch (error) {
        payload = null;
      }
      reload();
      const marketplace = String(payload?.marketplace || event.key.split(':').pop() || 'marketplace');
      toast.success(`${MARKETPLACE_LABELS[marketplace] || marketplace} account connected and bridge session captured.`);
    };
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, [reload]);

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
  const activeHostedTheme = useMemo(
    () => (settingsPanels?.hosted_pages?.themes || []).find((theme) => theme.id === hostedPagesForm.active_theme_id) || null,
    [hostedPagesForm.active_theme_id, settingsPanels?.hosted_pages?.themes],
  );
  const updateCmsPage = (pageKey, updater) => {
    setHostedPagesForm((current) => {
      const page = current.pages?.[pageKey] || createDefaultCmsPages()[pageKey];
      const nextPage = typeof updater === 'function' ? updater(page) : updater;
      return {
        ...current,
        pages: {
          ...(current.pages || {}),
          [pageKey]: nextPage,
        },
      };
    });
  };
  const cmsPreviewPages = useMemo(
    () =>
      CMS_PAGE_CONFIG.map((entry) => {
        const key = entry.key;
        const pageRecord = hostedPagesForm.pages?.[key] || {};
        const draft = pageRecord.draft || {};
        return {
          key,
          label: entry.label,
          pageRecord,
          page: {
            brand_name: hostedPagesForm.brand_name || 'PosterPro',
            title: draft.title || 'Untitled page',
            summary: draft.summary || '',
            hero: draft.hero || {},
            primary_button: draft.primary_button || {},
            secondary_button: draft.secondary_button || {},
            blocks: draft.blocks || [],
            theme: activeHostedTheme || {},
          },
          statusTone: entry.statusTone,
          statusMessage: entry.statusMessage,
        };
      }),
    [activeHostedTheme, hostedPagesForm],
  );
  const activeCmsPreviewEntry = useMemo(
    () => cmsPreviewPages.find((entry) => entry.key === activeCmsPreview) || cmsPreviewPages[0] || null,
    [activeCmsPreview, cmsPreviewPages],
  );
  const applyCmsTemplate = (pageKey) => {
    const templateDraft = buildTemplateDraftForPage(pageKey);
    updateCmsPage(pageKey, (currentPage) => ({
      ...currentPage,
      slug: templateDraft.slug,
      route_group: templateDraft.route_group,
      draft: templateDraft.draft,
    }));
    setActiveCmsPreview(pageKey);
  };
  const applyCmsTemplatePack = () => {
    setHostedPagesForm((current) => ({
      ...current,
      pages: CMS_PAGE_KEYS.reduce((pages, key) => {
        const existing = current.pages?.[key] || createDefaultCmsPages()[key];
        const templateDraft = buildTemplateDraftForPage(key);
        pages[key] = {
          ...existing,
          slug: templateDraft.slug,
          route_group: templateDraft.route_group,
          draft: templateDraft.draft,
        };
        return pages;
      }, {}),
    }));
    setActiveCmsPreview(CMS_PAGE_KEYS[0]);
  };

  const openMarketplaceDrawer = (marketplace) => {
    if (!marketplace) return;
    const usesBrowserAssistDefaults = BROWSER_CONNECT_MARKETPLACES.includes(marketplace.marketplace);
    setSelectedMarketplace(marketplace.marketplace);
    setMarketplaceForm({
      display_name: marketplace.display_name || '',
      account_handle: marketplace.account_handle || '',
      notes: marketplace.notes || '',
      workflow_state: marketplace.workflow_state || 'draft',
      import_mode: marketplace.import_mode || (usesBrowserAssistDefaults && BROWSER_IMPORT_MARKETPLACES.includes(marketplace.marketplace) ? 'browser_assist' : 'manual'),
      publish_mode: marketplace.publish_mode || (usesBrowserAssistDefaults ? 'browser_assist' : 'manual_review'),
      shipping_scope: marketplace.shipping_scope || 'local_only',
      renewal_mode: marketplace.renewal_mode || 'manual',
      support_url: marketplace.support_url || '',
      bridge_account_key: marketplace.bridge_account_key || '',
      import_listing_limit: Number(marketplace.import_listing_limit || 10),
      bridge_session_state: 'draft',
      bridge_session_payload_text: '',
    });
  };

  const activeMarketplaceGuide =
    MARKETPLACE_GUIDES[selectedMarketplace] ||
    MARKETPLACE_GUIDES[configuredMarketplace?.marketplace] ||
    null;
  const getBridgeAccountForMarketplace = (marketplace) => {
    if (!marketplace) return null;
    const accountKey = String(marketplace.bridge_account_key || '').trim().toLowerCase();
    if (!accountKey) return null;
    return (
      bridgeAccounts.find(
        (account) =>
          account.marketplace === marketplace.marketplace &&
          account.account_key === accountKey,
      ) || null
    );
  };
  const isBridgeSessionReady = (account) => (
    !!account && ['ready', 'active', 'valid'].includes(String(account.session_state || '').toLowerCase())
  );
  const selectedBridgeAccount = useMemo(
    () =>
      bridgeAccounts.find(
        (account) =>
          account.marketplace === configuredMarketplace?.marketplace &&
          account.account_key === String(marketplaceForm.bridge_account_key || '').trim().toLowerCase(),
      ) || null,
    [bridgeAccounts, configuredMarketplace?.marketplace, marketplaceForm.bridge_account_key],
  );
  const browserConnectInProgress =
    !!configuredMarketplace &&
    activeBridgeConnectMarketplace === String(configuredMarketplace.marketplace || '').toLowerCase();

  useEffect(() => {
    if (!BROWSER_CONNECT_MARKETPLACES.includes(String(configuredMarketplace?.marketplace || '')) || !selectedBridgeAccount) return;
    setMarketplaceForm((current) => {
      if ((current.bridge_session_payload_text || '').trim()) return current;
      return {
        ...current,
        bridge_session_state: selectedBridgeAccount.session_state || current.bridge_session_state,
        bridge_session_payload_text: JSON.stringify(selectedBridgeAccount.session_payload || {}, null, 2),
      };
    });
  }, [configuredMarketplace?.marketplace, selectedBridgeAccount]);
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

  const saveBrowserSession = async () => {
    const marketplaceName = String(configuredMarketplace?.marketplace || '').toLowerCase();
    if (!BROWSER_CONNECT_MARKETPLACES.includes(marketplaceName)) {
      toast.error('Browser session saving is only available for browser-assist marketplaces.');
      return;
    }
    const accountKey = String(marketplaceForm.bridge_account_key || '').trim().toLowerCase();
    if (!accountKey) {
      toast.error('Bridge account key is required.');
      return;
    }
    let sessionPayload = {};
    try {
      sessionPayload = marketplaceForm.bridge_session_payload_text.trim()
        ? JSON.parse(marketplaceForm.bridge_session_payload_text)
        : {};
    } catch (error) {
      toast.error('Session payload JSON is invalid.');
      return;
    }

    setSavingBrowserSession(true);
    try {
      await upsertBridgeAccount(marketplaceName, accountKey, {
        display_name: marketplaceForm.display_name || configuredMarketplace?.display_name || MARKETPLACE_LABELS[marketplaceName] || marketplaceName,
        login_handle: marketplaceForm.account_handle || configuredMarketplace?.account_handle || '',
        credential_secret: selectedBridgeAccount?.credential_configured ? undefined : `${marketplaceName}-session-managed-in-dashboard`,
        notes: marketplaceForm.notes,
        provider_enabled: false,
        browser_enabled: true,
        session_state: marketplaceForm.bridge_session_state,
        session_payload: sessionPayload,
      });
      await updateBridgeAccountSession(marketplaceName, accountKey, {
        session_state: marketplaceForm.bridge_session_state,
        session_payload: sessionPayload,
        last_tested_at: new Date().toISOString(),
        notes: marketplaceForm.notes,
      });
      await reload();
      toast.success(`${MARKETPLACE_LABELS[marketplaceName] || marketplaceName} browser session saved.`);
    } catch (error) {
      toast.error(error.message);
    } finally {
      setSavingBrowserSession(false);
    }
  };

  const launchBrowserConnectWorkspace = async ({ marketplace, accountKey, displayName, loginHandle, notes }) => {
    if (!automationForm.automation_bridge_enabled || !String(automationForm.automation_bridge_url || '').trim()) {
      toast.error('Enable and configure the automation bridge first.');
      selectTab('automation');
      return;
    }
    if (!accountKey) {
      toast.error('Bridge account key is required.');
      return;
    }

    setLaunchingBrowserWorkspace(true);
    try {
      const params = new URLSearchParams({
        marketplace,
        accountKey,
        displayName: displayName || MARKETPLACE_LABELS[marketplace] || marketplace,
        notes: notes || '',
      });
      if (String(loginHandle || '').trim()) {
        params.set('loginHandle', String(loginHandle).trim());
      }
      const href = `/bridge-desktop?${params.toString()}`;
      await router.push(href);
    } catch (error) {
      toast.error(error.message);
    } finally {
      setLaunchingBrowserWorkspace(false);
    }
  };

  const connectBrowserMarketplaceAccount = async () => {
    const marketplaceName = String(configuredMarketplace?.marketplace || '').toLowerCase();
    if (!BROWSER_CONNECT_MARKETPLACES.includes(marketplaceName)) {
      toast.error('Browser connect is only available for configured browser-assist marketplaces.');
      return;
    }
    await launchBrowserConnectWorkspace({
      marketplace: marketplaceName,
      accountKey: String(marketplaceForm.bridge_account_key || '').trim().toLowerCase(),
      displayName: marketplaceForm.display_name || configuredMarketplace?.display_name || MARKETPLACE_LABELS[marketplaceName] || marketplaceName,
      loginHandle: marketplaceForm.account_handle || '',
      notes: marketplaceForm.notes,
    });
  };

  const connectBrowserMarketplaceFromCard = async (marketplace) => {
    if (!marketplace || !BROWSER_CONNECT_MARKETPLACES.includes(String(marketplace.marketplace || '').toLowerCase())) {
      return;
    }
    const accountKey = String(marketplace.bridge_account_key || '').trim().toLowerCase();
    if (!accountKey) {
      openMarketplaceDrawer(marketplace);
      toast.error(`Save a bridge account key in ${MARKETPLACE_LABELS[marketplace.marketplace] || marketplace.marketplace} setup before running the live connect flow.`);
      return;
    }
    setSelectedMarketplace(marketplace.marketplace);
    await launchBrowserConnectWorkspace({
      marketplace: marketplace.marketplace,
      accountKey,
      displayName: marketplace.display_name || MARKETPLACE_LABELS[marketplace.marketplace] || marketplace.marketplace,
      loginHandle: '',
      notes: marketplace.notes || '',
    });
  };

  const waitForMarketplaceImportJob = async (jobId, { timeoutMs = 180000, pollMs = 1500 } = {}) => {
    const deadline = Date.now() + timeoutMs;
    let latestJob = await fetchMarketplaceImportJob(jobId);
    while (!['completed', 'failed', 'canceled'].includes(String(latestJob?.status || '').toLowerCase())) {
      if (Date.now() >= deadline) {
        throw new Error(`Facebook import job ${jobId} did not finish within ${Math.round(timeoutMs / 1000)} seconds.`);
      }
      await new Promise((resolve) => setTimeout(resolve, pollMs));
      latestJob = await fetchMarketplaceImportJob(jobId);
    }
    return latestJob;
  };

  const runFacebookImport = async ({
    accountKey,
    displayName,
    importMode = 'browser_assist',
    maxListings = 10,
  }) => {
    const normalizedAccountKey = String(accountKey || '').trim().toLowerCase();
    if (!normalizedAccountKey) {
      throw new Error('Save a bridge account key first.');
    }

    setRunningMarketplaceImport(true);
    try {
      const job = await createMarketplaceImportJob({
        source_marketplace: 'facebook',
        source_listing_reference: 'https://www.facebook.com/marketplace/you/selling',
        import_mode: importMode || 'browser_assist',
        payload: {
          account_key: normalizedAccountKey,
          max_listings: Number(maxListings || 10),
          seller_label: displayName || '',
        },
      });
      const finalJob = await waitForMarketplaceImportJob(job.id);
      const status = String(finalJob?.status || '').toLowerCase();
      if (status === 'failed') {
        throw new Error(finalJob?.last_error || `Facebook import job ${job.id} failed.`);
      }
      if (status === 'canceled') {
        throw new Error(`Facebook import job ${job.id} was canceled.`);
      }

      const createdListingIds = finalJob?.normalized_preview?.created_listing_ids || [];
      const newListingIds = finalJob?.normalized_preview?.new_listing_ids || [];
      const reusedListingIds = finalJob?.normalized_preview?.reused_listing_ids || [];
      const importedCount = Number(createdListingIds.length || 0);
      if (importedCount > 0 || finalJob?.created_listing_id) {
        const count = importedCount || 1;
        if (reusedListingIds.length && !newListingIds.length) {
          toast.success(`Reused ${count} existing Facebook draft${count === 1 ? '' : 's'} in PosterPro.`);
        } else if (reusedListingIds.length) {
          toast.success(
            `Prepared ${count} Facebook draft${count === 1 ? '' : 's'} in PosterPro (${newListingIds.length} new, ${reusedListingIds.length} existing).`
          );
        } else {
          toast.success(`Imported ${count} Facebook listing${count === 1 ? '' : 's'} into PosterPro drafts.`);
        }
        await reload();
        await router.push('/listings?tab=drafts');
        return;
      }

      toast.success(`Facebook import job ${job.id} finished. Review the import job details if no drafts were created.`);
      await reload();
      await router.push('/jobs');
    } finally {
      setRunningMarketplaceImport(false);
    }
  };

  const importFacebookFromMarketplaceCard = async (marketplace) => {
    if (!marketplace || marketplace.marketplace !== 'facebook') {
      return;
    }
    const accountKey = String(marketplace.bridge_account_key || '').trim().toLowerCase();
    if (!accountKey) {
      openMarketplaceDrawer(marketplace);
      toast.error('Save a bridge account key in Facebook setup before importing listings.');
      return;
    }
    const bridgeAccount = getBridgeAccountForMarketplace(marketplace);
    if (!isBridgeSessionReady(bridgeAccount)) {
      openMarketplaceDrawer(marketplace);
      toast.error('Connect Facebook first so PosterPro has an active browser session to import from.');
      return;
    }
    if ((marketplace.import_mode || 'manual') !== 'browser_assist') {
      openMarketplaceDrawer(marketplace);
      toast.error('Set Facebook import mode to browser assist before importing listings.');
      return;
    }
    setSelectedMarketplace(marketplace.marketplace);
    await runFacebookImport({
      accountKey,
      displayName: marketplace.display_name || marketplace.account_handle || 'Facebook Marketplace',
      importMode: marketplace.import_mode || 'browser_assist',
      maxListings: Number(marketplace.import_listing_limit || 10),
    });
  };

  const importExistingFacebookListings = async () => {
    if (configuredMarketplace?.marketplace !== 'facebook') {
      toast.error('This import action is only available for Facebook Marketplace.');
      return;
    }
    const accountKey = String(marketplaceForm.bridge_account_key || '').trim().toLowerCase();
    if (!accountKey) {
      toast.error('Save a bridge account key first.');
      return;
    }

    try {
      if (!isBridgeSessionReady(selectedBridgeAccount)) {
        toast.error('Connect Facebook first so PosterPro has an active browser session to import from.');
        return;
      }
      await runFacebookImport({
        accountKey,
        displayName: marketplaceForm.display_name || marketplaceForm.account_handle || '',
        importMode: marketplaceForm.import_mode || 'browser_assist',
        maxListings: Number(marketplaceForm.import_listing_limit || 10),
      });
    } catch (error) {
      toast.error(error.message);
    }
  };

  return (
    <AppShell
      active="/settings"
      title="Settings"
      autonomousConfig={autonomousConfig}
      subnav={settingsSubnav}
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
                      <button type="button" onClick={() => selectTab('profile')} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
                        <p className="text-sm font-semibold text-[#101828]">Profile</p>
                        <p className="mt-1 text-sm text-[#667085]">Operator name, password changes, and account-level identity.</p>
                      </button>
                      <button type="button" onClick={() => selectTab('workflow')} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
                        <p className="text-sm font-semibold text-[#101828]">Workflow</p>
                        <p className="mt-1 text-sm text-[#667085]">Review-before-publish, bulk approvals, and preview layout.</p>
                      </button>
                      <button type="button" onClick={() => selectTab('ebay')} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
                        <p className="text-sm font-semibold text-[#101828]">eBay account</p>
                        <p className="mt-1 text-sm text-[#667085]">Server OAuth setup plus the current user connection state.</p>
                      </button>
                      <button type="button" onClick={() => selectTab('marketplaces')} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
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
                        ['CMS + Themes', 'Hosted public pages, theme imports, and clean OAuth handoff content.', 'hosted-pages'],
                        ['Email Delivery', 'SMTP relay settings for real forgot-password delivery.', 'email'],
                        ['Server', 'Public URL, storage root, and deployment-wide environment values.', 'server'],
                        ['Automation', 'Global publish and polling behavior that affects every user.', 'automation'],
                      ].map(([title, note, tab]) => (
                        <button key={tab} type="button" onClick={() => selectTab(tab)} className="w-full rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
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
            <SectionPanel title="eBay" description="Configure the server app, publish the three required public URLs, and connect the current operator account.">
              <div className="space-y-6">
                <GuideCard
                  title="eBay onboarding"
                  description={MARKETPLACE_GUIDES.ebay.summary}
                  tooltip={MARKETPLACE_GUIDES.ebay.tooltip}
                  prerequisites={MARKETPLACE_GUIDES.ebay.prerequisites}
                  steps={MARKETPLACE_GUIDES.ebay.steps}
                />
                <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(340px,0.85fr)]">
                  <form
                    className="space-y-4 rounded-[18px] border border-[#e5e7eb] bg-[#fcfcfd] p-5"
                    onSubmit={async (event) => {
                      event.preventDefault();
                      if (!canManageServer) {
                        toast.error('Admin access is required to change server credentials.');
                        return;
                      }
                      const payload = {};
                      if (ebayForm.ebay_client_id.trim()) payload.ebay_client_id = ebayForm.ebay_client_id.trim();
                      if (ebayForm.ebay_client_secret.trim()) payload.ebay_client_secret = ebayForm.ebay_client_secret.trim();
                      payload.ebay_runame = ebayForm.ebay_redirect_uri.trim();
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
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-[#101828]">Server OAuth app</p>
                        <p className="mt-1 text-sm text-[#667085]">These values belong to the hosted PosterPro deployment, not to an individual operator account.</p>
                      </div>
                      <StatusPill status={settingsPanels?.ebay?.oauth_ready ? 'success' : 'warning'} label={settingsPanels?.ebay?.oauth_ready ? 'OAuth ready' : 'Credentials incomplete'} />
                    </div>
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
                        RuName / redirect_uri value
                        <HelpTip label="eBay RuName help">For eBay user OAuth, redirect_uri is the RuName value, not a raw callback URL. Paste the exact OAuth-enabled RuName from eBay.</HelpTip>
                      </label>
                      <Input
                        value={ebayForm.ebay_redirect_uri}
                        onChange={(event) => setEbayForm((current) => ({ ...current, ebay_redirect_uri: event.target.value }))}
                        placeholder="matthew_ruderma-matthewr-poster-cyatix"
                      />
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button type="submit" disabled={savingServer || !canManageServer}>
                        {savingServer ? 'Saving...' : 'Save eBay app settings'}
                      </Button>
                      {!canManageServer ? <p className="text-sm text-[#667085]">Only the bootstrap admin can change server-side credentials.</p> : null}
                    </div>
                  </form>

                  <div className="space-y-4">
                    <div className="rounded-[18px] border border-[#dbe7ff] bg-[#f7faff] p-5">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[#101828]">Required eBay URLs</p>
                          <p className="mt-1 text-sm text-[#667085]">Paste these exact values into the matching RuName fields inside the eBay developer dashboard.</p>
                        </div>
                        <StatusPill status={settingsPanels?.server?.app_base_url ? 'info' : 'warning'} label={settingsPanels?.server?.app_base_url ? 'Base URL ready' : 'App base URL missing'} />
                      </div>
                      <div className="mt-4 space-y-3">
                        {[
                          ['Privacy Policy URL', settingsPanels?.ebay?.privacy_policy_url],
                          ['Auth Accepted URL', settingsPanels?.ebay?.auth_accepted_url],
                          ['Auth Declined URL', settingsPanels?.ebay?.auth_declined_url],
                        ].map(([label, value]) => (
                          <div key={label} className="rounded-[12px] border border-white/80 bg-white/90 p-3">
                            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#667085]">{label}</p>
                            <p className="mt-2 break-all text-sm text-[#101828]">{value || 'Set APP_BASE_URL and Hosted Pages slugs to generate this URL.'}</p>
                          </div>
                        ))}
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        <Button type="button" variant="outline" onClick={() => selectTab('hosted-pages')}>
                          Open hosted pages CMS
                        </Button>
                      </div>
                    </div>

                    <div className="rounded-[18px] border border-[#e5e7eb] bg-white p-5">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[#101828]">Current operator connection</p>
                          <p className="mt-1 text-sm text-[#667085]">Use the OAuth flow for the real long-term connection. Manual token import is available for advanced recovery or testing.</p>
                        </div>
                        <StatusPill status={settingsPanels?.ebay?.connected ? 'success' : 'default'} label={settingsPanels?.ebay?.connected ? 'Connected' : 'Not connected'} />
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        <Button type="button" onClick={connectEbay} disabled={connectingEbay || !settingsPanels?.ebay?.oauth_ready}>
                          {connectingEbay ? 'Opening OAuth...' : 'Connect eBay'}
                        </Button>
                      </div>
                      {ebayConnectError ? <p className="mt-3 text-sm text-[#b42318]">{ebayConnectError}</p> : null}
                    </div>

                    <form
                      className="space-y-3 rounded-[18px] border border-[#e5e7eb] bg-white p-5"
                      onSubmit={async (event) => {
                        event.preventDefault();
                        setSavingMarketplace(true);
                        try {
                          await importEbayTokens(
                            {
                              access_token: ebayTokenForm.access_token.trim(),
                              refresh_token: ebayTokenForm.refresh_token.trim() || undefined,
                              expires_in_seconds: Number(ebayTokenForm.expires_in_seconds || 7200),
                              external_account_id: ebayTokenForm.external_account_id.trim() || undefined,
                            },
                            user?.id,
                          );
                          setEbayTokenForm({
                            access_token: '',
                            refresh_token: '',
                            expires_in_seconds: 7200,
                            external_account_id: '',
                          });
                          await reload();
                          toast.success('eBay token data saved for this operator.');
                        } catch (error) {
                          toast.error(error.message);
                        } finally {
                          setSavingMarketplace(false);
                        }
                      }}
                    >
                      <div>
                        <p className="text-sm font-semibold text-[#101828]">Advanced token import</p>
                        <p className="mt-1 text-sm text-[#667085]">Only use this if you intentionally generated a user token outside PosterPro. A refresh token is strongly recommended for anything beyond short-lived testing.</p>
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">Access token</label>
                        <textarea
                          value={ebayTokenForm.access_token}
                          onChange={(event) => setEbayTokenForm((current) => ({ ...current, access_token: event.target.value }))}
                          placeholder="Paste the eBay user access token if you need a manual import."
                          className="min-h-24 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">Refresh token</label>
                        <textarea
                          value={ebayTokenForm.refresh_token}
                          onChange={(event) => setEbayTokenForm((current) => ({ ...current, refresh_token: event.target.value }))}
                          placeholder="Paste the refresh token when available so PosterPro can keep the connection alive."
                          className="min-h-24 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                        />
                      </div>
                      <div className="grid gap-3 md:grid-cols-2">
                        <div className="space-y-2">
                          <label className="text-sm font-medium text-[#101828]">Token lifetime in seconds</label>
                          <Input
                            type="number"
                            value={ebayTokenForm.expires_in_seconds}
                            onChange={(event) => setEbayTokenForm((current) => ({ ...current, expires_in_seconds: event.target.value }))}
                          />
                        </div>
                        <div className="space-y-2">
                          <label className="text-sm font-medium text-[#101828]">External account label</label>
                          <Input
                            value={ebayTokenForm.external_account_id}
                            onChange={(event) => setEbayTokenForm((current) => ({ ...current, external_account_id: event.target.value }))}
                            placeholder="optional-seller-handle"
                          />
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button type="submit" variant="outline" disabled={savingMarketplace}>
                          {savingMarketplace ? 'Saving...' : 'Import tokens'}
                        </Button>
                      </div>
                    </form>
                  </div>
                </div>
              </div>
            </SectionPanel>
          ) : null}

          {activeTab === 'hosted-pages' ? (
            <SectionPanel title="CMS + Themes" description="Manage the public hosted CMS pages PosterPro serves for privacy policy, account handoff, and future branded customer-facing content.">
              <div className="space-y-6">
                <GuideCard
                  title="CMS onboarding flow"
                  description="Treat the hosted CMS like a lightweight SaaS trust center: choose a base theme, review the public URLs, then tailor the content for policy and login handoff pages."
                  prerequisites={['APP_BASE_URL points at the real public frontend host', 'Admin has reviewed the public brand name and desired public tone', 'Policy copy and OAuth handoff text have been reviewed for the real business']}
                  steps={[
                    'Choose the active CMS theme that should wrap every hosted page.',
                    'Import an additional theme pack when the default theme is not enough for the brand.',
                    'Confirm the generated privacy, accepted, and declined URLs before copying them back into eBay.',
                    'Edit the page slugs, titles, and HTML copy so the public experience reads like a polished SaaS trust surface.',
                  ]}
                  tone="slate"
                />
                <div className="grid gap-4 md:grid-cols-3">
                  <MetricCard label="Available themes" value={(settingsPanels?.hosted_pages?.themes || []).length} detail="Theme packs currently saved in the CMS workspace." />
                  <MetricCard label="Published pages" value={cmsPreviewPages.length} detail="Trust, onboarding, and OAuth handoff pages live from the CMS route set." />
                  <MetricCard label="Active theme" value={settingsPanels?.hosted_pages?.themes?.find((theme) => theme.id === hostedPagesForm.active_theme_id)?.name || 'Default'} detail="Theme currently applied to every public CMS page." />
                </div>
                <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(340px,0.9fr)]">
                  <form
                    className="space-y-4 rounded-[18px] border border-[#e5e7eb] bg-white p-5"
                    onSubmit={async (event) => {
                      event.preventDefault();
                      if (!canManageServer) {
                        toast.error('Admin access is required to change hosted pages.');
                        return;
                      }
                      setSavingServer(true);
                      try {
                        await updateHostedPages({
                          brand_name: hostedPagesForm.brand_name,
                          active_theme_id: hostedPagesForm.active_theme_id,
                          pages: hostedPagesForm.pages,
                        });
                        await reload();
                        toast.success('CMS draft saved.');
                      } catch (error) {
                        toast.error(error.message);
                      } finally {
                        setSavingServer(false);
                      }
                    }}
                  >
                    <FormSection
                      title="Brand + theme"
                      description="Set the public brand and choose the active theme template that wraps every hosted CMS page."
                    >
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">Brand name</label>
                        <Input
                          value={hostedPagesForm.brand_name}
                          onChange={(event) => setHostedPagesForm((current) => ({ ...current, brand_name: event.target.value }))}
                          placeholder="PosterPro"
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">Active CMS theme</label>
                        <select
                          value={hostedPagesForm.active_theme_id}
                          onChange={(event) => setHostedPagesForm((current) => ({ ...current, active_theme_id: event.target.value }))}
                          className="w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 py-2 text-sm text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                        >
                          {(settingsPanels?.hosted_pages?.themes || []).map((theme) => (
                            <option key={theme.id} value={theme.id}>
                              {theme.name}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="grid gap-3 md:grid-cols-2">
                        {(settingsPanels?.hosted_pages?.themes || []).map((theme) => (
                          <div
                            key={theme.id}
                            className={`rounded-[14px] border p-4 ${hostedPagesForm.active_theme_id === theme.id ? 'border-[#2563eb] bg-[#f6f9ff]' : 'border-[#e5e7eb] bg-[#fcfcfd]'}`}
                          >
                            <p className="text-sm font-semibold text-[#101828]">{theme.name}</p>
                            <p className="mt-1 text-sm text-[#667085]">{theme.description}</p>
                            <div className="mt-3 flex items-center gap-2">
                              <span className="h-4 w-4 rounded-full border border-white shadow-sm" style={{ background: theme.palette?.accent_color || '#2563eb' }} />
                              <span className="h-4 w-4 rounded-full border border-white shadow-sm" style={{ background: theme.palette?.hero_background || '#0f172a' }} />
                              <span className="h-4 w-4 rounded-full border border-white shadow-sm" style={{ background: theme.palette?.surface_background || '#ffffff' }} />
                            </div>
                          </div>
                        ))}
                      </div>
                    </FormSection>

                    <FormSection
                      title="Theme import"
                      description="Import a JSON theme pack so the hosted CMS can switch visual systems without custom code changes."
                    >
                      <div className="grid gap-3 md:grid-cols-2">
                        <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm text-[#101828]">
                          Replace existing themes
                          <input
                            type="checkbox"
                            checked={themeImportForm.replace_existing}
                            onChange={(event) => setThemeImportForm((current) => ({ ...current, replace_existing: event.target.checked }))}
                          />
                        </label>
                        <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm text-[#101828]">
                          Activate imported theme
                          <input
                            type="checkbox"
                            checked={themeImportForm.activate_imported}
                            onChange={(event) => setThemeImportForm((current) => ({ ...current, activate_imported: event.target.checked }))}
                          />
                        </label>
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">Theme pack JSON</label>
                        <textarea
                          value={themeImportForm.theme_pack_json}
                          onChange={(event) => setThemeImportForm((current) => ({ ...current, theme_pack_json: event.target.value }))}
                          className="min-h-56 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 font-mono text-xs text-[#101828] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                        />
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          variant="outline"
                          disabled={savingServer || !canManageServer}
                          onClick={async () => {
                            if (!canManageServer) {
                              toast.error('Admin access is required to import CMS themes.');
                              return;
                            }
                            setSavingServer(true);
                            try {
                              await importHostedPageTheme(themeImportForm);
                              await reload();
                              toast.success('Theme pack imported.');
                            } catch (error) {
                              toast.error(error.message);
                            } finally {
                              setSavingServer(false);
                            }
                          }}
                        >
                          Import theme pack
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          onClick={() => setThemeImportForm((current) => ({ ...current, theme_pack_json: DEFAULT_THEME_IMPORT_TEMPLATE }))}
                        >
                          Reset sample template
                        </Button>
                      </div>
                    </FormSection>

                    <FormSection
                      title="Structured page drafts"
                      description="Use the page list and standard template pack like a lightweight WordPress backend, then save or publish the hosted pages live."
                    >
                      <CmsTemplateWorkspace
                        pages={hostedPagesForm.pages}
                        activePageKey={activeCmsPreview}
                        onSelectPage={setActiveCmsPreview}
                        onUpdatePage={updateCmsPage}
                        onApplyTemplate={applyCmsTemplate}
                        onApplyTemplatePack={applyCmsTemplatePack}
                        canManageServer={canManageServer}
                        previewPage={activeCmsPreviewEntry?.page}
                        previewBrandName={activeCmsPreviewEntry?.page?.brand_name}
                        previewTitle={activeCmsPreviewEntry?.page?.title}
                        previewStatusTone={activeCmsPreviewEntry?.statusTone}
                        previewStatusMessage={activeCmsPreviewEntry?.statusMessage}
                        activeTheme={activeHostedTheme}
                        liveUrl={settingsPanels?.hosted_pages?.pages?.[activeCmsPreview]?.url}
                      />
                    </FormSection>
                    <div className="flex flex-wrap gap-2">
                      <Button type="submit" disabled={savingServer || !canManageServer}>
                        {savingServer ? 'Saving...' : 'Save draft'}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        disabled={savingServer || !canManageServer}
                        onClick={async () => {
                          if (!canManageServer) return;
                          setSavingServer(true);
                          try {
                            await publishHostedPages({ page_keys: CMS_PAGE_KEYS });
                            await reload();
                            toast.success('CMS draft published.');
                          } catch (error) {
                            toast.error(error.message);
                          } finally {
                            setSavingServer(false);
                          }
                        }}
                      >
                        Publish draft
                      </Button>
                    </div>
                  </form>

                  <div className="space-y-4">
                    <div className="rounded-[18px] border border-[#dbe7ff] bg-[#f7faff] p-5">
                      <p className="text-sm font-semibold text-[#101828]">CMS workflow</p>
                      <div className="mt-4 space-y-3 text-sm text-[#475467]">
                        <p><span className="font-semibold text-[#101828]">1.</span> Select the active theme to set the overall public visual system.</p>
                        <p><span className="font-semibold text-[#101828]">2.</span> Import a new theme pack when a customer or brand needs a different tone.</p>
                        <p><span className="font-semibold text-[#101828]">3.</span> Verify the generated URLs before copying them into eBay developer settings.</p>
                        <p><span className="font-semibold text-[#101828]">4.</span> Edit the copy so the onboarding and trust flow feels crisp and operator-friendly.</p>
                      </div>
                    </div>
                    <div className="rounded-[18px] border border-[#dbe7ff] bg-[#f7faff] p-5">
                      <p className="text-sm font-semibold text-[#101828]">Generated URLs</p>
                      <p className="mt-1 text-sm text-[#667085]">These are the live URLs for the broader CMS surface. The eBay-specific ones still need to be copied back into the RuName settings.</p>
                      <div className="mt-4 space-y-3">
                        {[
                          ['Privacy policy', settingsPanels?.hosted_pages?.pages?.privacy_policy?.url],
                          ['Trust center', settingsPanels?.hosted_pages?.pages?.trust_center?.url],
                          ['Operator onboarding', settingsPanels?.hosted_pages?.pages?.operator_onboarding?.url],
                          ['Accepted', settingsPanels?.hosted_pages?.pages?.ebay_auth_accepted?.url],
                          ['Declined', settingsPanels?.hosted_pages?.pages?.ebay_auth_declined?.url],
                        ].map(([label, value]) => (
                          <div key={label} className="rounded-[12px] border border-white/80 bg-white/90 p-3">
                            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#667085]">{label}</p>
                            <p className="mt-2 break-all text-sm text-[#101828]">{value || 'Set APP_BASE_URL first to generate the public URL.'}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className="rounded-[18px] border border-[#e5e7eb] bg-white p-5">
                      <p className="text-sm font-semibold text-[#101828]">Current theme details</p>
                      {(() => {
                        const activeTheme = (settingsPanels?.hosted_pages?.themes || []).find((theme) => theme.id === hostedPagesForm.active_theme_id);
                        if (!activeTheme) {
                          return <p className="mt-1 text-sm text-[#667085]">No active theme data loaded yet.</p>;
                        }
                        return (
                          <div className="mt-3 space-y-3">
                            <p className="text-sm text-[#667085]">{activeTheme.description}</p>
                            <div className="grid gap-3 sm:grid-cols-3">
                              {[
                                ['Accent', activeTheme.palette?.accent_color],
                                ['Hero', activeTheme.palette?.hero_background],
                                ['Surface', activeTheme.palette?.surface_background],
                              ].map(([label, value]) => (
                                <div key={label} className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                                  <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#667085]">{label}</p>
                                  <div className="mt-2 h-10 rounded-[10px] border border-white/80" style={{ background: value || '#ffffff' }} />
                                </div>
                              ))}
                            </div>
                          </div>
                        );
                      })()}
                    </div>
                    <div className="rounded-[18px] border border-[#e5e7eb] bg-white p-5">
                      <p className="text-sm font-semibold text-[#101828]">Operator note</p>
                      <p className="mt-1 text-sm text-[#667085]">The accepted page finalizes the eBay OAuth callback and refreshes the opener window. The declined page gives the operator a clean way back into PosterPro instead of leaving them on a generic eBay response. Trust center and onboarding pages extend the same theme system into broader marketing and credibility flows without needing a second site stack.</p>
                    </div>
                  </div>
                </div>
              </div>
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
                  const bridgeAccount = getBridgeAccountForMarketplace(marketplace);
                  const supportsBrowserConnect = BROWSER_CONNECT_MARKETPLACES.includes(marketplace.marketplace);
                  const supportsBrowserImport = BROWSER_IMPORT_MARKETPLACES.includes(marketplace.marketplace);
                  const browserSessionReady =
                    supportsBrowserConnect &&
                    bridgeAccount &&
                    ['ready', 'active', 'valid'].includes(String(bridgeAccount.session_state || '').toLowerCase());
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
                      {supportsBrowserConnect ? (
                        <div className="mt-3 rounded-[10px] border border-[#dbe7ff] bg-[#f7faff] px-3 py-3 text-sm text-[#475467]">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <p className="font-medium text-[#101828]">{`${MARKETPLACE_LABELS[marketplace.marketplace] || marketplace.marketplace} browser session`}</p>
                            <StatusPill status={browserSessionReady ? 'success' : 'warning'} label={bridgeAccount?.session_state || 'Not connected'} />
                          </div>
                          <p className="mt-2">
                            {`Use `}
                            <span className="font-medium text-[#101828]">{`Connect ${MARKETPLACE_LABELS[marketplace.marketplace] || marketplace.marketplace} account`}</span>
                            {` to capture the cookies and browser storage state required for browser-assist posting.`}
                          </p>
                          <div className="mt-3 flex flex-wrap gap-2">
                            <Button
                              size="sm"
                              onClick={() => {
                                void connectBrowserMarketplaceFromCard(marketplace);
                              }}
                              disabled={launchingBrowserWorkspace}
                            >
                              {launchingBrowserWorkspace ? 'Opening workspace...' : 'Connect now'}
                            </Button>
                            {supportsBrowserImport ? (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                  void importFacebookFromMarketplaceCard(marketplace);
                                }}
                                disabled={
                                  runningMarketplaceImport ||
                                  !String(marketplace.bridge_account_key || '').trim() ||
                                  (marketplace.import_mode || 'manual') !== 'browser_assist' ||
                                  !browserSessionReady
                                }
                              >
                                {runningMarketplaceImport ? 'Importing listings...' : 'Import listings'}
                              </Button>
                            ) : null}
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => openMarketplaceDrawer(marketplace)}
                            >
                              {`Open ${MARKETPLACE_LABELS[marketplace.marketplace] || marketplace.marketplace} setup`}
                            </Button>
                          </div>
                        </div>
                      ) : null}
                      {manualMode ? (
                        <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
                          <div className="rounded-[10px] border border-[#e5e7eb] bg-[#fcfcfd] px-3 py-2">
                            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#667085]">Import mode</p>
                            <p className="mt-1 text-sm text-[#101828]">{marketplace.import_mode || 'manual'}</p>
                          </div>
                          <div className="rounded-[10px] border border-[#e5e7eb] bg-[#fcfcfd] px-3 py-2">
                            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#667085]">Publish mode</p>
                            <p className="mt-1 text-sm text-[#101828]">{marketplace.publish_mode || 'manual_review'}</p>
                          </div>
                          <div className="rounded-[10px] border border-[#e5e7eb] bg-[#fcfcfd] px-3 py-2">
                            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#667085]">Shipping scope</p>
                            <p className="mt-1 text-sm text-[#101828]">{marketplace.shipping_scope || 'local_only'}</p>
                          </div>
                          <div className="rounded-[10px] border border-[#e5e7eb] bg-[#fcfcfd] px-3 py-2">
                            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#667085]">Renewal mode</p>
                            <p className="mt-1 text-sm text-[#101828]">{marketplace.renewal_mode || 'manual'}</p>
                          </div>
                        </div>
                      ) : null}
                      <div className="mt-4 flex flex-wrap gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            if (marketplace.marketplace === 'ebay') {
                              selectTab('ebay');
                              return;
                            }
                            openMarketplaceDrawer(marketplace);
                          }}
                        >
                          {marketplace.marketplace === 'ebay'
                            ? 'Open eBay setup'
                            : supportsBrowserConnect
                            ? `Open ${MARKETPLACE_LABELS[marketplace.marketplace] || marketplace.marketplace} setup`
                            : manualMode
                            ? 'Configure account'
                            : 'Review setup'}
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
                      {marketplace.support_url ? (
                        <p className="mt-3 text-sm text-[#475467]">
                          Runbook: <a href={marketplace.support_url} target="_blank" rel="noreferrer" className="font-medium text-[#2563eb]">{marketplace.support_url}</a>
                        </p>
                      ) : null}
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
                <div className="rounded-[14px] border border-[#dbe7ff] bg-[#f7faff] p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-[#101828]">Automation bridge</p>
                      <p className="mt-1 text-sm text-[#667085]">
                        This is the transport layer for unsupported marketplaces like Facebook when PosterPro needs a browser-assist or provider-assist runner.
                      </p>
                    </div>
                    <StatusPill
                      status={settingsPanels?.automation?.automation_bridge_configured ? 'success' : 'warning'}
                      label={settingsPanels?.automation?.automation_bridge_configured ? 'Bridge ready' : 'Bridge not configured'}
                    />
                  </div>
                  <div className="mt-4 grid gap-4 md:grid-cols-2">
                    <label className="flex items-center justify-between rounded-[10px] border border-white/80 bg-white px-4 py-3 text-sm text-[#101828]">
                      Bridge enabled
                      <input
                        type="checkbox"
                        checked={automationForm.automation_bridge_enabled}
                        onChange={(event) => setAutomationForm((current) => ({ ...current, automation_bridge_enabled: event.target.checked }))}
                      />
                    </label>
                    <div className="space-y-2 rounded-[10px] border border-white/80 bg-white p-3">
                      <label className="text-sm font-medium text-[#101828]">Bridge timeout (seconds)</label>
                      <Input
                        type="number"
                        min="5"
                        value={automationForm.automation_bridge_timeout_seconds}
                        onChange={(event) => setAutomationForm((current) => ({ ...current, automation_bridge_timeout_seconds: Number(event.target.value || 5) }))}
                      />
                    </div>
                  </div>
                  <div className="mt-4 grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-[#101828]">Bridge base URL</label>
                      <Input
                        value={automationForm.automation_bridge_url}
                        onChange={(event) => setAutomationForm((current) => ({ ...current, automation_bridge_url: event.target.value }))}
                        placeholder="https://automation-bridge.yourdomain.com"
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-[#101828]">Bridge API key</label>
                      <Input
                        value={automationForm.automation_bridge_api_key}
                        onChange={(event) => setAutomationForm((current) => ({ ...current, automation_bridge_api_key: event.target.value }))}
                        placeholder={settingsPanels?.automation?.automation_bridge_configured ? 'Configured on server' : 'Paste bridge API key'}
                      />
                    </div>
                  </div>
                  <div className="mt-4 rounded-[10px] border border-white/80 bg-white p-3 text-sm text-[#475467]">
                    PosterPro will submit `crosspost` and `import` jobs to:
                    <span className="ml-1 font-mono text-xs">POST /jobs/crosspost</span>
                    <span className="mx-1">and</span>
                    <span className="font-mono text-xs">POST /jobs/import</span>
                    on the configured bridge using bearer-token auth.
                  </div>
                  <div className="mt-4 flex flex-wrap items-center gap-3">
                    <Button
                      type="button"
                      variant="outline"
                      disabled={!canManageServer || testingBridge}
                      onClick={async () => {
                        if (!canManageServer) {
                          toast.error('Admin access is required to test the automation bridge.');
                          return;
                        }
                        setTestingBridge(true);
                        try {
                          const result = await runAutomationBridgeSmokeTest();
                          setBridgeSmokeResult(result);
                          toast.success(result.ok ? 'Automation bridge reachable.' : 'Automation bridge smoke test failed.');
                        } catch (error) {
                          toast.error(error.message);
                        } finally {
                          setTestingBridge(false);
                        }
                      }}
                    >
                      {testingBridge ? 'Testing bridge...' : 'Run bridge smoke test'}
                    </Button>
                    {bridgeSmokeResult ? (
                      <StatusPill
                        status={bridgeSmokeResult.ok ? 'success' : 'warning'}
                        label={bridgeSmokeResult.ok ? 'Bridge reachable' : bridgeSmokeResult.status || 'Bridge check failed'}
                      />
                    ) : null}
                  </div>
                  {bridgeSmokeResult ? (
                    <div className={`mt-4 rounded-[10px] border p-3 text-sm ${bridgeSmokeResult.ok ? 'border-[#d1fadf] bg-[#ecfdf3] text-[#067647]' : 'border-[#fecdca] bg-[#fff6f3] text-[#912018]'}`}>
                      <p className="font-medium">{bridgeSmokeResult.message || (bridgeSmokeResult.ok ? 'The bridge responded successfully to the connectivity probe.' : 'The bridge did not respond successfully.')}</p>
                      {bridgeSmokeResult.checked_url ? <p className="mt-1 font-mono text-xs">{bridgeSmokeResult.checked_url}</p> : null}
                      {bridgeSmokeResult.errors?.length ? (
                        <div className="mt-2 space-y-1">
                          {bridgeSmokeResult.errors.map((item) => (
                            <p key={item} className="font-mono text-xs">{item}</p>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
                <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-[#101828]">Bridge marketplace accounts</p>
                      <p className="mt-1 text-sm text-[#667085]">
                        Store bridge-side login profiles and session state for browser-assist and provider-assist marketplaces like Facebook Marketplace.
                      </p>
                    </div>
                    <StatusPill status={bridgeAccounts.length ? 'success' : 'warning'} label={bridgeAccounts.length ? `${bridgeAccounts.length} saved` : 'No bridge accounts'} />
                  </div>
                  <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
                    <form
                      className="space-y-4"
                      onSubmit={async (event) => {
                        event.preventDefault();
                        if (!canManageServer) {
                          toast.error('Admin access is required to manage bridge accounts.');
                          return;
                        }
                        if (!bridgeAccountForm.account_key.trim()) {
                          toast.error('Account key is required.');
                          return;
                        }
                        setSavingBridgeAccount(true);
                        try {
                          await upsertBridgeAccount(bridgeAccountForm.marketplace, bridgeAccountForm.account_key, {
                            display_name: bridgeAccountForm.display_name,
                            login_handle: bridgeAccountForm.login_handle,
                            credential_secret: bridgeAccountForm.credential_secret || undefined,
                            notes: bridgeAccountForm.notes,
                            provider_enabled: bridgeAccountForm.provider_enabled,
                            browser_enabled: bridgeAccountForm.browser_enabled,
                            session_state: bridgeAccountForm.session_state,
                            session_payload: bridgeAccountForm.session_payload_text.trim() ? JSON.parse(bridgeAccountForm.session_payload_text) : {},
                            expires_at: bridgeAccountForm.expires_at || null,
                          });
                          await updateBridgeAccountSession(bridgeAccountForm.marketplace, bridgeAccountForm.account_key, {
                            session_state: bridgeAccountForm.session_state,
                            session_payload: bridgeAccountForm.session_payload_text.trim() ? JSON.parse(bridgeAccountForm.session_payload_text) : {},
                            expires_at: bridgeAccountForm.expires_at || null,
                            last_tested_at: new Date().toISOString(),
                            notes: bridgeAccountForm.notes,
                          });
                          await reload();
                          toast.success('Bridge account saved.');
                          setBridgeAccountForm((current) => ({ ...current, credential_secret: '', session_payload_text: '' }));
                        } catch (error) {
                          toast.error(error.message);
                        } finally {
                          setSavingBridgeAccount(false);
                        }
                      }}
                    >
                      <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                          <label className="text-sm font-medium text-[#101828]">Marketplace</label>
                          <select
                            value={bridgeAccountForm.marketplace}
                            onChange={(event) => setBridgeAccountForm((current) => ({ ...current, marketplace: event.target.value }))}
                            className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]"
                          >
                            {BRIDGE_MARKETPLACE_OPTIONS.map((name) => (
                              <option key={name} value={name}>{MARKETPLACE_LABELS[name] || name}</option>
                            ))}
                          </select>
                        </div>
                        <div className="space-y-2">
                          <label className="text-sm font-medium text-[#101828]">Account key</label>
                          <Input value={bridgeAccountForm.account_key} onChange={(event) => setBridgeAccountForm((current) => ({ ...current, account_key: event.target.value }))} placeholder="facebook-main" />
                        </div>
                        <div className="space-y-2">
                          <label className="text-sm font-medium text-[#101828]">Display name</label>
                          <Input value={bridgeAccountForm.display_name} onChange={(event) => setBridgeAccountForm((current) => ({ ...current, display_name: event.target.value }))} placeholder="Main FB seller profile" />
                        </div>
                        <div className="space-y-2">
                          <label className="text-sm font-medium text-[#101828]">Login handle</label>
                          <Input value={bridgeAccountForm.login_handle} onChange={(event) => setBridgeAccountForm((current) => ({ ...current, login_handle: event.target.value }))} placeholder="seller@example.com" />
                        </div>
                      </div>
                      <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                          <label className="text-sm font-medium text-[#101828]">Credential secret</label>
                          <Input type="password" value={bridgeAccountForm.credential_secret} onChange={(event) => setBridgeAccountForm((current) => ({ ...current, credential_secret: event.target.value }))} placeholder="Paste bridge-side login secret" />
                        </div>
                        <div className="space-y-2">
                          <label className="text-sm font-medium text-[#101828]">Session state</label>
                          <select
                            value={bridgeAccountForm.session_state}
                            onChange={(event) => setBridgeAccountForm((current) => ({ ...current, session_state: event.target.value }))}
                            className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]"
                          >
                            <option value="draft">Draft</option>
                            <option value="ready">Ready</option>
                            <option value="active">Active</option>
                            <option value="expired">Expired</option>
                            <option value="invalid">Invalid</option>
                          </select>
                        </div>
                      </div>
                      <div className="grid gap-4 md:grid-cols-2">
                        <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-[#f9fafb] px-4 py-3 text-sm text-[#101828]">
                          Provider assist enabled
                          <input type="checkbox" checked={bridgeAccountForm.provider_enabled} onChange={(event) => setBridgeAccountForm((current) => ({ ...current, provider_enabled: event.target.checked }))} />
                        </label>
                        <label className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-[#f9fafb] px-4 py-3 text-sm text-[#101828]">
                          Browser assist enabled
                          <input type="checkbox" checked={bridgeAccountForm.browser_enabled} onChange={(event) => setBridgeAccountForm((current) => ({ ...current, browser_enabled: event.target.checked }))} />
                        </label>
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">Session payload JSON</label>
                        <textarea
                          value={bridgeAccountForm.session_payload_text}
                          onChange={(event) => setBridgeAccountForm((current) => ({ ...current, session_payload_text: event.target.value }))}
                          placeholder='{"cookies":[{"name":"c_user","value":"..."}]}'
                          className="mt-1 h-28 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 py-2 text-sm text-[#101828]"
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium text-[#101828]">Notes</label>
                        <textarea
                          value={bridgeAccountForm.notes}
                          onChange={(event) => setBridgeAccountForm((current) => ({ ...current, notes: event.target.value }))}
                          placeholder="Browser profile notes, MFA expectations, proxy requirements, etc."
                          className="mt-1 h-20 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 py-2 text-sm text-[#101828]"
                        />
                      </div>
                      <div className="flex justify-end">
                        <Button type="submit" disabled={savingBridgeAccount}>
                          {savingBridgeAccount ? 'Saving bridge account...' : 'Save bridge account'}
                        </Button>
                      </div>
                    </form>
                    <div className="space-y-3">
                      {bridgeAccounts.length ? bridgeAccounts.map((account) => (
                        <button
                          key={account.account_id}
                          type="button"
                          onClick={() =>
                            setBridgeAccountForm({
                              marketplace: account.marketplace,
                              account_key: account.account_key,
                              display_name: account.display_name || '',
                              login_handle: account.login_handle || '',
                              credential_secret: '',
                              notes: account.notes || '',
                              provider_enabled: !!account.provider_enabled,
                              browser_enabled: !!account.browser_enabled,
                              session_state: account.session_state || 'draft',
                              session_payload_text: JSON.stringify(account.session_payload || {}, null, 2),
                              expires_at: account.expires_at || '',
                            })
                          }
                          className="w-full rounded-[12px] border border-[#e5e7eb] bg-[#f9fafb] p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]"
                        >
                          <div className="flex items-center justify-between gap-3">
                            <p className="text-sm font-semibold text-[#101828]">{account.display_name || account.account_key}</p>
                            <StatusPill status={['ready', 'active', 'valid'].includes(String(account.session_state).toLowerCase()) ? 'success' : 'warning'} label={account.session_state || 'draft'} />
                          </div>
                          <p className="mt-1 text-sm text-[#667085]">{MARKETPLACE_LABELS[account.marketplace] || account.marketplace}{account.login_handle ? ` · ${account.login_handle}` : ''}</p>
                          <div className="mt-2 flex flex-wrap gap-2">
                            {account.provider_enabled ? <span className="pp-chip">Provider</span> : null}
                            {account.browser_enabled ? <span className="pp-chip">Browser</span> : null}
                            {account.credential_configured ? <span className="pp-chip">Credential saved</span> : <span className="pp-chip">No credential</span>}
                          </div>
                        </button>
                      )) : (
                        <EmptyState title="No bridge accounts yet" description="Add a Facebook or secondary-marketplace bridge account so provider/browser-assisted jobs have a real runner-side identity." className="border-0 p-0 py-8" />
                      )}
                    </div>
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

      <Drawer
        open={activeTab === 'marketplaces' && !!configuredMarketplace && configuredMarketplace.connection_mode === 'manual'}
        onClose={() => setSelectedMarketplace('')}
        title={
          configuredMarketplace
            ? BROWSER_CONNECT_MARKETPLACES.includes(configuredMarketplace.marketplace)
              ? `Connect ${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} account`
              : `${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} account setup`
            : 'Marketplace setup'
        }
        description={
          BROWSER_CONNECT_MARKETPLACES.includes(String(configuredMarketplace?.marketplace || ''))
            ? 'Capture the browser session first, then save the operator account details and workflow settings.'
            : 'Save the operator-facing account details for this channel, then mark it ready when this user can work it from PosterPro.'
        }
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
            {BROWSER_CONNECT_MARKETPLACES.includes(configuredMarketplace.marketplace) ? (
              <div className="space-y-4 rounded-[14px] border border-[#dbe7ff] bg-[#f7faff] p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-[#101828]">{`${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} browser connection`}</p>
                    <p className="mt-1 text-sm text-[#667085]">
                      Use the bridge browser to capture the cookies and storage state PosterPro needs for browser-assist posting.
                    </p>
                  </div>
                  <StatusPill
                    status={selectedBridgeAccount && ['ready', 'active', 'valid'].includes(String(selectedBridgeAccount.session_state || '').toLowerCase()) ? 'success' : 'warning'}
                    label={selectedBridgeAccount ? selectedBridgeAccount.session_state || 'draft' : 'No session'}
                  />
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Bridge account key</label>
                    <Input
                      value={marketplaceForm.bridge_account_key}
                      onChange={(event) => setMarketplaceForm((current) => ({ ...current, bridge_account_key: event.target.value.toLowerCase() }))}
                      placeholder={`${configuredMarketplace.marketplace}-main`}
                    />
                  </div>
                  {BROWSER_IMPORT_MARKETPLACES.includes(configuredMarketplace.marketplace) ? (
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-[#101828]">Listings to import</label>
                      <Input
                        type="number"
                        min="1"
                        max="25"
                        value={marketplaceForm.import_listing_limit}
                        onChange={(event) => setMarketplaceForm((current) => ({ ...current, import_listing_limit: Number(event.target.value || 1) }))}
                      />
                    </div>
                  ) : null}
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">Bridge session state</label>
                  <select
                    value={marketplaceForm.bridge_session_state}
                    onChange={(event) => setMarketplaceForm((current) => ({ ...current, bridge_session_state: event.target.value }))}
                    className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  >
                    <option value="draft">Draft</option>
                    <option value="ready">Ready</option>
                    <option value="active">Active</option>
                    <option value="expired">Expired</option>
                    <option value="invalid">Invalid</option>
                  </select>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">{`${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} storage-state JSON`}</label>
                  <textarea
                    value={marketplaceForm.bridge_session_payload_text}
                    onChange={(event) => setMarketplaceForm((current) => ({ ...current, bridge_session_payload_text: event.target.value }))}
                    placeholder='{"cookies":[{"name":"session","value":"..."}],"origins":[]}'
                    className="min-h-40 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 font-mono text-xs text-[#101828] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  />
                </div>
                <div className="rounded-[12px] border border-white/80 bg-white p-3 text-sm text-[#475467]">
                  {`PosterPro cannot rely on a native ${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} API here. The bridge must capture a real authenticated browser session. Use the connect action when the bridge is running on a machine where you can complete the login flow, or paste a Playwright storage-state export as a fallback.`}
                </div>
                <div className="rounded-[12px] border border-[#dbe7ff] bg-[#eff6ff] p-3 text-sm text-[#1d4ed8]">
                  {`Click `}
                  <span className="font-semibold">{`Connect ${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} account`}</span>
                  {` to open the browser-based connect workspace, then complete the login and any MFA inside the embedded bridge desktop.`}
                </div>
                {browserConnectInProgress ? (
                  <div className="rounded-[12px] border border-[#fde68a] bg-[#fffbeb] p-3 text-sm text-[#92400e]">
                    <p className="font-semibold text-[#101828]">{`${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} connection in progress`}</p>
                    <p className="mt-1">{activeBridgeConnectSession.message || 'PosterPro is waiting for login in the bridge workspace.'}</p>
                    <p className="mt-1">
                      Status: <span className="font-medium">{String(activeBridgeConnectSession.status || 'waiting_for_login').replace(/_/g, ' ')}</span>
                    </p>
                    <div className="mt-3">
                      <Link href={`/bridge-desktop?marketplace=${encodeURIComponent(configuredMarketplace.marketplace)}&connectSessionId=${encodeURIComponent(activeBridgeConnectSession.connect_session_id)}`}>
                        <Button type="button" variant="outline">{`Resume ${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} login`}</Button>
                      </Link>
                    </div>
                  </div>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    onClick={connectBrowserMarketplaceAccount}
                    disabled={launchingBrowserWorkspace || !String(marketplaceForm.bridge_account_key || '').trim()}
                  >
                    {launchingBrowserWorkspace ? 'Opening workspace...' : `Connect ${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} account`}
                  </Button>
                  <Button type="button" variant="outline" onClick={saveBrowserSession} disabled={savingBrowserSession}>
                    {savingBrowserSession ? 'Saving session...' : `Save ${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} session`}
                  </Button>
                  {BROWSER_IMPORT_MARKETPLACES.includes(configuredMarketplace.marketplace) ? (
                    <Button
                      type="button"
                      onClick={importExistingFacebookListings}
                      disabled={
                        runningMarketplaceImport ||
                        !String(marketplaceForm.bridge_account_key || '').trim() ||
                        (marketplaceForm.import_mode || 'manual') !== 'browser_assist' ||
                        !isBridgeSessionReady(selectedBridgeAccount)
                      }
                    >
                      {runningMarketplaceImport ? 'Importing listings...' : `Import existing ${MARKETPLACE_LABELS[configuredMarketplace.marketplace] || configuredMarketplace.marketplace} listings`}
                    </Button>
                  ) : null}
                </div>
              </div>
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
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Import mode</label>
                <select
                  value={marketplaceForm.import_mode}
                  onChange={(event) => setMarketplaceForm((current) => ({ ...current, import_mode: event.target.value }))}
                  className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                >
                  <option value="manual">Manual import</option>
                  <option value="csv_assist">CSV assist</option>
                  <option value="provider_assist">Provider assist</option>
                  <option value="browser_assist">Browser assist</option>
                </select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Publish mode</label>
                <select
                  value={marketplaceForm.publish_mode}
                  onChange={(event) => setMarketplaceForm((current) => ({ ...current, publish_mode: event.target.value }))}
                  className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                >
                  <option value="manual_review">Manual review</option>
                  <option value="draft_only">Draft only</option>
                  <option value="provider_assist">Provider assist</option>
                  <option value="browser_assist">Browser assist</option>
                </select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Shipping scope</label>
                <select
                  value={marketplaceForm.shipping_scope}
                  onChange={(event) => setMarketplaceForm((current) => ({ ...current, shipping_scope: event.target.value }))}
                  className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                >
                  <option value="local_only">Local only</option>
                  <option value="shipping_only">Shipping only</option>
                  <option value="local_and_shipping">Local and shipping</option>
                </select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Renewal mode</label>
                <select
                  value={marketplaceForm.renewal_mode}
                  onChange={(event) => setMarketplaceForm((current) => ({ ...current, renewal_mode: event.target.value }))}
                  className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                >
                  <option value="manual">Manual</option>
                  <option value="daily">Daily plan</option>
                  <option value="scheduled">Scheduled plan</option>
                </select>
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-[#101828]">Support URL or runbook link</label>
              <Input
                value={marketplaceForm.support_url}
                onChange={(event) => setMarketplaceForm((current) => ({ ...current, support_url: event.target.value }))}
                placeholder="https://yourdomain.com/facebook-marketplace-runbook"
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
                    import_mode: BROWSER_IMPORT_MARKETPLACES.includes(configuredMarketplace.marketplace) ? 'browser_assist' : 'manual',
                    publish_mode: BROWSER_CONNECT_MARKETPLACES.includes(configuredMarketplace.marketplace) ? 'browser_assist' : 'manual_review',
                    shipping_scope: 'local_only',
                    renewal_mode: 'manual',
                    support_url: '',
                    bridge_account_key: '',
                    import_listing_limit: 10,
                    bridge_session_state: 'draft',
                    bridge_session_payload_text: '',
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
