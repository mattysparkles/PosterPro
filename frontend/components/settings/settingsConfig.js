export const SETTINGS_TABS = [
  { value: 'overview', label: 'Overview' },
  { value: 'profile', label: 'Profile' },
  { value: 'workflow', label: 'Workflow' },
  { value: 'appearance', label: 'Appearance' },
  { value: 'marketplaces', label: 'Marketplaces' },
  { value: 'ebay', label: 'eBay OAuth' },
  { value: 'amazon', label: 'Amazon / Vine' },
  { value: 'api-keys', label: 'API Keys' },
  { value: 'automation', label: 'Automation' },
  { value: 'hosted-pages', label: 'Hosted Pages / Themes' },
  { value: 'email', label: 'Email' },
  { value: 'server', label: 'Server / Readiness' },
];

export const SETTINGS_GROUPS = [
  { label: 'Account', tabs: ['overview', 'profile', 'workflow', 'appearance'] },
  { label: 'Channels', tabs: ['ebay', 'amazon', 'marketplaces'] },
  { label: 'Admin', tabs: ['automation', 'api-keys', 'hosted-pages', 'email', 'server'] },
];

export const MARKETPLACE_LABELS = {
  ebay: 'eBay',
  etsy: 'Etsy',
  facebook: 'Facebook Marketplace',
  mercari: 'Mercari',
  poshmark: 'Poshmark',
  depop: 'Depop',
  whatnot: 'Whatnot',
  vinted: 'Vinted',
};

export const BRIDGE_MARKETPLACE_OPTIONS = ['facebook', 'etsy', 'mercari', 'poshmark', 'depop', 'whatnot', 'vinted'];
export const BROWSER_CONNECT_MARKETPLACES = ['facebook', 'mercari', 'poshmark', 'etsy', 'depop', 'whatnot', 'vinted'];
export const BROWSER_IMPORT_MARKETPLACES = ['facebook'];

export const RESELLER_PRIORITY_MARKETPLACES = ['mercari', 'poshmark', 'whatnot'];
export const MARKETPLACE_CARD_PRIORITY = ['ebay', 'facebook', ...RESELLER_PRIORITY_MARKETPLACES, 'etsy', 'depop', 'vinted'];

export const MARKETPLACE_GUIDES = {
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
    summary: 'Mercari uses an assisted workflow. PosterPro tracks the account identity, bridge session, and readiness for cross-post drafting and handoff.',
    tooltip: 'Mercari is not a native OAuth/direct-API connector here. Use the bridge desktop to capture a real authenticated browser session.',
    prerequisites: ['Bridge account key (mercari-main)', 'Store or closet name + handle', 'Operator posting notes (shipping, pricing, required fields)'],
    steps: [
      'Save a bridge account key and store the operator identity details.',
      'Use Connect Mercari account to capture a valid browser session in Bridge Desktop.',
      'Mark the channel Ready only after the session is valid and the workflow is confirmed.',
      'Use listing previews + cross-post jobs to generate a structured handoff plan for Mercari.',
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
    summary: 'Poshmark uses an assisted workflow. PosterPro tracks the closet identity, bridge session health, and readiness for drafting/handoff.',
    tooltip: 'Poshmark is supported through the automation bridge/browser workflow rather than a direct API integration in this deployment.',
    prerequisites: ['Bridge account key (poshmark-main)', 'Closet name + @username', 'Operator notes (sharing, bundles, offers, shipping defaults)'],
    steps: [
      'Save the bridge account key and operator identity fields.',
      'Connect Poshmark in Bridge Desktop and confirm the session state is Ready/Valid.',
      'Mark Ready after you’ve verified the listing workflow and required fields.',
      'Use listing previews to review how the listing will map into Poshmark draft fields before handoff.',
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
    summary: 'Whatnot is modeled as a live-sale channel. PosterPro tracks the operator identity, bridge session, and readiness for drafting and follow-up.',
    tooltip: 'Whatnot posting is not a direct API publish path here. Use assisted workflows and operator policy for final submission.',
    prerequisites: ['Bridge account key (whatnot-main)', 'Seller handle', 'Operator notes (show schedule, shipping, category rules)'],
    steps: [
      'Save the bridge account key and seller identity details.',
      'Connect Whatnot in Bridge Desktop and confirm the session state is Ready/Valid.',
      'Mark Ready once the operator workflow for live-sale preparation is confirmed.',
      'Use listing previews to prepare consistent titles, prices, and item specifics before handoff.',
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

export const SERVICE_GUIDES = {
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

export const WORKFLOW_PREVIEW_OPTIONS = [
  { value: 'marketplace', label: 'Marketplace preview' },
  { value: 'editor', label: 'Editor first' },
];

export const CREDENTIAL_INSTRUCTIONS = {
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

export const DEFAULT_THEME_IMPORT_TEMPLATE = JSON.stringify(
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

export function formatDateTimeValue(value) {
  if (!value) return 'Not reported';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value));
}

export function bridgeSessionTone(sessionState) {
  return ['ready', 'active', 'valid'].includes(String(sessionState || '').toLowerCase()) ? 'success' : 'warning';
}

export function bridgeNextStep({ bridgeAccount, browserConnectInProgress, supportsBrowserImport }) {
  if (browserConnectInProgress) {
    return 'Resume the live bridge workspace and complete the login or MFA step there before returning to Settings.';
  }
  if (!bridgeAccount) {
    return 'Save a bridge account key first so PosterPro has a runner-side identity for this marketplace.';
  }
  if (!bridgeAccount.credential_configured && !bridgeAccount.session_payload) {
    return 'Add bridge credentials or capture a browser session so assisted jobs have something usable to run against.';
  }
  if (!['ready', 'active', 'valid'].includes(String(bridgeAccount.session_state || '').toLowerCase())) {
    return 'Reconnect this marketplace in the bridge workspace or save a fresh storage-state payload before relying on assisted jobs.';
  }
  if (supportsBrowserImport) {
    return 'This bridge session looks usable. You can run assisted posting now, and Facebook import should also be available.';
  }
  return 'This bridge session looks usable. Assisted posting should have the browser context it needs.';
}

export function supportTone(level) {
  if (level === 'direct_api') return 'success';
  if (level === 'browser_assist' || level === 'provider_assist' || level === 'csv_assist') return 'info';
  return 'default';
}
