/* eslint-disable @next/next/no-html-link-for-pages */
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  BarChart3,
  Briefcase,
  Bell,
  ChevronDown,
  FolderInput,
  LayoutDashboard,
  ListChecks,
  Package,
  Rocket,
  Search,
  ShieldCheck,
  Settings2,
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

function buildNavGroups(user) {
  return [
    {
      label: 'Workspace',
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
      items: [
        { href: '/publishing', label: 'Publishing', icon: Rocket },
        { href: '/posterpro/storefront', label: 'Storefront', icon: Store },
        { href: '/sales', label: 'Sales', icon: ShoppingCart },
        { href: '/offers', label: 'Offers', icon: Store },
      ],
    },
    {
      label: 'System',
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

function NavGroup({ title, items, isSelected, onNavigate, collapsed = false }) {
  return (
    <details className="group pp-sidebar-panel p-2.5" open={!collapsed}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-[16px] px-2 py-1.5 outline-none">
        <div className="min-w-0">
          {!collapsed ? <p className="pp-sidebar-label">{title}</p> : null}
        </div>
        <ChevronDown size={16} className="shrink-0 text-[var(--pp-shell-soft-copy)] transition-transform duration-200 group-open:rotate-180" />
      </summary>
      <div className="mt-2 space-y-1">
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
                'pp-sidebar-link flex items-center gap-3 rounded-xl px-3 py-2 transition',
                collapsed ? 'justify-center' : '',
                selected ? 'is-active' : '',
              ].join(' ')}
            >
              <span
                className="pp-sidebar-icon inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
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
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [processNotifications, setProcessNotifications] = useState([]);
  const [notificationUnreadCount, setNotificationUnreadCount] = useState(0);
  const [notificationTotal, setNotificationTotal] = useState(0);
  const [notificationOffset, setNotificationOffset] = useState(0);
  const [notificationLoading, setNotificationLoading] = useState(false);
  const [notificationFilter, setNotificationFilter] = useState('all');
  const notificationsSeenRef = useRef(new Set());
  const notificationsInitializedRef = useRef(false);
  const navGroups = buildNavGroups(user);
  const activePath = active || router.pathname;
  const activeHref = router.asPath || activePath;

  const normalizeHref = (href) => (href || '').replace(/#.*$/, '');
  const activeNavHref = navGroups
    .flatMap((group) => (Array.isArray(group?.items) ? group.items : []))
    .map((item) => item.href)
    .filter((href) => {
      const candidate = normalizeHref(href);
      const current = normalizeHref(activeHref);
      const [candidatePath, candidateQuery] = candidate.split('?');
      const [currentPath, currentQuery] = current.split('?');
      if (candidateQuery && candidateQuery !== currentQuery) return false;
      return currentPath === candidatePath || currentPath.startsWith(`${candidatePath}/`);
    })
    .sort((a, b) => normalizeHref(b).length - normalizeHref(a).length)[0] || normalizeHref(activeHref);
  const isSelected = (href) => {
    const normalizedHref = normalizeHref(href);
    return normalizedHref === activeNavHref || normalizeHref(activePath) === normalizedHref;
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
  const sidebarWidthClass = 'sm:w-[272px]';
  const contentPaddingClass = 'sm:pl-[272px]';

  const submitSearch = (event) => {
    event.preventDefault();
    const value = searchValue.trim();
    router.push(value ? `/listings?q=${encodeURIComponent(value)}` : '/listings');
  };

  const loadProcessNotifications = useCallback(async ({ offset = 0, append = false } = {}) => {
    if (!user?.id) return;
    if (append) setNotificationLoading(true);
    try {
      const payload = await fetchProcessNotifications({ limit: 20, offset, unreadOnly: false });
      const rows = Array.isArray(payload?.notifications) ? payload.notifications : [];
      const unreadCount = Number(payload?.unread_count ?? rows.filter((row) => !row.read_at).length);
      setProcessNotifications((current) => append
        ? [...current, ...rows.filter((row) => !current.some((existing) => existing.id === row.id))]
        : current.length > 20
          ? [...rows, ...current.filter((existing) => !rows.some((row) => row.id === existing.id))]
          : rows);
      setNotificationUnreadCount(unreadCount);
      setNotificationTotal(Number(payload?.total || 0));
      setNotificationOffset((current) => Math.max(current, offset + rows.length));

      const nextIds = new Set(rows.map((row) => row.id));
      if (!notificationsInitializedRef.current) {
        notificationsSeenRef.current = nextIds;
        notificationsInitializedRef.current = true;
        return;
      }
      if (append) return;

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
    } finally {
      if (append) setNotificationLoading(false);
    }
  }, [user?.id]);

  useEffect(() => {
    loadProcessNotifications();
    const interval = window.setInterval(() => loadProcessNotifications(), 60000);
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

  const filteredNotifications = processNotifications.filter((notification) => {
    if (notificationFilter === 'unread') return !notification.read_at;
    if (notificationFilter === 'errors') return /failed|error|blocked|urgent/i.test(`${notification.notification_type || ''} ${notification.title || ''} ${notification.message || ''}`);
    return true;
  });

  const renderNav = (onNavigate) => (
    <div className="space-y-4">
      <section className="pp-sidebar-brand-panel rounded-2xl p-3 text-[var(--pp-shell-copy)]">
        <Link href="/app" aria-label="PosterPro home" className="block rounded-xl px-2 py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white">
          <span className="block font-[var(--pp-heading-font)] text-[1.65rem] font-bold tracking-[-0.055em] text-white">PosterPro</span>
        </Link>
        <div className="mt-2 flex items-center gap-2 border-t border-white/10 px-2 pt-3">
          <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/10 text-white"><User size={15} aria-hidden="true" /></span>
          <div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold text-white">{user?.full_name || 'My account'}</p><p className="truncate text-xs text-[var(--pp-shell-soft-copy)]">{user?.email || ''}</p></div>
          <Link href="/settings?tab=profile" className="inline-flex min-h-9 shrink-0 items-center rounded-lg border border-white/20 px-2 text-sm font-semibold text-white hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white">My Account</Link>
        </div>
        {user?.is_admin && onToggleAutonomous ? <div className="mt-3 flex items-center justify-between rounded-xl border border-white/10 bg-white/5 px-2.5 py-2">
          <span className="flex items-center gap-1.5 text-sm font-medium text-white">Automation <button type="button" title="ON lets PosterPro automatically start publishing after supported intake workflows. OFF pauses automatic publishing; it does not stop intake, remove live listings, or disconnect marketplace accounts." aria-label="About automation" className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-white/40 text-xs font-bold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white">i</button></span>
          <button type="button" role="switch" aria-label="Automation" aria-checked={Boolean(autonomousConfig?.autonomous_mode)} onClick={onToggleAutonomous} className={`inline-flex h-8 min-w-[56px] items-center justify-center rounded-full px-2 text-xs font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white ${autonomousConfig?.autonomous_mode ? 'bg-emerald-400 text-[#102a1f]' : 'bg-slate-200 text-slate-900'}`}><span>{autonomousConfig?.autonomous_mode ? 'ON' : 'OFF'}</span></button>
        </div> : null}
      </section>

      {navGroups.filter(Boolean).map((group) => (
        <NavGroup key={group.label} title={group.label} items={group.items} isSelected={isSelected} onNavigate={onNavigate} />
      ))}

      <section className="px-2 pb-2 text-[var(--pp-shell-copy)]">
        <Button
          variant="secondary"
          size="sm"
          className="mt-3 w-full justify-center border-white/10 bg-white/10 text-white hover:bg-white hover:text-[var(--pp-primary)]"
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
    <div className="pp-surface-panel p-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="pp-topbar-kicker">{subnav.eyebrow || 'Section'}</p>
          <h2 className="pp-topbar-title mt-2 text-[1.4rem]">{subnav.title}</h2>
          {subnav.description ? <p className="pp-topbar-subtitle mt-2 max-w-3xl text-sm leading-6">{subnav.description}</p> : null}
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
            <div className="mx-auto flex w-full max-w-[1520px] items-center gap-3 py-3 pl-16 pr-4 sm:px-6 md:px-6">
              <Button
                variant="secondary"
                size="sm"
                className="fixed left-4 top-4 z-[120] gap-2 px-3 shadow-none sm:hidden"
                onClick={() => setMobileMenuOpen(true)}
                aria-label="Open navigation menu"
                title="Open navigation menu"
              >
                <span aria-hidden="true">☰</span><span className="sr-only">Open navigation menu</span>
              </Button>

              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-center gap-3">
                  <p className="pp-topbar-kicker">
                  {activeNav?.group?.label || 'PosterPro'}
                  </p>
                  <span className="hidden h-1.5 w-1.5 rounded-full bg-[#c3ac89] sm:inline-flex" />
                  <h1 className="pp-topbar-title truncate text-[1.02rem]">{title}</h1>
                </div>
                <p className="pp-topbar-subtitle hidden text-xs sm:block">{activeNav?.item?.label && activeNav.item.label !== title ? activeNav.item.label : 'Your reseller workspace'}</p>
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

              <div className="relative ml-auto">
                <Button
                  variant="secondary"
                  size="icon"
                  className="relative rounded-xl border-[var(--pp-border)] bg-white text-[var(--pp-text)] shadow-sm hover:bg-slate-50"
                  onClick={() => setNotificationsOpen((current) => !current)}
                  aria-label={`Open notifications. ${notificationUnreadCount.toLocaleString()} unread.`}
                  aria-expanded={notificationsOpen}
                  title="Notifications"
                >
                  <Bell size={19} aria-hidden="true" />
                  {notificationUnreadCount ? <span className="absolute -right-2 -top-2 inline-flex h-5 min-w-5 items-center justify-center rounded-full border-2 border-white bg-[#b42318] px-1 text-[10px] font-bold leading-none text-white">{notificationUnreadCount > 99 ? '99+' : notificationUnreadCount}</span> : null}
                </Button>
                {notificationsOpen && typeof document !== 'undefined' ? createPortal((
                  <div role="dialog" aria-label="Notifications" style={{ position: 'fixed', top: 64, right: 20, left: 'auto', width: 'min(440px, calc(100vw - 24px))', maxHeight: 'min(78vh, 680px)', zIndex: 9999, backgroundColor: '#ffffff', opacity: 1 }} className="overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-[0_24px_60px_rgba(16,24,40,0.24)]">
                    <div className="flex items-start justify-between gap-3 border-b border-slate-200 bg-white px-4 py-4">
                      <div><p className="text-base font-semibold text-slate-950">Notifications</p><p className="mt-1 text-sm text-slate-600">Unread {notificationUnreadCount.toLocaleString()}</p></div>
                      <button type="button" onClick={handleMarkAllNotificationsRead} disabled={!notificationUnreadCount} className="rounded-lg px-2 py-1.5 text-sm font-semibold text-blue-800 hover:bg-blue-50 disabled:cursor-not-allowed disabled:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600">Mark all read</button>
                    </div>
                    <div className="flex items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-4 py-2">
                      <div className="flex gap-1" role="group" aria-label="Filter notifications">{[['all', 'All'], ['unread', 'Unread'], ['errors', 'Errors']].map(([value, label]) => <button key={value} type="button" aria-pressed={notificationFilter === value} onClick={() => setNotificationFilter(value)} className={`min-h-9 rounded-lg px-3 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 ${notificationFilter === value ? 'bg-blue-800 text-white' : 'bg-transparent text-slate-700 hover:bg-white'}`}>{label}</button>)}</div>
                      <Link href="/notices" onClick={() => setNotificationsOpen(false)} className="rounded-lg px-2 py-1.5 text-sm font-semibold text-blue-800 hover:bg-blue-50">View all notifications</Link>
                    </div>
                    <div className="max-h-[min(52vh,440px)] overflow-y-auto bg-white p-3">
                      {filteredNotifications.length ? (
                        filteredNotifications.map((notification) => (
                          <button
                            key={notification.id}
                            type="button"
                            className={`block w-full rounded-[14px] border px-3 py-3 text-left transition hover:bg-[#f9fafb] ${
                              notification.read_at ? 'border-slate-200 bg-white' : 'border-blue-200 bg-blue-50'
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
                                <p className="mt-1 text-sm leading-5 text-slate-700">{notification.message || 'Workflow update'}</p>
                                <p className="mt-1 text-xs text-slate-500">
                                  {notification.created_at ? new Date(notification.created_at).toLocaleString() : 'Just now'}
                                </p>
                              </div>
                            </div>
                          </button>
                        ))
                      ) : (
                        <div className="rounded-xl border border-dashed border-slate-300 px-3 py-8 text-center text-sm text-slate-600">
                          No notifications in this view.
                        </div>
                      )}
                    </div>
                    <div className="flex items-center justify-between gap-3 border-t border-slate-200 bg-white px-4 py-3"><span className="text-xs text-slate-600">Showing {processNotifications.length} of {notificationTotal.toLocaleString()}</span>{processNotifications.length < notificationTotal ? <button type="button" disabled={notificationLoading} onClick={() => void loadProcessNotifications({ offset: notificationOffset, append: true })} className="min-h-10 rounded-lg bg-blue-800 px-4 text-sm font-semibold text-white hover:bg-blue-900 disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600">{notificationLoading ? 'Loading…' : 'Load more'}</button> : null}</div>
                  </div>
                ), document.body) : null}
              </div>
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
