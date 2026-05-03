import Head from 'next/head';
import Link from 'next/link';
import {
  ArrowRight,
  Bot,
  Camera,
  ChartColumn,
  ChevronRight,
  Database,
  Layers3,
  Package,
  Send,
} from 'lucide-react';
import styles from '../styles/LandingPage.module.css';

const MARKETPLACES = ['eBay', 'Facebook Marketplace', 'Mercari', 'Poshmark', 'Depop', 'Etsy', 'Shopify', 'Whatnot'];

const WORKFLOW_STEPS = [
  {
    number: '01',
    title: 'Import photos',
    body: 'Pull new item photos into one queue instead of chasing camera rolls and folders.',
  },
  {
    number: '02',
    title: 'Group inventory',
    body: 'Keep each item tied to its photos, notes, and status before drafting starts.',
  },
  {
    number: '03',
    title: 'Generate drafts',
    body: 'Create titles, details, and prices faster with AI-assisted listing prep.',
  },
  {
    number: '04',
    title: 'Publish and sync',
    body: 'Push listings out, track what sold, and keep inventory from drifting out of date.',
  },
];

const FEATURES = [
  { icon: Camera, title: 'Photo intake', body: 'Sort incoming photos into organized item-ready batches.' },
  { icon: Bot, title: 'AI listing drafts', body: 'Generate cleaner titles and item details from your photo sets.' },
  { icon: ChartColumn, title: 'Pricing guidance', body: 'Keep draft prices visible before anything goes live.' },
  { icon: Layers3, title: 'Inventory command center', body: 'See what is waiting, drafted, listed, and sold in one place.' },
  { icon: Send, title: 'Marketplace publishing', body: 'Prepare listings for eBay first with room to expand outward.' },
  { icon: Database, title: 'Sold-sync foundation', body: 'Track sold items so inventory does not linger after the sale.' },
];

const HERO_STATS = [
  { label: 'Draft queue', value: '42', tone: 'blue' },
  { label: 'Ready to publish', value: '19', tone: 'green' },
  { label: 'Sold today', value: '7', tone: 'amber' },
];

const HERO_LISTINGS = [
  {
    title: 'Vintage Carhartt chore coat',
    status: 'Draft ready',
    price: '$94',
    marketplaces: ['eBay', 'Mercari'],
    accent: 'blue',
  },
  {
    title: 'Nike windbreaker jacket',
    status: 'Needs pricing',
    price: '$42',
    marketplaces: ['eBay'],
    accent: 'amber',
  },
  {
    title: 'Levi’s sherpa denim jacket',
    status: 'Ready now',
    price: '$68',
    marketplaces: ['eBay', 'Poshmark', 'Depop'],
    accent: 'green',
  },
];

export default function LandingPage() {
  return (
    <div className={styles.page}>
      <Head>
        <title>PosterPro | Reseller Listing Software</title>
        <meta
          name="description"
          content="PosterPro helps resellers organize photos, build listing drafts, track inventory, and publish across marketplaces from one workspace."
        />
      </Head>

      <header className={styles.navbar}>
        <div className={styles.container}>
          <div className={styles.navbarInner}>
            <Link href="/" className={styles.wordmark}>
              PosterPro
            </Link>

            <nav className={styles.navLinks} aria-label="Primary">
              <a href="#features">Features</a>
              <a href="#workflow">Workflow</a>
              <a href="#marketplaces">Marketplaces</a>
              <a href="#pricing">Pricing</a>
            </nav>

            <div className={styles.navActions}>
              <Link href="/login" className={styles.buttonGhost}>
                Log in
              </Link>
              <Link href="/register" className={styles.buttonPrimary}>
                Start free
              </Link>
            </div>
          </div>
        </div>
      </header>

      <main>
        <section className={styles.hero}>
          <div className={`${styles.container} ${styles.heroGrid}`}>
            <div className={styles.heroContent}>
              <span className={styles.badge}>AI-powered resale workflow</span>
              <h1>From product photos to live listings, faster.</h1>
              <p>
                PosterPro helps resellers organize intake, generate listing drafts, manage inventory, and publish
                across marketplaces from one clean workspace.
              </p>

              <div className={styles.heroActions}>
                <Link href="/register" className={styles.buttonPrimaryLarge}>
                  Start free
                </Link>
                <a href="#workflow" className={styles.buttonSecondaryLarge}>
                  View workflow
                  <ChevronRight size={18} />
                </a>
              </div>

              <div className={styles.trustRow} aria-label="Product highlights">
                <span>eBay-first</span>
                <span>Inventory-focused</span>
                <span>Cross-posting ready</span>
              </div>
            </div>

            <div className={styles.heroVisual}>
              <div className={styles.mockupGlow} />
              <div className={styles.dashboard}>
                <div className={styles.browserBar}>
                  <div className={styles.browserDots}>
                    <span />
                    <span />
                    <span />
                  </div>
                  <div className={styles.browserAddress}>app.posterpro.io/dashboard</div>
                  <div className={styles.browserUser}>Seller workspace</div>
                </div>

                <div className={styles.dashboardBody}>
                  <aside className={styles.sidebar}>
                    <div className={styles.sidebarBrand}>
                      <div className={styles.sidebarLogo}>P</div>
                      <div>
                        <strong>PosterPro</strong>
                        <span>Listings</span>
                      </div>
                    </div>

                    <div className={styles.sidebarMenu}>
                      <div className={styles.sidebarItemActive}>Dashboard</div>
                      <div className={styles.sidebarItem}>Inventory</div>
                      <div className={styles.sidebarItem}>Drafts</div>
                      <div className={styles.sidebarItem}>Publishing</div>
                      <div className={styles.sidebarItem}>Sales</div>
                    </div>

                    <div className={styles.sidebarCard}>
                      <span>Active batch</span>
                      <strong>Outerwear intake</strong>
                      <p>12 items with photos attached</p>
                    </div>
                  </aside>

                  <div className={styles.mainPanel}>
                    <div className={styles.mainHeader}>
                      <div>
                        <span className={styles.overline}>Today</span>
                        <h2>Listing draft workspace</h2>
                      </div>
                      <div className={styles.headerActionsInline}>
                        <span className={styles.headerChip}>12 new photos</span>
                        <span className={styles.headerChipStrong}>3 ready now</span>
                      </div>
                    </div>

                    <div className={styles.statsGrid}>
                      {HERO_STATS.map((stat) => (
                        <article key={stat.label} className={styles.statCard}>
                          <span>{stat.label}</span>
                          <strong>{stat.value}</strong>
                          <i className={`${styles.statAccent} ${styles[`statAccent${stat.tone}`]}`} />
                        </article>
                      ))}
                    </div>

                    <div className={styles.contentGrid}>
                      <section className={styles.listPanel}>
                        <div className={styles.panelHeader}>
                          <div>
                            <strong>Draft listings</strong>
                            <span>3 items in progress</span>
                          </div>
                          <button type="button" className={styles.inlineButton}>
                            View all
                            <ArrowRight size={14} />
                          </button>
                        </div>

                        <div className={styles.listRows}>
                          {HERO_LISTINGS.map((item, index) => (
                            <article key={item.title} className={styles.listRow}>
                              <div className={`${styles.thumb} ${styles[`thumb${item.accent}`]}`}>
                                <span />
                                <span />
                              </div>
                              <div className={styles.rowCopy}>
                                <strong>{item.title}</strong>
                                <span>{item.status}</span>
                              </div>
                              <div className={styles.rowMarkets}>
                                {item.marketplaces.map((market) => (
                                  <em key={`${item.title}-${market}`}>{market}</em>
                                ))}
                              </div>
                              <div className={styles.rowPrice}>{item.price}</div>
                            </article>
                          ))}
                        </div>
                      </section>

                      <div className={styles.sidePanels}>
                        <section className={styles.marketPanel}>
                          <div className={styles.panelHeaderCompact}>
                            <strong>Marketplace status</strong>
                          </div>
                          <div className={styles.marketChipGrid}>
                            <span className={styles.marketChipActive}>eBay connected</span>
                            <span className={styles.marketChip}>Poshmark ready</span>
                            <span className={styles.marketChip}>Mercari queued</span>
                            <span className={styles.marketChip}>Depop queued</span>
                          </div>
                        </section>

                        <section className={styles.publishPanel}>
                          <div className={styles.publishIcon}>
                            <Package size={18} />
                          </div>
                          <div>
                            <strong>Ready to publish</strong>
                            <p>3 drafts have titles, prices, photos, and marketplace mapping.</p>
                          </div>
                          <button type="button" className={styles.publishButton}>
                            Push live
                          </button>
                        </section>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section id="marketplaces" className={styles.marketplaceSection}>
          <div className={styles.container}>
            <div className={styles.marketplaceStrip}>
              {MARKETPLACES.map((marketplace) => (
                <span key={marketplace} className={styles.marketplaceBadge}>
                  {marketplace}
                </span>
              ))}
            </div>
          </div>
        </section>

        <section id="workflow" className={styles.section}>
          <div className={styles.container}>
            <div className={styles.sectionIntro}>
              <h2>Built around the way resellers actually work.</h2>
            </div>

            <div className={styles.workflowGrid}>
              {WORKFLOW_STEPS.map((step) => (
                <article key={step.number} className={styles.workflowCard}>
                  <span className={styles.stepNumber}>{step.number}</span>
                  <h3>{step.title}</h3>
                  <p>{step.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="features" className={styles.sectionAlt}>
          <div className={styles.container}>
            <div className={styles.sectionIntro}>
              <h2>Everything needed to move photos into sold listings.</h2>
            </div>

            <div className={styles.featureGrid}>
              {FEATURES.map((feature) => {
                const Icon = feature.icon;
                return (
                  <article key={feature.title} className={styles.featureCard}>
                    <div className={styles.featureIcon}>
                      <Icon size={20} />
                    </div>
                    <h3>{feature.title}</h3>
                    <p>{feature.body}</p>
                  </article>
                );
              })}
            </div>
          </div>
        </section>

        <section id="pricing" className={styles.ctaSection}>
          <div className={styles.container}>
            <div className={styles.ctaBand}>
              <div>
                <h2>Your inventory should not live in chaos.</h2>
                <p>
                  Bring intake, listing prep, publishing, and inventory follow-through into one organized reseller
                  workspace.
                </p>
              </div>

              <div className={styles.ctaActions}>
                <Link href="/register" className={styles.buttonLight}>
                  Start free
                </Link>
                <Link href="/login" className={styles.buttonOutlineLight}>
                  Log in
                </Link>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className={styles.footer}>
        <div className={styles.container}>
          <div className={styles.footerRow}>
            <span className={styles.footerBrand}>PosterPro</span>
            <div className={styles.footerLinks}>
              <a href="#features">Features</a>
              <a href="#workflow">Workflow</a>
              <a href="#marketplaces">Marketplaces</a>
              <Link href="/login">Log in</Link>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
