/* eslint-disable @next/next/no-html-link-for-pages */
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  BarChart3,
  Bot,
  Briefcase,
  Bell,
  ChevronDown,
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
  Puzzle,
  QrCode,
  Film,
} from 'lucide-react';
import toast from 'react-hot-toast';

import { useAuth } from '../../contexts/AuthContext';
import { fetchProcessNotifications, markAllProcessNotificationsRead, markProcessNotificationRead } from '../../lib/api';
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
        { href: '/intake/slate', label: 'Slate', icon: QrCode },
        { href: '/intake/timeline', label: 'Photo Timeline', icon: Film },
        { href: '/listings', label: 'Listings', icon: ListChecks },
        { href: '/inventory', label: 'Inventory', icon: Package },
      ],
    },
    {
      label: 'Selling',
      description: 'Publishing, offers, and sales.',
      items: [
        { href: '/publishing', label: 'Publishing', icon: Rocket },
        { href: '/posterpro/storefront', label: 'Storefront', icon: Store },
        { href: '/sales', label: 'Sales', icon: ShoppingCart },
        { href: '/offers', label: 'Offers', icon: Store },
      ],
    },
    {
      label: 'System',
      description: 'Settings, jobs, and reporting.',
      items: [
        { href: '/analytics', label: 'Analytics', icon: BarChart3 },
        { href: '/bridge-desktop', label: 'Marketplace Extension', icon: Puzzle },
        { href: '/settings?tab=marketplaces', label: 'Marketplaces', icon: ShoppingCart },
        { href: '/settings/ebay', label: 'Marketplace setup', icon: Wrench },
        { href: '/settings', label: 'Settings', icon: Settings2 },
        { href: '/jobs', label: 'Jobs', icon: Briefcase },
        { href: '/notices', label: 'Notices', icon: Bell },
        ...(user?.can_access_vine_import ? [{ href: '/imports/vine', label: 'Vine Import', icon: ShieldCheck }] : []),
      ],
      },
    {
      label: 'Account',
      description: 'Profile and communication preferences.',
      items: [
        { href: '/settings?tab=profile', label: 'My account', icon: User },
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
    <details className="group pp-sidebar-panel p-3.5" open={!collapsed}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-[16px] px-2 py-1.5 outline-none">
        <div className="min-w-0">
          {!collapsed ? <p className="pp-sidebar-label">{title}</p> : null}
          {!collapsed && description ? <p className="pt-1 text-xs leading-5 text-[var(--pp-shell-soft-copy)]">{description}</p> : null}
        </div>
        <ChevronDown size={16} className="shrink-0 text-[var(--pp-shell-soft-copy)] transition-transform duration-200 group-open:rotate-180" />
      </summary>
      <div className="mt-3 space-y-2">
        {items.map((item) => {
          const Icon = item.icon;
          const selected = isSelected(item.href);
          return (
            <Link
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
                </span>
              ) : null}
            </Link>
          );
        })}
      </div>
    </details>
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
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [processNotifications, setProcessNotifications] = useState([]);
  const [notificationUnreadCount, setNotificationUnreadCount] = useState(0);
  const notificationsSeenRef = useRef(new Set());
  const notificationsInitializedRef = useRef(false);
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
  const sidebarWidthClass = sidebarCollapsed ? 'sm:w-[104px]' : 'sm:w-[340px]';
  const contentPaddingClass = sidebarCollapsed ? 'sm:pl-[104px]' : 'sm:pl-[340px]';

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

  const handleMenuClick = () => {
    // Desktop uses the persistent left rail; smaller screens use the drawer.
    if (typeof window !== 'undefined' && window.matchMedia('(min-width: 640px)').matches) {
      toggleSidebar();
    } else {
      setMobileMenuOpen(true);
    }
  };

  const submitSearch = (event) => {
    event.preventDefault();
    const value = searchValue.trim();
    router.push(value ? `/listings?q=${encodeURIComponent(value)}` : '/listings');
  };

  const loadProcessNotifications = useCallback(async () => {
    if (!user?.id) return;
    try {
      const payload = await fetchProcessNotifications({ limit: 8, unreadOnly: false });
      const rows = Array.isArray(payload?.notifications) ? payload.notifications : [];
      const unreadCount = Number(payload?.unread_count || rows.filter((row) => !row.read_at).length || 0);
      setProcessNotifications(rows);
      setNotificationUnreadCount(unreadCount);

      const nextIds = new Set(rows.map((row) => row.id));
      if (!notificationsInitializedRef.current) {
        notificationsSeenRef.current = nextIds;
        notificationsInitializedRef.current = true;
        return;
      }

      const newRows = rows.filter((row) => !notificationsSeenRef.current.has(row.id));
      const shouldSuppressToast = (row) => {
        const normalize = (value) => String(value || '').toLowerCase().replaceAll('_', ' ').replaceAll('-', ' ');
        const type = normalize(row?.notification_type);
        const title = normalize(row?.title);
        const message = normalize(row?.message);
        const combined = `${title} ${message}`;
        return (
          type.includes('blocked') ||
          type.includes('generic_or_caption_identity') ||
          type.includes('needs_image_identification') ||
          type.includes('insufficient_identity_evidence') ||
          type.includes('caption_identity') ||
          combined.includes('needs attention') ||
          combined.includes('generic or caption identity') ||
          combined.includes('needs image identification') ||
          combined.includes('insufficient identity evidence')
        );
      };
      newRows.reverse().forEach((row) => {
        if (shouldSuppressToast(row)) return;
        const status = String(row?.metadata_json?.status || row?.metadata_json?.stage || '').toLowerCase();
        const isError = ['failed', 'error', 'blocked'].some((token) => status.includes(token)) || /failed|error|blocked/i.test(`${row.title || ''} ${row.message || ''}`);
        const message = row.message || row.title || 'Process update';
        if (isError) {
          toast.error(message);
        } else if (status.includes('complete') || status.includes('completed') || status.includes('success')) {
          toast.success(message);
        } else {
          toast(message);
        }
      });
      notificationsSeenRef.current = nextIds;
    } catch {
      return;
    }
  }, [user?.id]);

  useEffect(() => {
    loadProcessNotifications();
    const interval = window.setInterval(loadProcessNotifications, 15000);
    return () => window.clearInterval(interval);
  }, [loadProcessNotifications]);

  const handleMarkNotificationRead = async (notificationId) => {
    try {
      await markProcessNotificationRead(notificationId);
      await loadProcessNotifications();
    } catch (error) {
      toast.error(error.message || 'Could not mark notification read.');
    }
  };

  const handleMarkAllNotificationsRead = async () => {
    try {
      await markAllProcessNotificationsRead();
      await loadProcessNotifications();
      toast.success('Process notifications cleared.');
    } catch (error) {
      toast.error(error.message || 'Could not clear notifications.');
    }
  };

  const renderNav = (onNavigate) => (
    <div className="space-y-4">
      <section className="pp-sidebar-brand-panel p-5 text-[var(--pp-shell-copy)]">
        <Link href="/app" className="flex items-center gap-3">
          <div className="pp-sidebar-brand-mark flex h-12 w-12 items-center justify-center rounded-[18px] font-[var(--pp-heading-font)] text-xl font-bold text-white">
            PP
          </div>
          {!sidebarCollapsed ? (
          <div className="min-w-0">
            <p className="font-[var(--pp-heading-font)] text-xl font-semibold tracking-[-0.04em] text-white">PosterPro</p>
            <p className="text-sm text-[var(--pp-shell-soft-copy)]">Reseller operations system</p>
          </div>
          ) : null}
        </Link>
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
                  'pp-shell-chip rounded-full border px-3 py-2 text-sm font-semibold transition',
                  item.active
                    ? 'is-active border-[#bfd4ef] bg-[var(--pp-primary)] text-white'
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
      <aside className={`pp-shell-sidebar-rail pp-shell-sidebar-surface ${sidebarWidthClass}`}>
        <div className="h-full overflow-y-auto px-4 py-5">{renderNav()}</div>
      </aside>

      <div className={`min-h-screen min-w-0 ${contentPaddingClass}`}>
        <div className="pp-shell-content-wrap min-w-0">
          <header className="pp-shell-header-surface sticky top-0 z-30 backdrop-blur-xl">
            <div className="mx-auto flex w-full max-w-[1520px] items-center gap-3 px-4 py-3 md:px-6">
              <Button variant="secondary" size="sm" className="inline-flex gap-2 px-3 shadow-none" onClick={handleMenuClick} aria-label="Open navigation menu" title="Open navigation menu">
                <Menu size={18} />
                Menu
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="fixed left-4 top-4 z-[120] gap-2 px-3 shadow-none sm:hidden"
                onClick={() => setMobileMenuOpen(true)}
                aria-label="Open navigation menu"
                title="Open navigation menu"
              >
                <Menu size={18} />
                Menu
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="hidden gap-2 px-3 shadow-none sm:inline-flex"
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

              <form onSubmit={submitSearch} className="relative hidden lg:block">
                <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--pp-shell-soft-copy)]" size={16} />
                <Input
                  aria-label="Global search"
                  value={searchValue}
                  onChange={(event) => setSearchValue(event.target.value)}
                  placeholder="Search listings"
                  className="w-[280px] rounded-2xl border-[var(--pp-border)] bg-white pl-9"
                />
              </form>

              <Button href="/intake" variant="outline" size="sm" className="hidden lg:inline-flex">
                Quick import
              </Button>
              <Button href="/settings" variant="outline" size="sm" className="inline-flex">
                <Settings2 size={15} />
                Settings
              </Button>

              <div className="relative">
                <Button
                  variant="outline"
                  size="sm"
                  className="inline-flex gap-2"
                  onClick={() => setNotificationsOpen((current) => !current)}
                  aria-label="Open process notifications"
                >
                  <Bell size={15} />
                  {notificationUnreadCount ? <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-[#b42318] px-1.5 py-0.5 text-[11px] font-semibold text-white">{notificationUnreadCount}</span> : null}
                </Button>
                {notificationsOpen ? (
                  <div className="fixed right-3 top-[72px] z-50 mt-2 w-[min(360px,calc(100vw-24px))] max-h-[min(70vh,560px)] overflow-y-auto rounded-[18px] border border-[var(--pp-border)] bg-white p-3 shadow-[0_16px_40px_rgba(16,24,40,0.18)] sm:right-6">
                    <div className="flex items-center justify-between gap-3 border-b border-[var(--pp-border)] pb-2">
                      <div>
                        <p className="text-sm font-semibold text-[var(--pp-text)]">Process notifications</p>
                        <p className="text-xs text-[var(--pp-muted)]">Uploads, jobs, and workflow actions</p>
                      </div>
                      <div className="flex items-center gap-1"><Button size="sm" variant="ghost" onClick={handleMarkAllNotificationsRead} disabled={!notificationUnreadCount}>Mark all read</Button><Button size="sm" variant="ghost" href="/notices" onClick={() => setNotificationsOpen(false)}>View all</Button></div>
                    </div>
                    <div className="max-h-[420px] overflow-y-auto py-2">
                      {processNotifications.length ? (
                        processNotifications.map((notification) => (
                          <button
                            key={notification.id}
                            type="button"
                            className={`block w-full rounded-[14px] border px-3 py-3 text-left transition hover:bg-[#f9fafb] ${
                              notification.read_at ? 'border-transparent' : 'border-[#bfd4ef] bg-[#f8fbff]'
                            }`}
                            onClick={async () => {
                              if (notification.href) {
                                await handleMarkNotificationRead(notification.id);
                                router.push(notification.href);
                              } else {
                                await handleMarkNotificationRead(notification.id);
                              }
                              setNotificationsOpen(false);
                            }}
                          >
                            <div className="flex items-start gap-2">
                              <span className="mt-0.5">
                                {String(notification.notification_type || '').includes('failed') || /failed|error|blocked/i.test(`${notification.title || ''} ${notification.message || ''}`) ? (
                                  <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-[#fee4e2] text-[#b42318]">!</span>
                                ) : (
                                  <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-[#ecfdf3] text-[#027a48]">✓</span>
                                )}
                              </span>
                              <div className="min-w-0 flex-1">
                                <p className="truncate text-sm font-semibold text-[var(--pp-text)]">{notification.title || 'Process update'}</p>
                                <p className="mt-1 text-xs leading-5 text-[var(--pp-muted)]">{notification.message || 'Workflow update'}</p>
                                <p className="mt-1 text-[11px] text-[var(--pp-shell-soft-copy)]">
                                  {notification.created_at ? new Date(notification.created_at).toLocaleString() : 'Just now'}
                                </p>
                              </div>
                            </div>
                          </button>
                        ))
                      ) : (
                        <div className="rounded-[14px] border border-dashed border-[var(--pp-border)] px-3 py-6 text-center text-sm text-[var(--pp-muted)]">
                          No recent process notifications.
                        </div>
                      )}
                    </div>
                  </div>
                ) : null}
              </div>

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

          <main className="mx-auto w-full max-w-[1520px] px-4 py-4 pb-20 md:px-6">
            <div className="space-y-4">
              {sectionBadge}
              <div className={`mx-auto w-full ${contentWidthClass} min-w-0 space-y-4 ${contentClassName}`}>{children}</div>
            </div>
          </main>
        </div>
      </div>

      <Drawer open={mobileMenuOpen} onClose={() => setMobileMenuOpen(false)} side="left" title="Main menu" description="All core areas, marketplace setup, and settings are available here." widthClassName="max-w-[440px]">
        <div className="space-y-4">{renderNav(() => setMobileMenuOpen(false))}</div>
      </Drawer>
    </div>
  );
}
