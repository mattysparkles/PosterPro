import Link from 'next/link';
import {
  BarChart3,
  Bot,
  Boxes,
  LayoutDashboard,
  Moon,
  Package,
  Send,
  Settings2,
  Sun,
  User,
  Wallet,
} from 'lucide-react';

import { useAuth } from '../../contexts/AuthContext';
import Badge from '../ui/badge';
import Button from '../ui/button';

const NAV_ITEMS = [
  { href: '/app', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/inventory', label: 'Inventory', icon: Package },
  { href: '/listings', label: 'Drafts', icon: Boxes },
  { href: '/published', label: 'Publishing', icon: Send },
  { href: '/sales', label: 'Sales', icon: Wallet },
  { href: '/analytics', label: 'Analytics', icon: BarChart3 },
  { href: '/settings', label: 'Setup', icon: Settings2 },
];

export default function AppShell({
  active = '/',
  autonomousConfig,
  onToggleAutonomous,
  theme,
  onToggleTheme,
  children,
}) {
  const { user, logout } = useAuth();

  return (
    <div className="posterpro-app-shell text-[#111827]">
      <div className="mx-auto grid min-h-screen max-w-[1280px] lg:grid-cols-[250px_minmax(0,1fr)]">
        <aside className="hidden min-h-screen border-r border-[#e5e7eb] bg-[linear-gradient(180deg,#f5f8fd_0%,#eef3f9_100%)] lg:block">
          <div className="sticky top-0 p-4">
            <div className="rounded-[20px] bg-[#0f172a] p-4 text-white">
              <div className="flex items-center gap-3">
                <div className="inline-flex h-9 w-9 items-center justify-center rounded-[14px] bg-[linear-gradient(180deg,#3b82f6_0%,#1d4ed8_100%)] font-extrabold">
                  P
                </div>
                <div>
                  <strong className="block text-sm font-semibold">PosterPro</strong>
                  <span className="block text-xs text-white/70">Workspace</span>
                </div>
              </div>
            </div>

            <nav className="mt-4 space-y-1">
              {NAV_ITEMS.map((item) => {
                const Icon = item.icon;
                const selected = item.href === active;
                return (
                  <Link
                    key={item.label}
                    href={item.href}
                    className={`flex h-11 items-center gap-3 rounded-[16px] px-3 text-sm font-semibold transition ${
                      selected
                        ? 'bg-white text-[#111827] shadow-[0_14px_30px_rgba(15,23,42,0.08)]'
                        : 'text-[#667085] hover:bg-white/75 hover:text-[#111827]'
                    }`}
                  >
                    <Icon size={18} className={selected ? 'text-[#111827]' : 'text-[#667085]'} />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </nav>
          </div>
        </aside>

        <div className="min-w-0">
          <header className="sticky top-0 z-30 border-b border-[#e5e7eb] bg-white/95 backdrop-blur">
            <div className="mx-auto flex h-[72px] max-w-[1180px] items-center gap-3 px-4 md:px-6">
              <Link href="/" className="text-sm font-extrabold tracking-[-0.04em] text-[#111827] lg:hidden">
                PosterPro
              </Link>

              <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
                <Badge tone={autonomousConfig?.autonomous_mode ? 'success' : 'default'} className="hidden sm:inline-flex">
                  <Bot size={14} className="mr-2" />
                  {autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'}
                </Badge>
                <Button onClick={onToggleAutonomous} variant="outline" size="sm" className="inline-flex">
                  <Bot size={14} />
                  <span className="hidden xl:inline">{autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'}</span>
                </Button>
                <div className="hidden items-center gap-2 rounded-full border border-[#e5e7eb] bg-[#f8fafc] px-3 py-1.5 xl:flex">
                  <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-white text-[#111827]">
                    <User size={15} />
                  </span>
                  <span className="max-w-[160px] truncate text-sm font-medium text-[#475467]">
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
                <Button variant="outline" size="icon" onClick={onToggleTheme} title="Switch theme">
                  {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
                </Button>
              </div>
            </div>
          </header>

          <main className="mx-auto max-w-[1180px] space-y-5 px-4 py-6 pb-32 md:px-6 xl:pb-8">{children}</main>
        </div>
      </div>

      <nav className="fixed bottom-0 left-0 right-0 z-40 border-t border-[#e5e7eb] bg-white/98 p-2 backdrop-blur lg:hidden">
        <div className="mx-auto grid max-w-3xl grid-cols-4 gap-1 sm:grid-cols-7">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const selected = item.href === active;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex flex-col items-center justify-center gap-1 rounded-[16px] px-2 py-2 text-[11px] font-semibold ${
                  selected ? 'bg-[#eff6ff] text-[#2563eb]' : 'text-[#667085]'
                }`}
              >
                <Icon size={16} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
