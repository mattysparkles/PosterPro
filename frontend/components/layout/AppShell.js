import Link from 'next/link';
import { useRouter } from 'next/router';
import { useState } from 'react';
import {
  BarChart3,
  Bot,
  Briefcase,
  FolderInput,
  LayoutDashboard,
  ListChecks,
  Menu,
  Package,
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
        { href: '/settings?tab=marketplaces', label: 'Marketplace setup', icon: Wrench },
        { href: '/settings', label: 'Settings', icon: Settings2 },
        { href: '/jobs', label: 'Jobs', icon: Briefcase },
        ...(user?.can_access_vine_import ? [{ href: '/imports/vine', label: 'Vine Import', icon: ShieldCheck }] : []),
      ],
    },
  ];
}

function NavGroup({ title, description, items, isSelected, onNavigate }) {
  return (
    <section className="rounded-[18px] border border-[var(--pp-border)] bg-white p-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <p className="px-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-[var(--pp-shell-soft-copy)]">{title}</p>
      {description ? <p className="px-2 pt-1 text-xs leading-5 text-[var(--pp-muted)]">{description}</p> : null}
      <div className="space-y-1.5">
        {items.map((item) => {
          const Icon = item.icon;
          const selected = isSelected(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              className={[
                'flex items-center gap-3 rounded-2xl border px-3 py-3 transition',
                selected
                  ? 'border-sky-200 bg-sky-50 text-[var(--pp-primary)]'
                  : 'border-transparent text-[var(--pp-text)] hover:border-[var(--pp-border)] hover:bg-[var(--pp-shell-hover)] hover:text-[var(--pp-text)]',
              ].join(' ')}
            >
              <span
                className={[
                  'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border',
                  selected ? 'border-sky-200 bg-white text-[var(--pp-primary)]' : 'border-[var(--pp-border)] bg-[var(--pp-shell-hover)] text-[var(--pp-muted)]',
                ].join(' ')}
              >
                <Icon size={16} />
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-semibold leading-5">{item.label}</span>
              </span>
            </Link>
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

  const contentWidthClass =
    contentWidth === 'narrow' ? 'max-w-[940px]' : contentWidth === 'wide' ? 'max-w-[1320px]' : 'max-w-[1180px]';

  const submitSearch = (event) => {
    event.preventDefault();
    const value = searchValue.trim();
    router.push(value ? `/listings?q=${encodeURIComponent(value)}` : '/listings');
  };

  const renderNav = (onNavigate) => (
    <div className="space-y-4">
      <section className="rounded-[24px] border border-[var(--pp-border)] bg-white p-4 text-[var(--pp-text)] shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        <Link href="/app" className="flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--pp-shell-hover)] text-lg font-bold text-[var(--pp-primary)]">
            P
          </div>
          <div className="min-w-0">
            <p className="text-lg font-semibold tracking-[-0.03em] text-[var(--pp-text)]">PosterPro</p>
            <p className="text-sm text-[var(--pp-muted)]">Reseller operations workspace</p>
          </div>
        </Link>
        <div className="mt-4 flex flex-wrap gap-2">
          <StatusPill status={autonomousConfig?.autonomous_mode ? 'success' : 'default'} label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'} />
          <StatusPill status={user ? 'success' : 'warning'} label={user?.email ? 'Signed in' : 'Account pending'} />
        </div>
      </section>

      {navGroups.map((group) => (
        <NavGroup key={group.label} title={group.label} description={group.description} items={group.items} isSelected={isSelected} onNavigate={onNavigate} />
      ))}

      <section className="rounded-[24px] border border-[var(--pp-border)] bg-white p-4 text-[var(--pp-text)] shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[var(--pp-shell-soft-copy)]">Account</p>
        <div className="mt-3 flex items-center gap-3 rounded-2xl border border-[var(--pp-border)] bg-[var(--pp-shell-hover)] px-3 py-3">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-white text-[var(--pp-primary)]">
            <User size={16} />
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-[var(--pp-text)]">{user?.full_name || user?.email || 'Account'}</p>
            <p className="truncate text-xs text-[var(--pp-muted)]">{user?.email || 'Not signed in'}</p>
          </div>
        </div>
        <Button
          variant="secondary"
          size="sm"
          className="mt-3 w-full justify-center"
          onClick={async () => {
            await logout();
            window.location.href = '/login';
          }}
        >
          Sign out
        </Button>
      </section>
    </div>
  );

  const sectionBadge = subnav ? (
    <div className="rounded-[24px] border border-[var(--pp-border)] bg-white p-4 shadow-[0_8px_18px_rgba(15,23,42,0.04)]">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--pp-shell-soft-copy)]">{subnav.eyebrow || 'Section'}</p>
          <h2 className="mt-2 text-lg font-semibold tracking-[-0.03em] text-[var(--pp-text)]">{subnav.title}</h2>
          {subnav.description ? <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--pp-muted)]">{subnav.description}</p> : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill status={autonomousConfig?.autonomous_mode ? 'success' : 'default'} label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'} />
          <StatusPill status={user ? 'success' : 'warning'} label={user?.email ? 'Signed in' : 'Not signed in'} />
        </div>
      </div>
      {subnav.sections?.length ? (
      <div className="mt-4 flex flex-wrap gap-2">
          {subnav.sections.flatMap((section) =>
            section.items.map((item) => (
              <button
                key={item.key || item.label}
                type="button"
                onClick={item.onClick}
                className={[
                  'rounded-full border px-3 py-2 text-sm font-medium transition',
                  item.active
                    ? 'border-sky-200 bg-sky-50 text-[var(--pp-primary)]'
                    : 'border-[var(--pp-border)] bg-[var(--pp-shell-hover)] text-[var(--pp-muted)] hover:border-[#b8c6dd] hover:bg-white hover:text-[var(--pp-text)]',
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
      <div className="grid min-h-screen md:grid-cols-[288px_minmax(0,1fr)]">
        <aside className="hidden border-r border-[var(--pp-border)] bg-white md:block">
          <div className="sticky top-0 h-screen overflow-y-auto px-4 py-5">{renderNav()}</div>
        </aside>

        <div className="min-w-0">
          <header className="sticky top-0 z-30 border-b border-[var(--pp-border)] bg-white/90 backdrop-blur-xl">
            <div className="mx-auto flex w-full max-w-[1440px] items-center gap-3 px-4 py-3 md:px-6">
              <Button variant="ghost" size="sm" className="md:hidden gap-2 px-3" onClick={() => setMobileMenuOpen(true)} aria-label="Open navigation">
                <Menu size={18} />
                Menu
              </Button>

              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-3">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--pp-shell-soft-copy)]">PosterPro</p>
                  <span className="hidden h-1 w-1 rounded-full bg-[var(--pp-shell-soft-copy)] sm:inline-flex" />
                  <h1 className="truncate text-[15px] font-semibold tracking-[-0.02em] text-[var(--pp-text)]">{title}</h1>
                </div>
                <p className="hidden text-xs text-[var(--pp-muted)] sm:block">One workspace for intake, listings, publishing, and sold-item tracking.</p>
              </div>

              <form onSubmit={submitSearch} className="relative hidden lg:block">
                <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--pp-shell-soft-copy)]" size={16} />
                <Input
                  aria-label="Global search"
                  value={searchValue}
                  onChange={(event) => setSearchValue(event.target.value)}
                  placeholder="Search listings"
                  className="w-[260px] rounded-2xl border-[var(--pp-border)] bg-white pl-9"
                />
              </form>

              <Button href="/intake" variant="secondary" size="sm" className="hidden sm:inline-flex">
                Quick import
              </Button>

              <button
                type="button"
                onClick={onToggleAutonomous}
                className="hidden items-center gap-2 rounded-2xl border border-[var(--pp-border)] bg-white px-3 py-2 text-xs font-medium text-[var(--pp-text)] md:inline-flex"
                aria-label="Toggle automation mode"
              >
                <Bot size={14} className="text-[var(--pp-primary)]" />
                <StatusPill status={autonomousConfig?.autonomous_mode ? 'success' : 'default'} label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'} />
              </button>
            </div>
          </header>

          <main className="mx-auto w-full max-w-[1440px] px-4 py-6 pb-24 md:px-6">
            <div className="space-y-5">
              {sectionBadge}
              <div className={`mx-auto w-full ${contentWidthClass} min-w-0 space-y-5 ${contentClassName}`}>{children}</div>
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
