/* eslint-disable @next/next/no-html-link-for-pages */
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';
import {
  BarChart3,
  Bot,
  Briefcase,
  FolderInput,
  LayoutDashboard,
  ListChecks,
  Menu,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  Rocket,
  Search,
  Settings2,
  ShieldCheck,
  ShoppingCart,
  Store,
  User,
  Wrench,
} from 'lucide-react';

import { useAuth } from '../../contexts/AuthContext';
import Button from '../ui/button';
import Drawer from '../ui/drawer';
import Input from '../ui/input';
import StatusPill from '../ui/status-pill';

function buildNavGroups(user) {
  return [
    {
      label: 'Workspace',
      description: 'Daily intake and listing work.',
      items: [
        { href: '/app', label: 'Dashboard', icon: LayoutDashboard },
        { href: '/intake', label: 'Intake', icon: FolderInput },
        { href: '/listings', label: 'Listings', icon: ListChecks },
        { href: '/inventory', label: 'Inventory', icon: Package },
      ],
    },
    {
      label: 'Selling',
      description: 'Publishing, offers, and sales.',
      items: [
        { href: '/publishing', label: 'Publishing', icon: Rocket },
        { href: '/sales', label: 'Sales', icon: ShoppingCart },
        { href: '/offers', label: 'Offers', icon: Store },
      ],
    },
      {
        label: 'System',
        description: 'Settings, jobs, and reporting.',
        items: [
          { href: '/analytics', label: 'Analytics', icon: BarChart3 },
        { href: '/settings/ebay', label: 'Marketplace setup', icon: Wrench },
          { href: '/settings', label: 'Settings', icon: Settings2 },
          { href: '/jobs', label: 'Jobs', icon: Briefcase },
          ...(user?.can_access_vine_import ? [{ href: '/imports/vine', label: 'Vine Import', icon: ShieldCheck }] : []),
        ],
      },
  ];
}

function findActiveNavItem(navGroups, isSelected) {
  for (const group of navGroups) {
    for (const item of Array.isArray(group?.items) ? group.items : []) {
      if (isSelected(item.href)) {
        return { group, item };
      }
    }
  }
  return null;
}

function NavGroup({ title, description, items, isSelected, onNavigate, collapsed = false }) {
  return (
    <section className="pp-sidebar-panel p-3.5">
      {!collapsed ? <p className="pp-sidebar-label px-2">{title}</p> : null}
      {!collapsed && description ? <p className="px-2 pt-1 text-xs leading-5 text-[var(--pp-shell-soft-copy)]">{description}</p> : null}
      <div className="mt-3 space-y-2">
        {items.map((item) => {
          const Icon = item.icon;
          const selected = isSelected(item.href);
          return (
            <a
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              title={collapsed ? item.label : undefined}
              className={[
                'pp-sidebar-link flex items-center gap-3 rounded-[18px] px-3 py-3 transition',
                collapsed ? 'justify-center' : '',
                selected ? 'is-active' : '',
              ].join(' ')}
            >
              <span
                className={[
                  'pp-sidebar-icon inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[14px]',
                ].join(' ')}
              >
                <Icon size={16} />
              </span>
              {!collapsed ? (
                <span className="min-w-0">
                  <span className="block text-sm font-semibold leading-5">{item.label}</span>
                  <span className="mt-0.5 block text-xs leading-5 text-[var(--pp-shell-soft-copy)]">{title}</span>
                </span>
              ) : null}
            </a>
          );
        })}
      </div>
    </section>
  );
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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const navGroups = buildNavGroups(user);
  const activePath = active || router.pathname;
  const activeHref = router.asPath || activePath;

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
  const activeNav = findActiveNavItem(navGroups, isSelected);
  const currentGroup = activeNav?.group || navGroups[0] || null;
  const currentGroupItems = (Array.isArray(currentGroup?.items) ? currentGroup.items : []).filter(Boolean);
  const subnavSections = Array.isArray(subnav?.sections)
    ? subnav.sections
        .filter((section) => section && Array.isArray(section.items))
        .map((section) => ({
          ...section,
          items: section.items.filter(Boolean),
        }))
    : [];

  const contentWidthClass =
    contentWidth === 'narrow' ? 'max-w-[940px]' : contentWidth === 'wide' ? 'max-w-[1320px]' : 'max-w-[1180px]';

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem('posterpro.sidebar.collapsed');
      if (saved === '1') setSidebarCollapsed(true);
    } catch {
      return undefined;
    }
    return undefined;
  }, []);

  const toggleSidebar = () => {
    setSidebarCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem('posterpro.sidebar.collapsed', next ? '1' : '0');
      } catch {
        return next;
      }
      return next;
    });
  };

  const submitSearch = (event) => {
    event.preventDefault();
    const value = searchValue.trim();
    router.push(value ? `/listings?q=${encodeURIComponent(value)}` : '/listings');
  };

  const renderNav = (onNavigate) => (
    <div className="space-y-4">
      <section className="pp-sidebar-brand-panel p-5 text-[var(--pp-shell-copy)]">
        <a href="/app" className="flex items-center gap-3">
          <div className="pp-sidebar-brand-mark flex h-12 w-12 items-center justify-center rounded-[18px] font-[var(--pp-heading-font)] text-xl font-bold text-white">
            PP
          </div>
          {!sidebarCollapsed ? (
          <div className="min-w-0">
            <p className="font-[var(--pp-heading-font)] text-xl font-semibold tracking-[-0.04em] text-white">PosterPro</p>
            <p className="text-sm text-[var(--pp-shell-soft-copy)]">Reseller operations system</p>
          </div>
          ) : null}
        </a>
        {!sidebarCollapsed ? (
        <div className="mt-5 grid gap-3">
          <div className="rounded-[18px] border border-white/10 bg-white/5 px-3 py-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--pp-shell-soft-copy)]">Current lane</p>
            <p className="mt-2 text-sm font-semibold text-white">{activeNav?.item?.label || title}</p>
            <p className="mt-1 text-xs leading-5 text-[var(--pp-shell-soft-copy)]">
              {activeNav?.group?.description || 'Navigate between intake, listings, publishing, and system setup.'}
            </p>
          </div>
        </div>
        ) : null}
        <div className="mt-4 flex flex-wrap gap-2">
          <StatusPill status={autonomousConfig?.autonomous_mode ? 'success' : 'default'} label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'} />
          <StatusPill status={user ? 'success' : 'warning'} label={user?.email ? 'Signed in' : 'Account pending'} />
        </div>
      </section>

      {navGroups.filter(Boolean).map((group) => (
        <NavGroup key={group.label} title={group.label} description={group.description} items={group.items} isSelected={isSelected} onNavigate={onNavigate} collapsed={sidebarCollapsed} />
      ))}

      <section className="pp-sidebar-panel p-4 text-[var(--pp-shell-copy)]">
        {!sidebarCollapsed ? <p className="pp-sidebar-label">Account</p> : null}
        <div className={`mt-3 flex items-center gap-3 rounded-[18px] border border-white/10 bg-white/5 px-3 py-3 ${sidebarCollapsed ? 'justify-center' : ''}`}>
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-[14px] border border-white/10 bg-white/10 text-white">
            <User size={16} />
          </span>
          {!sidebarCollapsed ? (
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-white">{user?.full_name || user?.email || 'Account'}</p>
            <p className="truncate text-xs text-[var(--pp-shell-soft-copy)]">{user?.email || 'Not signed in'}</p>
          </div>
          ) : null}
        </div>
        <Button
          variant="secondary"
          size="sm"
          className={`mt-3 justify-center border-white/10 bg-white/10 text-white hover:bg-white hover:text-[var(--pp-primary)] ${sidebarCollapsed ? 'w-full px-0' : 'w-full'}`}
          onClick={async () => {
            await logout();
            window.location.href = '/login';
          }}
        >
          {sidebarCollapsed ? 'Out' : 'Sign out'}
        </Button>
      </section>
    </div>
  );

  const sectionBadge = subnav ? (
    <div className="pp-surface-panel p-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="pp-topbar-kicker">{subnav.eyebrow || 'Section'}</p>
          <h2 className="pp-topbar-title mt-2 text-[1.4rem]">{subnav.title}</h2>
          {subnav.description ? <p className="pp-topbar-subtitle mt-2 max-w-3xl text-sm leading-6">{subnav.description}</p> : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill status={autonomousConfig?.autonomous_mode ? 'success' : 'default'} label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'} />
          <StatusPill status={user ? 'success' : 'warning'} label={user?.email ? 'Signed in' : 'Not signed in'} />
        </div>
      </div>
      {subnavSections.length ? (
      <div className="mt-4 flex flex-wrap gap-2">
          {subnavSections.flatMap((section) =>
            section.items.map((item) => (
              <button
                key={item.key || item.label}
                type="button"
                onClick={typeof item.onClick === 'function' ? item.onClick : undefined}
                className={[
                  'rounded-full border px-3 py-2 text-sm font-semibold transition',
                  item.active
                    ? 'border-[#bfd4ef] bg-[var(--pp-primary)] text-white'
                    : 'border-[var(--pp-border)] bg-[var(--pp-surface-strong)] text-[var(--pp-muted)] hover:border-[#b8a98d] hover:bg-white hover:text-[var(--pp-text)]',
                ].join(' ')}
              >
                {item.label}
              </button>
            )),
          )}
        </div>
      ) : null}
    </div>
  ) : null;

  return (
    <div className="posterpro-app-shell min-h-screen bg-[var(--pp-bg)] text-[var(--pp-text)]">
      <div className={`grid min-h-screen ${sidebarCollapsed ? 'md:grid-cols-[104px_minmax(0,1fr)]' : 'md:grid-cols-[340px_minmax(0,1fr)]'}`}>
        <aside className="pp-shell-sidebar-surface hidden md:block">
          <div className="sticky top-0 h-screen overflow-y-auto px-4 py-5">{renderNav()}</div>
        </aside>

        <div className="pp-shell-content-wrap min-w-0">
          <header className="pp-shell-header-surface sticky top-0 z-30 backdrop-blur-xl">
            <div className="mx-auto flex w-full max-w-[1520px] items-center gap-3 px-4 py-3 md:px-6">
              <Button variant="secondary" size="sm" className="gap-2 px-3 shadow-none md:hidden" onClick={() => setMobileMenuOpen(true)} aria-label="Open navigation">
                <Menu size={18} />
                Menu
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="hidden gap-2 px-3 shadow-none md:inline-flex"
                onClick={toggleSidebar}
                aria-label={sidebarCollapsed ? 'Expand navigation' : 'Collapse navigation'}
                title={sidebarCollapsed ? 'Expand navigation' : 'Collapse navigation'}
              >
                {sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
                {sidebarCollapsed ? 'Expand' : 'Collapse'}
              </Button>

              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-center gap-3">
                  <p className="pp-topbar-kicker">
                    {activeNav?.group?.label || 'PosterPro'}
                  </p>
                  <span className="hidden h-1.5 w-1.5 rounded-full bg-[#c3ac89] sm:inline-flex" />
                  <h1 className="pp-topbar-title truncate text-[1.02rem]">{title}</h1>
                </div>
                <p className="pp-topbar-subtitle hidden text-xs sm:block">
                  {activeNav?.item?.label
                    ? `${activeNav.item.label} is active. Navigation, search, and task context stay fixed above the fold.`
                    : 'One workspace for intake, listings, publishing, and sold-item tracking.'}
                </p>
              </div>

              <form onSubmit={submitSearch} className="relative hidden xl:block">
                <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--pp-shell-soft-copy)]" size={16} />
                <Input
                  aria-label="Global search"
                  value={searchValue}
                  onChange={(event) => setSearchValue(event.target.value)}
                  placeholder="Search listings"
                  className="w-[280px] rounded-2xl border-[var(--pp-border)] bg-white pl-9"
                />
              </form>

              <a href="/intake" className="hidden lg:inline-flex">
                <Button variant="outline" size="sm">
                  Quick import
                </Button>
              </a>

              <button
                type="button"
                onClick={onToggleAutonomous}
                className="hidden items-center gap-2 rounded-2xl border border-[var(--pp-border)] bg-[var(--pp-surface)] px-3 py-2 text-xs font-semibold text-[var(--pp-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.7)] md:inline-flex"
                aria-label="Toggle automation mode"
              >
                <Bot size={14} className="text-[var(--pp-primary)]" />
                <StatusPill status={autonomousConfig?.autonomous_mode ? 'success' : 'default'} label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'} />
              </button>
            </div>
          </header>

          <main className="mx-auto w-full max-w-[1520px] px-4 py-6 pb-24 md:px-6">
            <div className="space-y-6">
              <div className="pp-shell-lane-strip px-1">
                <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                  <div className="min-w-0">
                    <p className="pp-topbar-kicker">{currentGroup?.label || 'Workspace lane'}</p>
                    <p className="mt-2 text-sm leading-6 text-[var(--pp-muted)]">
                      {currentGroup?.description || 'Move between related tools without hunting through a single long page.'}
                    </p>
                  </div>
                  <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-2 xl:max-w-[760px] xl:grid-cols-4">
                    {currentGroupItems.map((item) => {
                      const Icon = item.icon || Settings2;
                      const selected = isSelected(item.href);
                      return (
                        <a
                          key={item.href}
                          href={item.href}
                          className={`pp-shell-lane-link ${selected ? 'is-active' : ''}`}
                        >
                          <span className="pp-shell-lane-icon">
                            <Icon size={16} />
                          </span>
                          <span className="min-w-0">
                            <span className="block truncate text-sm font-semibold">{item.label}</span>
                            <span className="mt-1 block text-xs text-[var(--pp-muted)]">{currentGroup.label}</span>
                          </span>
                        </a>
                      );
                    })}
                  </div>
                </div>
              </div>
              {sectionBadge}
              <div className={`mx-auto w-full ${contentWidthClass} min-w-0 space-y-6 ${contentClassName}`}>{children}</div>
            </div>
          </main>
        </div>
      </div>

      <Drawer open={mobileMenuOpen} onClose={() => setMobileMenuOpen(false)} title="Main menu" description="Jump to the core workspace areas from one place." widthClassName="max-w-[440px]">
        <div className="space-y-4">{renderNav(() => setMobileMenuOpen(false))}</div>
      </Drawer>
    </div>
  );
}
