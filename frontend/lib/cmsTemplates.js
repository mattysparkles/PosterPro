const CMS_PAGE_DEFINITIONS = {
  privacy_policy: {
    key: 'privacy_policy',
    label: 'Privacy policy',
    description: 'Public compliance page for privacy, marketplace data handling, and operator disclosures.',
    routeGroup: 'legal',
    slug: 'privacy-policy',
    statusTone: undefined,
    statusMessage: '',
    templateName: 'Policy Standard',
    snapshot: {
      title: 'PosterPro Privacy Policy',
      summary: 'A clear policy page covering marketplace data use, operator account handling, and support expectations.',
      hero: {
        eyebrow: 'Policy + compliance',
        title: 'Privacy policy for marketplace operations',
        body: 'Explain how the deployment handles operator accounts, marketplace data, listing content, and support workflows.',
      },
      primary_button: {
        label: 'Open PosterPro',
        href: '/',
      },
      secondary_button: {
        label: 'Read trust center',
        href: '/site/trust-center',
      },
      blocks: [
        {
          type: 'rich_text',
          html:
            '<p>PosterPro stores the data required to help operators prepare, review, publish, and maintain marketplace listings. That can include listing text, images, workflow activity, marketplace account metadata, and authenticated tokens where a connector requires them.</p><p>The self-hosting operator is responsible for tailoring this policy so it matches the deployment, the connected marketplaces, and the team workflows that actually run in production.</p><p>Use this page as the public source of truth for what data is collected, why it is processed, and who controls that data in your environment.</p>',
        },
        {
          type: 'feature_list',
          items: [
            { title: 'Data used for listings', body: 'Document the listing content, media, pricing, and inventory metadata your team stores in PosterPro.' },
            { title: 'Connected account data', body: 'Explain how OAuth tokens, session payloads, and marketplace account identifiers are retained and refreshed.' },
            { title: 'Operator responsibility', body: 'State clearly that the self-hosting team owns final policy language, retention rules, and customer-facing notices.' },
          ],
        },
      ],
    },
  },
  trust_center: {
    key: 'trust_center',
    label: 'Trust center',
    description: 'Public trust page for platform posture, support promises, and marketplace workflow credibility.',
    routeGroup: 'site',
    slug: 'trust-center',
    statusTone: undefined,
    statusMessage: '',
    templateName: 'Trust Center Standard',
    snapshot: {
      title: 'PosterPro Trust Center',
      summary: 'A public overview of account-connect posture, workflow controls, and operational readiness.',
      hero: {
        eyebrow: 'Security + operations',
        title: 'A clean trust center for marketplace teams',
        body: 'Show how operators connect accounts, how listings move through review, and what support standards the deployment follows.',
      },
      primary_button: {
        label: 'View onboarding',
        href: '/site/operator-onboarding',
      },
      secondary_button: {
        label: 'Open PosterPro',
        href: '/',
      },
      blocks: [
        {
          type: 'feature_list',
          items: [
            { title: 'Centralized workflow', body: 'Listings, approval queues, marketplace connections, and automation settings are managed from one operator workspace.' },
            { title: 'Visible account handoffs', body: 'OAuth and browser-assisted connection flows are exposed through explicit status pages instead of generic redirects.' },
            { title: 'Admin-owned controls', body: 'Themes, hosted pages, and channel rules can be managed inside the same backend without a second CMS stack.' },
          ],
        },
        {
          type: 'rich_text',
          html:
            '<p>Use this page to describe security posture, escalation paths, support hours, workflow review checkpoints, and what a marketplace operator should expect during setup.</p><p>For self-hosted installations, this page is also the right place to explain who administers the deployment and how account access is provisioned or removed.</p>',
        },
        {
          type: 'cta',
          title: 'Need a guided setup path?',
          body: 'Pair the trust center with a simple onboarding page so account owners can move from confidence to action without leaving the hosted CMS flow.',
          button: {
            label: 'Open onboarding',
            href: '/site/operator-onboarding',
          },
        },
      ],
    },
  },
  operator_onboarding: {
    key: 'operator_onboarding',
    label: 'Operator onboarding',
    description: 'Public getting-started page for a new operator, customer, or partner before account setup.',
    routeGroup: 'site',
    slug: 'operator-onboarding',
    statusTone: undefined,
    statusMessage: '',
    templateName: 'Onboarding Standard',
    snapshot: {
      title: 'Get Started With PosterPro',
      summary: 'A guided onboarding page that explains the account setup flow before an operator signs in.',
      hero: {
        eyebrow: 'Operator onboarding',
        title: 'Start with a predictable setup workflow',
        body: 'Walk a new operator through sign-in, marketplace connection, and the first review steps before they enter the live workspace.',
      },
      primary_button: {
        label: 'Open PosterPro',
        href: '/login',
      },
      secondary_button: {
        label: 'Read trust center',
        href: '/site/trust-center',
      },
      blocks: [
        {
          type: 'steps',
          items: [
            'Sign in with the operator account or invite issued by your team.',
            'Connect the required marketplace accounts or complete any browser-assisted login steps.',
            'Review the draft workflow, pricing posture, and publishing approvals before going live.',
          ],
        },
        {
          type: 'feature_list',
          items: [
            { title: 'Preflight before publish', body: 'Explain what needs to be configured before drafts can move into queueing or live publication.' },
            { title: 'Clear role boundaries', body: 'Tell operators which actions are automated, which remain manual, and where human approval is still required.' },
            { title: 'Reusable onboarding shell', body: 'This page can be tailored for customers, internal operators, or partner teams without spinning up a separate site.' },
          ],
        },
      ],
    },
  },
  ebay_auth_accepted: {
    key: 'ebay_auth_accepted',
    label: 'eBay auth accepted',
    description: 'Success page shown after eBay approval while PosterPro finalizes the account connection.',
    routeGroup: 'connect',
    slug: 'ebay-auth-complete',
    statusTone: 'success',
    statusMessage: 'The eBay account is now connected to PosterPro. This window can be closed.',
    templateName: 'OAuth Success Standard',
    snapshot: {
      title: 'eBay Connection Complete',
      summary: 'Success page shown when eBay returns the operator to PosterPro after approval.',
      hero: {
        eyebrow: 'Account connection',
        title: 'Authorization approved',
        body: 'PosterPro is finishing the eBay account connection and syncing the operator workspace.',
      },
      primary_button: {
        label: 'Return to settings',
        href: '/settings?tab=ebay',
      },
      secondary_button: {
        label: 'Open PosterPro',
        href: '/',
      },
      blocks: [
        {
          type: 'steps',
          items: [
            'PosterPro exchanges the authorization response for account tokens.',
            'The operator workspace refreshes the connection state and stores the linked seller account details.',
            'If the connection does not appear, return to Settings and retry the OAuth flow from the eBay panel.',
          ],
        },
      ],
    },
  },
  ebay_auth_declined: {
    key: 'ebay_auth_declined',
    label: 'eBay auth declined',
    description: 'Fallback page shown if the operator cancels or declines eBay OAuth.',
    routeGroup: 'connect',
    slug: 'ebay-auth-declined',
    statusTone: 'warning',
    statusMessage: 'The authorization request was canceled before PosterPro could finish the account connection.',
    templateName: 'OAuth Declined Standard',
    snapshot: {
      title: 'eBay Access Declined',
      summary: 'Fallback page shown when the operator cancels or declines eBay authorization.',
      hero: {
        eyebrow: 'Account connection',
        title: 'Authorization was canceled',
        body: 'Return to PosterPro whenever you are ready to restart the eBay connection workflow.',
      },
      primary_button: {
        label: 'Return to settings',
        href: '/settings?tab=ebay',
      },
      secondary_button: {
        label: 'Open trust center',
        href: '/site/trust-center',
      },
      blocks: [
        {
          type: 'rich_text',
          html:
            '<p>The authorization request ended before PosterPro could finish connecting the eBay account.</p><p>Return to Settings to start the connection again after confirming you are signed in to the correct seller account.</p>',
        },
        {
          type: 'cta',
          title: 'Ready to try again?',
          body: 'Restart the connection flow from the eBay settings panel once the operator is back in the correct account context.',
          button: {
            label: 'Go to settings',
            href: '/settings?tab=ebay',
          },
        },
      ],
    },
  },
};

export const CMS_PAGE_ORDER = Object.keys(CMS_PAGE_DEFINITIONS);
export const CMS_PAGE_KEYS = CMS_PAGE_ORDER;
export const CMS_PAGE_CONFIG = CMS_PAGE_ORDER.map((key) => CMS_PAGE_DEFINITIONS[key]);

export const STANDARD_CMS_TEMPLATE_PACK = {
  id: 'standard-operator-trust-pack',
  name: 'Standard Operator Trust Pack',
  description: 'A clean baseline pack for trust, onboarding, policy, and OAuth handoff pages.',
};

function cloneSnapshot(snapshot) {
  return JSON.parse(JSON.stringify(snapshot));
}

export function getCmsPageDefinition(pageKey) {
  return CMS_PAGE_DEFINITIONS[pageKey] || CMS_PAGE_DEFINITIONS.privacy_policy;
}

export function createDefaultDraftPage(pageKey) {
  const definition = getCmsPageDefinition(pageKey);
  const snapshot = cloneSnapshot(definition.snapshot);
  return {
    slug: definition.slug,
    route_group: definition.routeGroup,
    status: 'published',
    updated_at: null,
    published_at: null,
    draft: snapshot,
    published: cloneSnapshot(definition.snapshot),
  };
}

export function createDefaultCmsPages() {
  return CMS_PAGE_ORDER.reduce((pages, key) => {
    pages[key] = createDefaultDraftPage(key);
    return pages;
  }, {});
}

export function buildTemplateDraftForPage(pageKey) {
  const definition = getCmsPageDefinition(pageKey);
  return {
    slug: definition.slug,
    route_group: definition.routeGroup,
    draft: cloneSnapshot(definition.snapshot),
  };
}
