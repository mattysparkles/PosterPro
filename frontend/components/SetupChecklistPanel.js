import Link from 'next/link';
import { CheckCircle2, CircleDashed, Link2, Rocket, Settings2, UserRound } from 'lucide-react';

import Badge from './ui/badge';
import Button from './ui/button';
import { Card, CardDescription, CardTitle } from './ui/card';

function StepRow({ done, icon: Icon, title, description }) {
  return (
    <div className="flex items-start gap-4 rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] p-4">
      <div
        className={`mt-0.5 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl ${
          done ? 'bg-[#ecfdf3] text-[#067647]' : 'bg-[#eff6ff] text-[#2563eb]'
        }`}
      >
        <Icon size={18} />
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-semibold text-slate-950">{title}</p>
          <Badge tone={done ? 'success' : 'info'}>{done ? 'Done' : 'Needs attention'}</Badge>
        </div>
        <p className="mt-1 text-sm leading-6 text-slate-600">{description}</p>
      </div>
    </div>
  );
}

export default function SetupChecklistPanel({ setupSummary }) {
  if (!setupSummary) return null;

  const connectedMarketplaces = setupSummary.marketplace_connections.filter((item) => item.connected);
  const hasConnectedMarketplace = connectedMarketplaces.length > 0;
  const hasServerReadyMarketplace = setupSummary.marketplace_connections.some(
    (item) => item.marketplace === 'ebay' && item.available,
  );
  const completedSteps = [
    setupSummary.account_profile_complete,
    hasServerReadyMarketplace,
    hasConnectedMarketplace,
    setupSummary.total_listings > 0,
  ].filter(Boolean).length;

  return (
    <Card>
      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <div>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="max-w-2xl">
              <p className="text-xs font-bold uppercase tracking-[0.3em] text-[#2563eb]">Onboarding checklist</p>
              <CardTitle className="mt-2 text-2xl tracking-tight">Get this account ready to post cleanly</CardTitle>
              <CardDescription className="mt-3 max-w-xl text-sm leading-7 text-slate-600">
                This is the shortest path from first login to a usable workspace: complete the account profile, confirm marketplace readiness,
                connect credentials where available, and create the first real listing.
              </CardDescription>
            </div>
            <Link href="/settings">
              <Button size="lg" title="Open the Setup Center to finish onboarding steps.">
                <Settings2 size={18} />
                Open setup center
              </Button>
            </Link>
          </div>

          <div className="mt-6 grid gap-4">
            <StepRow
              done={setupSummary.account_profile_complete}
              icon={UserRound}
              title="Complete account profile"
              description={
                setupSummary.account_profile_complete
                  ? 'This account already has an operator or business name attached.'
                  : 'Add your operator or business name so the workspace is clearly assigned and easier to manage.'
              }
            />
            <StepRow
              done={hasServerReadyMarketplace}
              icon={Rocket}
              title="Confirm marketplace readiness"
              description={
                hasServerReadyMarketplace
                  ? 'The server is configured to support at least one real account-level marketplace connection.'
                  : 'Marketplace connection is still blocked until server-level OAuth credentials are configured.'
              }
            />
            <StepRow
              done={hasConnectedMarketplace}
              icon={Link2}
              title="Connect a marketplace account"
              description={
                hasConnectedMarketplace
                  ? `${connectedMarketplaces.length} marketplace account(s) are connected for this user.`
                  : 'No marketplace account is connected yet, so publishing and sales sync are still limited.'
              }
            />
            <StepRow
              done={setupSummary.total_listings > 0}
              icon={CircleDashed}
              title="Create the first listing"
              description={
                setupSummary.total_listings > 0
                  ? `${setupSummary.total_listings} listing(s) already exist in this workspace.`
                  : 'Add inventory or a photo batch so the user can move from setup into real listing work.'
              }
            />
          </div>
        </div>

        <div className="rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.28em] text-slate-400">Progress</p>
              <h3 className="mt-2 text-3xl font-semibold text-slate-950">{completedSteps}/4 complete</h3>
            </div>
            <div className="inline-flex h-14 w-14 items-center justify-center rounded-[22px] bg-white text-[#2563eb]">
              <CheckCircle2 size={24} />
            </div>
          </div>

          <div className="mt-5 h-3 overflow-hidden rounded-full bg-[#e5e7eb]">
            <div className="h-full rounded-full bg-[#2563eb]" style={{ width: `${(completedSteps / 4) * 100}%` }} />
          </div>

          <div className="mt-6 grid gap-3 sm:grid-cols-3 xl:grid-cols-1">
            <div className="rounded-[16px] border border-[#e5e7eb] bg-white p-4">
              <p className="text-xs font-bold uppercase tracking-[0.2em] text-slate-400">Listings</p>
              <p className="mt-2 text-2xl font-semibold text-slate-950">{setupSummary.total_listings}</p>
              <p className="mt-1 text-sm text-slate-600">Items already created in this account workspace.</p>
            </div>
            <div className="rounded-[16px] border border-[#e5e7eb] bg-white p-4">
              <p className="text-xs font-bold uppercase tracking-[0.2em] text-slate-400">Ready now</p>
              <p className="mt-2 text-2xl font-semibold text-slate-950">{setupSummary.ready_to_publish_count}</p>
              <p className="mt-1 text-sm text-slate-600">Listings that can move into publishing as-is.</p>
            </div>
            <div className="rounded-[16px] border border-[#e5e7eb] bg-white p-4">
              <p className="text-xs font-bold uppercase tracking-[0.2em] text-slate-400">Connected marketplaces</p>
              <p className="mt-2 text-2xl font-semibold text-slate-950">{connectedMarketplaces.length}</p>
              <p className="mt-1 text-sm text-slate-600">Account-level channels available to this user.</p>
            </div>
          </div>

          <div className="mt-6 rounded-[16px] border border-[#e5e7eb] bg-white p-4">
            <p className="text-sm font-semibold text-slate-950">Recommended next move</p>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {setupSummary.account_profile_complete
                ? hasConnectedMarketplace
                  ? 'This account is ready to move into listings and inventory workflow.'
                  : 'Finish marketplace connection next so the first listing can be published cleanly.'
                : 'Start by completing the operator profile so the workspace is properly initialized.'}
            </p>
          </div>
        </div>
      </div>
    </Card>
  );
}
