import Link from 'next/link';
import { useRouter } from 'next/router';
import { useMemo, useState } from 'react';
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
      label: 'Command Center',
      items: [
        { href: '/app', label: 'Dashboard', icon: LayoutDashboard },
        { href: '/intake', label: 'Intake', icon: FolderInput },
        { href: '/listings', label: 'Listings', icon: ListChecks },
        { href: '/inventory', label: 'Inventory', icon: Package },
      ],
    },
    {
      label: 'Commerce',
      items: [
        { href: '/publishing', label: 'Publishing', icon: Rocket },
        { href: '/sales', label: 'Sales', icon: ShoppingCart },
        { href: '/offers', label: 'Offers', icon: Store },
      ],
    },
    {
      label: 'Growth / Admin',
      items: [
        { href: '/analytics', label: 'Analytics', icon: BarChart3 },
        { href: '/settings?tab=marketplaces', label: 'Integrations', icon: Wrench },
        { href: '/settings', label: 'Settings', icon: Settings2 },
        { href: '/jobs', label: 'Jobs', icon: Briefcase },
        ...(user?.can_access_vine_import ? [{ href: '/imports/vine', label: 'Vine Import', icon: ShieldCheck }] : []),
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
    contentWidth === 'narrow' ? 'max-w-[940px]' : contentWidth === 'wide' ? 'max-w-[1320px]' : 'max-w-[1160px]';

  const submitSearch = (event) => {
    event.preventDefault();
    const value = searchValue.trim();
    router.push(value ? `/listings?q=${encodeURIComponent(value)}` : '/listings');
  };

  const subnavRail = useMemo(() => {
    if (!subnav) return null;
    return (
      <aside className="hidden lg:block">
        <div className="sticky top-[92px] space-y-3">
          <div className="pp-card p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--pp-muted)]">{subnav.eyebrow || 'Page menu'}</p>
            <p className="mt-2 text-base font-semibold text-[var(--pp-text)]">{subnav.title}</p>
            {subnav.description ? <p className="mt-1 text-sm text-[var(--pp-muted)]">{subnav.description}</p> : null}
          </div>
          <div className="pp-card p-3">
            <div className="space-y-4">
              {subnav.sections?.map((section) => (
                <div key={section.label}>
                  <p className="mb-2 px-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--pp-shell-soft-copy)]">{section.label}</p>
                  <div className="space-y-1.5">
                    {section.items.map((item) => (
                      <button
                        key={item.key || item.label}
                        type="button"
                        onClick={item.onClick}
                        className={`flex w-full items-start justify-between rounded-lg border px-3 py-2.5 text-left transition ${
                          item.active ? 'border-[var(--pp-primary-soft)] bg-[var(--pp-shell-active)]' : 'border-transparent bg-transparent hover:border-[var(--pp-border)] hover:bg-[var(--pp-shell-hover)]'
                        }`}
                      >
                        <span>
                          <span className="block text-sm font-medium text-[var(--pp-text)]">{item.label}</span>
                          {item.description ? <span className="mt-0.5 block text-xs text-[var(--pp-muted)]">{item.description}</span> : null}
                        </span>
                        {item.badge !== undefined ? (
                          <span className="rounded-md bg-[var(--pp-surface)] px-1.5 py-0.5 text-xs font-semibold text-[var(--pp-muted)]">{item.badge}</span>
                        ) : null}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </aside>
    );
  }, [subnav]);

  return (
    <div className="posterpro-app-shell min-h-screen text-[var(--pp-text)]">
      <div className="grid min-h-screen md:grid-cols-[248px_minmax(0,1fr)]">
        <aside className="hidden border-r border-[var(--pp-border)] bg-[var(--pp-shell-sidebar)]/95 md:block">
          <div className="sticky top-0 h-screen overflow-y-auto px-4 py-5">
            <Link href="/app" className="block rounded-xl border border-[var(--pp-border)] bg-[var(--pp-shell-hover)] px-3 py-3">
              <p className="text-[15px] font-semibold text-[var(--pp-shell-title)]">PosterPro</p>
              <p className="text-xs text-[var(--pp-shell-soft-copy)]">Reseller command center</p>
            </Link>

            <nav className="mt-6 space-y-6">
              {navGroups.map((group) => (
                <div key={group.label}>
                  <p className="mb-2 px-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--pp-shell-soft-copy)]">{group.label}</p>
                  <div className="space-y-1.5">
                    {group.items.map((item) => {
                      const Icon = item.icon;
                      const selected = isSelected(item.href);
                      return (
                        <Link
                          key={item.href}
                          href={item.href}
                          className={`pp-shell-nav-item flex items-center gap-3 rounded-lg border px-3 py-2.5 text-sm font-medium transition ${
                            selected
                              ? 'border-[var(--pp-primary-soft)] bg-[var(--pp-shell-active)] text-[var(--pp-primary)] shadow-[var(--pp-card-shadow)]'
                              : 'border-transparent text-[var(--pp-shell-copy)] hover:border-[var(--pp-border)] hover:bg-[var(--pp-shell-hover)] hover:text-[var(--pp-shell-title)]'
                          }`}
                        >
                          <Icon size={16} />
                          <span>{item.label}</span>
                        </Link>
                      );
                    })}
                  </div>
                </div>
              ))}
            </nav>
          </div>
        </aside>

        <div className="min-w-0">
          <header className="sticky top-0 z-30 border-b border-[var(--pp-border)] bg-[var(--pp-shell-header)]/90 backdrop-blur">
            <div className="mx-auto flex h-[68px] w-full max-w-[1400px] items-center gap-2 px-4 md:gap-3 md:px-6">
              <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobileMenuOpen(true)} aria-label="Open navigation">
                <Menu size={18} />
              </Button>

              <div className="min-w-0 flex-1">
                <h1 className="truncate text-base font-semibold text-[var(--pp-shell-title)]">{title}</h1>
              </div>

              <form onSubmit={submitSearch} className="relative hidden md:block">
                <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--pp-shell-soft-copy)]" size={16} />
                <Input
                  aria-label="Global search"
                  value={searchValue}
                  onChange={(event) => setSearchValue(event.target.value)}
                  placeholder="Search listings"
                  className="w-[220px] pl-9"
                />
              </form>

              <Link href="/intake" className="hidden sm:block">
                <Button variant="secondary" size="sm">Quick import</Button>
              </Link>

              <button
                type="button"
                onClick={onToggleAutonomous}
                className="hidden items-center gap-2 rounded-full border border-[var(--pp-border)] bg-[var(--pp-surface)] px-3 py-1.5 text-xs font-medium text-[var(--pp-shell-copy)] md:inline-flex"
                aria-label="Toggle automation mode"
              >
                <Bot size={14} className="text-[var(--pp-primary)]" />
                <StatusPill
                  status={autonomousConfig?.autonomous_mode ? 'success' : 'default'}
                  label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'}
                />
              </button>

              <div className="hidden items-center gap-2 rounded-lg border border-[var(--pp-border)] px-2.5 py-2 md:flex">
                <span className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-[var(--pp-surface-strong)] text-[var(--pp-muted)]">
                  <User size={14} />
                </span>
                <span className="max-w-[160px] truncate text-sm font-medium text-[var(--pp-shell-copy)]">{user?.full_name || user?.email || 'Account'}</span>
              </div>

              <Button
                variant="ghost"
                size="sm"
                className="shrink-0"
                onClick={async () => {
                  await logout();
                  window.location.href = '/login';
                }}
              >
                Sign out
              </Button>
            </div>
          </header>

          <main className="mx-auto w-full max-w-[1400px] px-4 py-6 pb-24 md:px-6">
            {subnav ? (
              <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
                {subnavRail}
                <div className={`min-w-0 ${contentWidthClass} space-y-5 ${contentClassName}`}>{children}</div>
              </div>
            ) : (
              <div className={`${contentWidthClass} space-y-5 ${contentClassName}`}>{children}</div>
            )}
          </main>
        </div>
      </div>

      <Drawer
        open={mobileMenuOpen}
        onClose={() => setMobileMenuOpen(false)}
        title="Navigation"
        description="Move through command center, commerce, and admin pages."
        widthClassName="max-w-[420px]"
      >
        <div className="space-y-5">
          {navGroups.map((group) => (
            <div key={group.label}>
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-[var(--pp-shell-soft-copy)]">{group.label}</p>
              <div className="space-y-1.5">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const selected = isSelected(item.href);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={() => setMobileMenuOpen(false)}
                      className={`flex items-center gap-3 rounded-lg border px-3 py-3 text-sm font-medium ${
                        selected
                          ? 'border-[var(--pp-primary-soft)] bg-[var(--pp-shell-active)] text-[var(--pp-primary)]'
                          : 'border-[var(--pp-border)] bg-[var(--pp-surface)] text-[var(--pp-shell-copy)]'
                      }`}
                    >
                      <Icon size={16} />
                      {item.label}
                    </Link>
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
