import Link from 'next/link';
import { BarChart3, Boxes, Bot, LayoutDashboard, Moon, Package, Search, Send, Sun, User } from 'lucide-react';

import Badge from '../ui/badge';
import Button from '../ui/button';
import Input from '../ui/input';

const NAV_ITEMS = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/listings', label: 'Listings', icon: Boxes },
  { href: '/inventory', label: 'Inventory', icon: Package },
  { href: '/published', label: 'Published', icon: Send },
  { href: '/analytics', label: 'Analytics', icon: BarChart3, disabled: true },
];

export default function AppShell({
  active = '/',
  autonomousConfig,
  onToggleAutonomous,
  theme,
  onToggleTheme,
  children,
}) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto flex w-full max-w-[1400px] gap-5 px-3 py-4 md:px-5">
        <aside className="hidden w-72 shrink-0 rounded-3xl border border-border/70 bg-card p-5 shadow-soft lg:block">
          <div className="mb-8">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-primary">PosterPro</p>
            <h1 className="mt-2 text-2xl font-bold">Reseller Command</h1>
            <p className="mt-2 text-sm text-muted-foreground">Everything you need to list, publish, and grow.</p>
          </div>
          <nav className="space-y-2">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const selected = item.href === active;
              return (
                <Link
                  key={item.href}
                  href={item.disabled ? '#' : item.href}
                  className={`flex items-center gap-3 rounded-2xl px-3 py-3 text-sm font-semibold transition ${
                    selected ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                  } ${item.disabled ? 'pointer-events-none opacity-50' : ''}`}
                  title={item.disabled ? 'Analytics page is coming soon.' : `Open ${item.label}`}
                >
                  <Icon size={18} />
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </aside>

        <div className="flex-1">
          <header className="mb-5 rounded-3xl border border-border/70 bg-card/95 p-4 shadow-soft backdrop-blur">
            <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
              <div className="flex flex-1 items-center gap-3">
                <div className="relative w-full max-w-xl" data-tour="search-bar" title="Search listings, IDs, and marketplaces from one place.">
                  <Search className="pointer-events-none absolute left-3 top-3.5 text-muted-foreground" size={18} />
                  <Input placeholder="Search listings, IDs, or marketplaces..." className="pl-10" />
                </div>
                <Button variant="outline" size="icon" onClick={onToggleTheme} title="Switch between light and dark mode.">
                  {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
                </Button>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <Badge
                  tone={autonomousConfig?.autonomous_mode ? 'success' : 'danger'}
                  className="text-sm px-4 py-2"
                  data-tour="autonomous-toggle"
                  title="Autonomous mode lets PosterPro auto-publish when listings are ready."
                >
                  <Bot size={14} className="mr-2" />
                  Autonomous Mode: {autonomousConfig?.autonomous_mode ? 'ON' : 'OFF'}
                  {autonomousConfig?.autonomous_dry_run ? ' (Dry Run)' : ''}
                </Badge>
                <Button onClick={onToggleAutonomous} className="min-w-40" title="Turn autonomous publishing on or off.">
                  Toggle Autonomous
                </Button>
                <Button variant="secondary" size="icon" title="Open your profile and account settings.">
                  <User size={18} />
                </Button>
              </div>
            </div>
          </header>
          {children}
        </div>
      </div>
    </div>
  );
}
