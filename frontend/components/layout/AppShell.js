import Link from 'next/link';
import { useRouter } from 'next/router';
import { useEffect, useMemo, useState } from 'react';
import {
  BarChart3,
  Bot,
  ChevronDown,
  FileText,
  FolderOpen,
  Layers3,
  LayoutDashboard,
  Menu,
  Package,
  Search,
  Settings2,
  ShoppingCart,
  Store,
  Tag,
  Upload,
  User,
} from 'lucide-react';

import { useAuth } from '../../contexts/AuthContext';
import Button from '../ui/button';
import Drawer from '../ui/drawer';
import Input from '../ui/input';
import StatusPill from '../ui/status-pill';

function buildNavGroups(user) {
  return [
    {
      label: 'Command Center',
      items: [
        {
          href: '/app',
          label: 'Overview',
          icon: LayoutDashboard,
          children: [
            { href: '/app', label: 'Overview' },
            { href: '/app#jobs', label: 'Jobs' },
            { href: '/app#operations', label: 'Operations' },
            { href: '/app#activity', label: 'Activity' },
            { href: '/app#channels', label: 'Channels' },
          ],
        },
        {
          href: '/intake',
          label: 'Intake & Uploads',
          icon: FolderOpen,
          children: [
            { href: '/intake', label: 'Upload Workspace' },
            { href: '/intake?tab=batches', label: 'Batch Queue' },
            { href: '/intake?tab=grouped', label: 'Grouped Items' },
            { href: '/intake#workflow', label: 'Workflow Guide' },
          ],
        },
        {
          href: '/listings',
          label: 'Listings',
          icon: FileText,
          children: [
            { href: '/listings', label: 'All Listings' },
            { href: '/listings/new', label: 'New Item' },
            { href: '/listings?tab=review', label: 'Review Queue' },
          ],
        },
        {
          href: '/inventory',
          label: 'Inventory',
          icon: Package,
          children: [
            { href: '/inventory', label: 'Catalog' },
            { href: '/inventory?tab=batches', label: 'Batches' },
            { href: '/inventory#bulk-actions', label: 'Bulk Actions' },
          ],
        },
        ...(user?.can_access_vine_import ? [{ href: '/imports/vine', label: 'Vine Import', icon: Upload }] : []),
      ],
    },
    {
      label: 'Commerce',
      items: [
        {
          href: '/publishing',
          label: 'Marketplace Publishing',
          icon: Store,
          children: [
            { href: '/publishing?tab=queue', label: 'Queue' },
            { href: '/publishing?tab=approvals', label: 'Approvals' },
            { href: '/publishing?tab=live', label: 'Live Listings' },
            { href: '/publishing?tab=sync', label: 'Sync Status' },
            { href: '/jobs', label: 'Jobs Console' },
          ],
        },
        {
          href: '/sales',
          label: 'Sales',
          icon: ShoppingCart,
          children: [
            { href: '/sales', label: 'Orders' },
            { href: '/sales#mix', label: 'Marketplace Mix' },
            { href: '/sales#timeline', label: 'Sales Timeline' },
          ],
        },
        {
          href: '/offers',
          label: 'Automation & Offers',
          icon: Tag,
          children: [
            { href: '/offers', label: 'Offer Rules' },
            { href: '/offers#history', label: 'Offer History' },
          ],
        },
      ],
    },
    {
      label: 'Insights & Admin',
      items: [
        {
          href: '/analytics',
          label: 'Analytics',
          icon: BarChart3,
          children: [
            { href: '/analytics', label: 'Revenue' },
            { href: '/analytics?tab=performance', label: 'Performance' },
            { href: '/analytics?tab=pricing', label: 'Pricing' },
          ],
        },
        {
          href: '/settings?tab=ebay',
          label: 'Integrations',
          icon: Layers3,
          children: [
            { href: '/settings?tab=ebay', label: 'eBay OAuth' },
            { href: '/settings?tab=marketplaces', label: 'Marketplaces' },
            { href: '/settings?tab=api-keys', label: 'API Keys' },
          ],
        },
        {
          href: '/settings',
          label: 'Settings',
          icon: Settings2,
          children: [
            { href: '/settings?tab=overview', label: 'Overview' },
            { href: '/settings?tab=profile', label: 'Profile' },
            { href: '/settings?tab=workflow', label: 'Workflow' },
            { href: '/settings?tab=automation', label: 'Automation' },
            { href: '/settings?tab=server', label: 'Server' },
          ],
        },
      ],
    },
  ];
}

export default function AppShell({
  active,
  title = 'Dashboard',
  autonomousConfig,
  onToggleAutonomous,
  subnav,
  contentClassName = '',
  contentWidth = 'default',
  children,
}) {
  const { user, logout } = useAuth();
  const router = useRouter();
  const [searchValue, setSearchValue] = useState('');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const navGroups = buildNavGroups(user);
  const activePath = active || router.pathname;
  const activeHref = router.asPath || activePath;
  const mobilePrimaryItems = [
    { href: '/app', label: 'Dashboard', icon: LayoutDashboard },
    { href: '/intake', label: 'Intake', icon: FolderOpen },
    { href: '/listings', label: 'Listings', icon: FileText },
    { href: '/publishing', label: 'Publish', icon: Store },
  ];

  const normalizeHref = (href) => (href || '').replace(/#.*$/, '');
  const isSelected = (href) => {
    const normalizedHref = normalizeHref(href);
    const normalizedActive = normalizeHref(activeHref);
    return (
      normalizedActive === normalizedHref ||
      activePath === normalizedHref ||
      activePath.startsWith(`${normalizedHref}/`) ||
      normalizedActive.startsWith(`${normalizedHref}?`)
    );
  };

  const defaultExpanded = useMemo(() => {
    const expanded = {};
    navGroups.forEach((group) => {
      group.items.forEach((item) => {
        if (item.children?.some((child) => isSelected(child.href)) || isSelected(item.href)) {
          expanded[item.href] = true;
        }
      });
    });
    return expanded;
  }, [activeHref]);
  const [expandedItems, setExpandedItems] = useState(defaultExpanded);

  useEffect(() => {
    setExpandedItems((current) => ({ ...defaultExpanded, ...current }));
  }, [defaultExpanded]);

  const toggleExpanded = (href) => {
    setExpandedItems((current) => ({
      ...current,
      [href]: !current[href],
    }));
  };

  const submitSearch = (event) => {
    event.preventDefault();
    const value = searchValue.trim();
    router.push(value ? `/listings?q=${encodeURIComponent(value)}` : '/listings');
  };

  const contentWidthClass =
    contentWidth === 'narrow'
      ? 'max-w-[880px]'
      : contentWidth === 'wide'
      ? 'max-w-[1180px]'
      : 'max-w-[1040px]';

  return (
    <div className="posterpro-app-shell min-h-screen text-[#101828]">
      <div className="grid min-h-screen lg:grid-cols-[272px_minmax(0,1fr)]">
        <aside className="pp-app-sidebar hidden min-h-screen lg:block">
          <div className="sticky top-0 p-5">
            <div className="border-b border-[rgba(148,163,184,0.18)] pb-5">
              <Link href="/app" className="block">
                <strong className="pp-app-brand__title block">PosterPro</strong>
                <span className="pp-app-brand__subtitle mt-1 block text-sm">CMS resale workspace</span>
              </Link>
            </div>
            <div className="pp-app-status-card mt-5 p-4">
              <p className="pp-app-status-card__label">Reseller OS</p>
              <p className="pp-app-status-card__title mt-2 text-sm">
                {autonomousConfig?.autonomous_mode ? 'Automation enabled' : 'Operator-led review'}
              </p>
              <p className="pp-app-status-card__copy mt-1 text-sm leading-6">Use one structured admin workspace for intake, review, publishing, and system setup.</p>
            </div>
            <nav className="mt-6 space-y-6">
              {navGroups.map((group) => (
                <div key={group.label}>
                  <p className="pp-app-nav-group-label mb-2 px-3 text-xs">{group.label}</p>
                  <div className="space-y-1.5">
                    {group.items.map((item) => {
                      const Icon = item.icon;
                      const selected = isSelected(item.href);
                      const expanded = item.children?.length ? !!expandedItems[item.href] : false;
                      return (
                        <div key={item.label} className="rounded-[14px]">
                          <div
                            className={`pp-app-nav-item group flex items-center rounded-[14px] border px-3 py-3 ${selected ? 'is-active' : ''}`}
                          >
                            <Link href={item.href} onClick={() => setMobileMenuOpen(false)} className="flex min-w-0 flex-1 items-center gap-3">
                              <span className={`pp-app-nav-item__icon inline-flex h-9 w-9 items-center justify-center rounded-[11px] ${selected ? 'is-active' : ''}`}>
                                <Icon size={17} />
                              </span>
                              <span className="min-w-0">
                                <span className="pp-app-nav-item__title block truncate text-sm">{item.label}</span>
                                {item.children?.length ? <span className="pp-app-nav-item__meta mt-0.5 block text-xs">{item.children.length} related views</span> : null}
                              </span>
                            </Link>
                            {item.children?.length ? (
                              <button
                                type="button"
                                onClick={() => toggleExpanded(item.href)}
                                className="pp-app-nav-item__toggle ml-2 inline-flex h-8 w-8 items-center justify-center rounded-[10px]"
                                aria-label={`Toggle ${item.label} menu`}
                              >
                                <ChevronDown size={16} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
                              </button>
                            ) : null}
                          </div>
                          {item.children?.length && expanded ? (
                            <div className="pp-app-nav-children mt-2 ml-5 border-l pl-4">
                              <div className="space-y-1.5">
                                {item.children.map((child) => {
                                  const childSelected = isSelected(child.href);
                                  return (
                                    <Link
                                      key={child.href}
                                      href={child.href}
                                      className={`pp-app-nav-child-link flex items-center rounded-[10px] px-3 py-2 text-sm ${childSelected ? 'is-active' : ''}`}
                                    >
                                      {child.label}
                                    </Link>
                                  );
                                })}
                              </div>
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </nav>
          </div>
        </aside>

        <div className="min-w-0">
          <header className="pp-app-header sticky top-0 z-30 border-b backdrop-blur">
            <div className="mx-auto flex h-[68px] w-full max-w-[1320px] items-center gap-3 px-4 md:px-6">
              <Link href="/app" className="pp-app-header__brand text-sm lg:hidden">
                PosterPro
              </Link>

              <div className="min-w-0 flex-1">
                <h1 className="pp-app-header__title truncate text-base">{title}</h1>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="icon"
                  className="lg:hidden"
                  onClick={() => setMobileMenuOpen(true)}
                  title="Open workspace navigation"
                >
                  <Menu size={18} />
                </Button>
                <form onSubmit={submitSearch} className="relative hidden md:block">
                  <Search className="pp-app-search__icon pointer-events-none absolute left-3 top-1/2 -translate-y-1/2" size={16} />
                  <Input
                    value={searchValue}
                    onChange={(event) => setSearchValue(event.target.value)}
                    placeholder="Search listings"
                    className="pp-app-search w-[220px] pl-9"
                  />
                </form>
                <button
                  type="button"
                  onClick={onToggleAutonomous}
                  className="pp-app-mode-toggle inline-flex h-9 items-center gap-2 rounded-full border px-3 text-sm font-medium"
                >
                  <Bot size={14} className="text-[var(--pp-dashboard-accent)]" />
                  <StatusPill
                    status={autonomousConfig?.autonomous_mode ? 'success' : 'default'}
                    label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'}
                  />
                </button>
                <div className="pp-app-user-pill hidden items-center gap-2 rounded-[10px] border px-3 py-2 lg:flex">
                  <span className="pp-app-user-pill__icon inline-flex h-8 w-8 items-center justify-center rounded-full">
                    <User size={15} />
                  </span>
                  <span className="pp-app-user-pill__text max-w-[140px] truncate text-sm font-medium">
                    {user?.full_name || user?.email || 'Account'}
                  </span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="px-3"
                  onClick={async () => {
                    await logout();
                    window.location.href = '/login';
                  }}
                >
                  Sign out
                </Button>
              </div>
            </div>
          </header>

          <main className="mx-auto w-full max-w-[1320px] px-4 py-6 pb-24 md:px-6">
            {subnav ? (
              <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
                <div className="lg:hidden">
                  <div className="pp-app-subnav-card p-4">
                    <p className="pp-app-subnav-card__label">{subnav.eyebrow || 'Section menu'}</p>
                    <p className="pp-app-subnav-card__title mt-2 text-lg">{subnav.title}</p>
                    {subnav.description ? <p className="pp-app-subnav-card__copy mt-2 text-sm leading-6">{subnav.description}</p> : null}
                    <div className="mt-4 flex gap-2 overflow-x-auto pb-1">
                      {subnav.sections?.flatMap((section) =>
                        section.items.map((item) => (
                          <button
                            key={item.key || item.label}
                            type="button"
                            onClick={item.onClick}
                            className={`pp-app-subnav-chip shrink-0 rounded-full border px-3 py-2 text-sm font-medium ${item.active ? 'is-active' : ''}`}
                          >
                            {item.label}
                          </button>
                        )),
                      )}
                    </div>
                  </div>
                </div>
                <aside className="hidden lg:block">
                  <div className="sticky top-[88px] space-y-4">
                    <div className="pp-app-subnav-card p-4">
                      <p className="pp-app-subnav-card__label">{subnav.eyebrow || 'Section menu'}</p>
                      <p className="pp-app-subnav-card__title mt-2 text-lg">{subnav.title}</p>
                      {subnav.description ? <p className="pp-app-subnav-card__copy mt-2 text-sm leading-6">{subnav.description}</p> : null}
                    </div>
                    <div className="pp-app-subnav-card p-3">
                      <div className="space-y-4">
                        {subnav.sections?.map((section) => (
                          <div key={section.label}>
                            <p className="pp-app-subnav-card__label mb-2 px-3 text-[11px]">{section.label}</p>
                            <div className="space-y-1.5">
                              {section.items.map((item) => (
                                <button
                                  key={item.key || item.label}
                                  type="button"
                                  onClick={item.onClick}
                                  className={`pp-app-subnav-item flex w-full items-center justify-between rounded-[12px] px-3 py-3 text-left ${item.active ? 'is-active' : ''}`}
                                >
                                  <span className="min-w-0">
                                    <span className="pp-app-subnav-item__title block truncate text-sm">{item.label}</span>
                                    {item.description ? <span className="pp-app-subnav-item__copy mt-0.5 block truncate text-xs">{item.description}</span> : null}
                                  </span>
                                  {item.badge ? <span className="pp-app-subnav-item__badge ml-3 rounded-full px-2 py-1 text-[11px] font-semibold">{item.badge}</span> : null}
                                </button>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </aside>

                <div className={`min-w-0 ${contentWidthClass} space-y-5 ${contentClassName}`}>{children}</div>
              </div>
            ) : (
              <div className={`${contentWidthClass} space-y-5 ${contentClassName}`}>{children}</div>
            )}
          </main>
        </div>
      </div>

      <nav className="pp-app-mobile-bar fixed bottom-0 left-0 right-0 z-40 border-t p-2 backdrop-blur lg:hidden">
        <div className="mx-auto grid max-w-3xl grid-cols-5 gap-1">
          {mobilePrimaryItems.map((item) => {
            const Icon = item.icon;
            const selected = isSelected(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`pp-app-mobile-link flex flex-col items-center justify-center gap-1 rounded-[10px] px-2 py-2 text-[11px] font-medium ${selected ? 'is-active' : ''}`}
              >
                <Icon size={16} />
                <span>{item.label}</span>
              </Link>
            );
          })}
          <button
            type="button"
            onClick={() => setMobileMenuOpen(true)}
            className={`pp-app-mobile-link flex flex-col items-center justify-center gap-1 rounded-[10px] px-2 py-2 text-[11px] font-medium ${
              isSelected('/settings') || mobileMenuOpen ? 'is-active' : ''
            }`}
          >
            <Settings2 size={16} />
            <span>More</span>
          </button>
        </div>
      </nav>

      <Drawer
        open={mobileMenuOpen}
        onClose={() => setMobileMenuOpen(false)}
        title="Workspace navigation"
        description="Move between setup, operations, and marketplace workflow."
        widthClassName="max-w-[420px]"
      >
        <div className="space-y-5">
          <div className="pp-app-subnav-card p-4">
            <p className="pp-app-subnav-card__title text-sm">{user?.full_name || user?.email || 'PosterPro account'}</p>
            <p className="pp-app-subnav-card__copy mt-1 text-sm">Signed in workspace operator</p>
          </div>

          {navGroups.map((group) => (
            <div key={group.label}>
              <p className="pp-app-nav-group-label mb-2 text-xs">{group.label}</p>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const selected = isSelected(item.href);
                  return (
                    <div key={item.href} className="pp-app-drawer-card rounded-[12px] border">
                      <Link
                        href={item.href}
                        onClick={() => setMobileMenuOpen(false)}
                        className={`pp-app-drawer-link flex items-center justify-between px-3 py-3 text-sm font-medium ${selected ? 'is-active' : ''}`}
                      >
                        <span className="flex items-center gap-3">
                          <Icon size={17} className={selected ? 'text-[var(--pp-dashboard-accent)]' : 'text-[#6c7a91]'} />
                          {item.label}
                        </span>
                        {selected ? <StatusPill status="success" label="Open" /> : null}
                      </Link>
                      {item.children?.length ? (
                        <div className="border-t border-[#f2f4f7] px-3 py-2">
                          <div className="flex flex-wrap gap-2">
                            {item.children.map((child) => (
                              <Link
                                key={child.href}
                                href={child.href}
                                onClick={() => setMobileMenuOpen(false)}
                                className={`rounded-full border px-3 py-1.5 text-xs font-medium ${
                                  isSelected(child.href)
                                    ? 'border-[#bfd2ff] bg-[#eef4ff] text-[#2563eb]'
                                    : 'border-[#e5e7eb] bg-white text-[#667085]'
                                }`}
                              >
                                {child.label}
                              </Link>
                            ))}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}

          <Button
            variant="outline"
            className="w-full"
            onClick={async () => {
              await logout();
              window.location.href = '/login';
            }}
          >
            Sign out
          </Button>
        </div>
      </Drawer>
    </div>
  );
}
