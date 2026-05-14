import Link from 'next/link';
import { useRouter } from 'next/router';
import { useState } from 'react';
import {
  BarChart3,
  Bot,
  FileText,
  FolderOpen,
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
      label: 'Operations',
      items: [
        { href: '/app', label: 'Dashboard', icon: LayoutDashboard },
        { href: '/intake', label: 'Intake', icon: FolderOpen },
        { href: '/listings', label: 'Listings', icon: FileText },
        { href: '/inventory', label: 'Inventory', icon: Package },
        ...(user?.can_access_vine_import ? [{ href: '/imports/vine', label: 'Vine Import', icon: Upload }] : []),
      ],
    },
    {
      label: 'Marketplaces',
      items: [
        { href: '/publishing', label: 'Publishing', icon: Store },
        { href: '/sales', label: 'Sales', icon: ShoppingCart },
        { href: '/offers', label: 'Offers', icon: Tag },
      ],
    },
    {
      label: 'System',
      items: [
        { href: '/analytics', label: 'Analytics', icon: BarChart3 },
        { href: '/settings', label: 'Settings', icon: Settings2 },
      ],
    },
  ];
}

export default function AppShell({
  active,
  title = 'Dashboard',
  autonomousConfig,
  onToggleAutonomous,
  children,
}) {
  const { user, logout } = useAuth();
  const router = useRouter();
  const [searchValue, setSearchValue] = useState('');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const navGroups = buildNavGroups(user);
  const activePath = active || router.pathname;
  const mobilePrimaryItems = [
    { href: '/app', label: 'Dashboard', icon: LayoutDashboard },
    { href: '/intake', label: 'Intake', icon: FolderOpen },
    { href: '/listings', label: 'Listings', icon: FileText },
    { href: '/publishing', label: 'Publishing', icon: Store },
  ];

  const isSelected = (href) => activePath === href || activePath.startsWith(`${href}/`);

  const submitSearch = (event) => {
    event.preventDefault();
    const value = searchValue.trim();
    router.push(value ? `/listings?q=${encodeURIComponent(value)}` : '/listings');
  };

  return (
    <div className="posterpro-app-shell min-h-screen bg-[#f6f8fb] text-[#101828]">
      <div className="grid min-h-screen lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="hidden min-h-screen border-r border-[#e5e7eb] bg-white lg:block">
          <div className="sticky top-0 p-5">
            <div className="pb-6">
              <Link href="/app" className="block">
                <strong className="block text-lg font-semibold tracking-[-0.02em] text-[#101828]">PosterPro</strong>
                <span className="mt-1 block text-sm text-[#667085]">Reseller OS</span>
              </Link>
            </div>
            <nav className="space-y-6">
              {navGroups.map((group) => (
                <div key={group.label}>
                  <p className="mb-2 px-3 text-xs font-medium uppercase tracking-[0.08em] text-[#667085]">{group.label}</p>
                  <div className="space-y-1">
                    {group.items.map((item) => {
                      const Icon = item.icon;
                      const selected = isSelected(item.href);
                      return (
                        <Link
                          key={item.label}
                          href={item.href}
                          onClick={() => setMobileMenuOpen(false)}
                          className={`flex h-10 items-center gap-3 rounded-[10px] px-3 text-sm font-medium transition-colors ${
                            selected ? 'bg-[#eef4ff] text-[#2563eb]' : 'text-[#475467] hover:bg-[#f9fafb]'
                          }`}
                        >
                          <Icon size={18} className={selected ? 'text-[#2563eb]' : 'text-[#667085]'} />
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
          <header className="sticky top-0 z-30 border-b border-[#e5e7eb] bg-white">
            <div className="mx-auto flex h-16 w-full max-w-[1180px] items-center gap-3 px-4 md:px-6">
              <Link href="/" className="text-sm font-semibold tracking-[-0.02em] text-[#101828] lg:hidden">
                PosterPro
              </Link>

              <div className="min-w-0 flex-1">
                <h1 className="truncate text-base font-semibold text-[#101828]">{title}</h1>
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
                  <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" size={16} />
                  <Input
                    value={searchValue}
                    onChange={(event) => setSearchValue(event.target.value)}
                    placeholder="Search listings"
                    className="w-[220px] pl-9"
                  />
                </form>
                <button
                  type="button"
                  onClick={onToggleAutonomous}
                  className="inline-flex h-9 items-center gap-2 rounded-full border border-[#e5e7eb] bg-white px-3 text-sm font-medium text-[#475467] transition-colors hover:bg-[#f9fafb]"
                >
                  <Bot size={14} className="text-[#2563eb]" />
                  <StatusPill
                    status={autonomousConfig?.autonomous_mode ? 'success' : 'default'}
                    label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'}
                  />
                </button>
                <div className="hidden items-center gap-2 rounded-[10px] border border-[#e5e7eb] bg-white px-3 py-2 lg:flex">
                  <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-[#f9fafb] text-[#101828]">
                    <User size={15} />
                  </span>
                  <span className="max-w-[140px] truncate text-sm font-medium text-[#475467]">
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

          <main className="mx-auto w-full max-w-[1180px] space-y-5 px-4 py-6 pb-24 md:px-6">{children}</main>
        </div>
      </div>

      <nav className="fixed bottom-0 left-0 right-0 z-40 border-t border-[#e5e7eb] bg-white/98 p-2 backdrop-blur lg:hidden">
        <div className="mx-auto grid max-w-3xl grid-cols-5 gap-1">
          {mobilePrimaryItems.map((item) => {
            const Icon = item.icon;
            const selected = isSelected(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex flex-col items-center justify-center gap-1 rounded-[10px] px-2 py-2 text-[11px] font-medium ${
                  selected ? 'bg-[#eef4ff] text-[#2563eb]' : 'text-[#667085]'
                }`}
              >
                <Icon size={16} />
                <span>{item.label}</span>
              </Link>
            );
          })}
          <button
            type="button"
            onClick={() => setMobileMenuOpen(true)}
            className={`flex flex-col items-center justify-center gap-1 rounded-[10px] px-2 py-2 text-[11px] font-medium ${
              isSelected('/settings') || mobileMenuOpen ? 'bg-[#eef4ff] text-[#2563eb]' : 'text-[#667085]'
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
          <div className="rounded-[14px] border border-[#e5e7eb] bg-[#f8fafc] p-4">
            <p className="text-sm font-semibold text-[#101828]">{user?.full_name || user?.email || 'PosterPro account'}</p>
            <p className="mt-1 text-sm text-[#667085]">Signed in workspace operator</p>
          </div>

          {navGroups.map((group) => (
            <div key={group.label}>
              <p className="mb-2 text-xs font-medium uppercase tracking-[0.08em] text-[#667085]">{group.label}</p>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const selected = isSelected(item.href);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={() => setMobileMenuOpen(false)}
                      className={`flex items-center justify-between rounded-[12px] border px-3 py-3 text-sm font-medium ${
                        selected ? 'border-[#bfd2ff] bg-[#eef4ff] text-[#2563eb]' : 'border-[#e5e7eb] bg-white text-[#475467]'
                      }`}
                    >
                      <span className="flex items-center gap-3">
                        <Icon size={17} className={selected ? 'text-[#2563eb]' : 'text-[#667085]'} />
                        {item.label}
                      </span>
                      {selected ? <StatusPill status="success" label="Open" /> : null}
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
