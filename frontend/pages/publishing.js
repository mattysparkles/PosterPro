import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { useRouter } from 'next/router';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import DataTable from '../components/ui/data-table';
import EmptyState from '../components/ui/empty-state';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import StatusPill from '../components/ui/status-pill';
import { Tabs } from '../components/ui/tabs';
import Toolbar from '../components/ui/toolbar';
import { useAuth } from '../contexts/AuthContext';
import useDashboardData from '../hooks/useDashboardData';
import { fetchSettingsPanels, toPublicImageUrl, toggleAutonomousMode } from '../lib/api';

const PUBLISHING_TABS = [
  { value: 'approvals', label: 'Approvals' },
  { value: 'queue', label: 'Publishing Queue' },
  { value: 'live', label: 'Live Listings' },
  { value: 'sync', label: 'Sync Status' },
];

function getListingTargets(listing, enabledPlatforms) {
  const targets = new Set(listing.marketplace_data?.targets || []);
  if (listing.ebay_publish_status || listing.ebay_listing_id) targets.add('ebay');
  if (!targets.size) {
    (enabledPlatforms || ['ebay']).forEach((target) => targets.add(target));
  }
  return Array.from(targets);
}

function formatMarketplace(name) {
  if (name === 'ebay') return 'eBay';
  return name ? name.charAt(0).toUpperCase() + name.slice(1) : 'Unknown';
}

function marketplaceStatusFor(listing, marketplace) {
  if (marketplace === 'ebay') {
    return listing.ebay_publish_status || (listing.ebay_listing_id ? 'POSTED' : listing.status || 'DRAFT');
  }
  return listing.marketplace_data?.publish?.[marketplace]?.status || ((listing.marketplace_data?.targets || []).includes(marketplace) ? 'QUEUED' : 'DRAFT');
}

function marketplaceErrorFor(listing, marketplace) {
  if (marketplace === 'ebay' && listing.ebay_publish_status === 'FAILED') {
    return listing.marketplace_data?.error || 'Publish failed';
  }
  return listing.marketplace_data?.publish?.[marketplace]?.error || listing.marketplace_data?.error || '';
}

function formatTime(value) {
  if (!value) return 'Pending';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value));
}

export default function PublishingPage() {
  const { user } = useAuth();
  const router = useRouter();
  const { listings, autonomousConfig, enabledPlatforms, reload } = useDashboardData(user?.id);
  const [activeTab, setActiveTab] = useState('queue');
  const [workflowPreferences, setWorkflowPreferences] = useState({
    review_before_publish: true,
    auto_publish_after_approval: false,
    bulk_approval_enabled: true,
    listing_preview_mode: 'marketplace',
  });

  useEffect(() => {
    fetchSettingsPanels()
      .then((panels) => setWorkflowPreferences(panels.workflow || workflowPreferences))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const tab = typeof router.query.tab === 'string' ? router.query.tab : '';
    if (tab && PUBLISHING_TABS.some((item) => item.value === tab)) {
      setActiveTab(tab);
    }
  }, [router.query.tab]);

  const selectTab = (nextTab) => {
    setActiveTab(nextTab);
    if (!router.isReady) return;
    router.replace({ pathname: router.pathname, query: { ...router.query, tab: nextTab } }, undefined, { shallow: true });
  };

  const marketplaceRows = useMemo(
    () =>
      listings.flatMap((listing) =>
        getListingTargets(listing, enabledPlatforms).map((marketplace) => ({
          id: `${listing.id}-${marketplace}`,
          listing_id: listing.id,
          title: listing.title || `Listing #${listing.id}`,
          thumbnail: listing.image_urls?.[0] || null,
          marketplace,
          status: marketplaceStatusFor(listing, marketplace),
          error: marketplaceErrorFor(listing, marketplace),
          last_sync: listing.last_refreshed || listing.updated_at || listing.created_at || null,
        })),
      ),
    [enabledPlatforms, listings],
  );

  const queueRows = useMemo(
    () =>
      marketplaceRows.filter((row) => !['POSTED', 'LIVE', 'SOLD'].includes(String(row.status).toUpperCase())),
    [marketplaceRows],
  );

  const approvalRows = useMemo(
    () =>
      listings
        .filter((listing) => listing.needs_review || listing.restricted_review_required)
        .map((listing) => ({
          id: listing.id,
          listing_id: listing.id,
          title: listing.title || `Listing #${listing.id}`,
          thumbnail: listing.image_urls?.[0] || null,
          review_status: listing.restricted_review_required ? 'Restricted review' : 'Needs review',
          targets: getListingTargets(listing, enabledPlatforms).map(formatMarketplace).join(', '),
          price: listing.suggested_price || listing.listing_price || listing.estimated_value || 0,
          updated_at: listing.updated_at || listing.created_at || null,
        })),
    [enabledPlatforms, listings],
  );

  const liveRows = useMemo(
    () =>
      marketplaceRows.filter((row) => ['POSTED', 'LIVE', 'SOLD'].includes(String(row.status).toUpperCase())),
    [marketplaceRows],
  );

  const syncRows = useMemo(() => {
    const grouped = marketplaceRows.reduce((accumulator, row) => {
      if (!accumulator[row.marketplace]) {
        accumulator[row.marketplace] = {
          marketplace: row.marketplace,
          status: 'Healthy',
          errors: 0,
          live_count: 0,
          queued_count: 0,
          last_sync: row.last_sync,
        };
      }
      const current = accumulator[row.marketplace];
      if (row.error) {
        current.errors += 1;
        current.status = 'Attention needed';
      }
      if (['POSTED', 'LIVE', 'SOLD'].includes(String(row.status).toUpperCase())) {
        current.live_count += 1;
      } else {
        current.queued_count += 1;
      }
      if (row.last_sync && (!current.last_sync || new Date(row.last_sync) > new Date(current.last_sync))) {
        current.last_sync = row.last_sync;
      }
      return accumulator;
    }, {});
    return Object.values(grouped);
  }, [marketplaceRows]);

  const tableColumns = [
    {
      key: 'thumbnail',
      label: 'Thumbnail',
      render: (row) => (
        <div className="h-10 w-10 overflow-hidden rounded-[10px] bg-[#f2f4f7]">
          {row.thumbnail ? <img src={toPublicImageUrl(row.thumbnail)} alt={row.title} className="h-full w-full object-cover" /> : null}
        </div>
      ),
    },
    {
      key: 'title',
      label: 'Listing',
      cellClassName: 'min-w-[220px]',
      render: (row) => (
        <div>
          <p className="truncate font-medium text-[#101828]">{row.title}</p>
          <p className="mt-1 text-xs text-[#667085]">#{row.listing_id}</p>
        </div>
      ),
    },
    { key: 'marketplace', label: 'Marketplace', render: (row) => formatMarketplace(row.marketplace) },
    { key: 'status', label: 'Status', render: (row) => <StatusPill status={String(row.status).toLowerCase()} label={row.status} /> },
    { key: 'error', label: 'Errors', render: (row) => row.error || 'None' },
    { key: 'last_sync', label: 'Last sync', render: (row) => formatTime(row.last_sync) },
  ];

  const publishingSubnav = useMemo(
    () => ({
      eyebrow: 'Publishing CMS',
      title: 'Marketplace Control',
      description: 'Separate approvals, queue state, live listings, and sync health through a dedicated publishing rail.',
      sections: [
        {
          label: 'Publishing Views',
          items: [
            { key: 'approvals', label: 'Approvals', active: activeTab === 'approvals', badge: approvalRows.length, description: 'Drafts waiting for sign-off', onClick: () => selectTab('approvals') },
            { key: 'queue', label: 'Queue', active: activeTab === 'queue', badge: queueRows.length, description: 'Rows still publishing', onClick: () => selectTab('queue') },
            { key: 'live', label: 'Live Listings', active: activeTab === 'live', badge: liveRows.length, description: 'Posted marketplace rows', onClick: () => selectTab('live') },
            { key: 'sync', label: 'Sync Status', active: activeTab === 'sync', badge: syncRows.length, description: 'Channel-level status health', onClick: () => selectTab('sync') },
          ],
        },
        {
          label: 'Sections',
          items: [
            { key: 'publish-policy', label: 'Publish Policy', active: false, description: 'Approval and workflow rules', onClick: () => document.getElementById('publish-policy')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
            { key: 'publish-next', label: 'Next Steps', active: false, description: 'Recommended operator actions', onClick: () => document.getElementById('publish-next')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
            { key: 'publishing-table', label: 'Queue Table', active: false, description: 'Current publishing data', onClick: () => document.getElementById('publishing-table')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
          ],
        },
      ],
    }),
    [activeTab, approvalRows.length, liveRows.length, queueRows.length, syncRows.length],
  );

  return (
    <AppShell
      active="/publishing"
      title="Publishing"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
      subnav={publishingSubnav}
    >
      <PageHeader
        title="Publishing"
        description="Watch the publish queue, live marketplace listings, and sync health from one place."
        actions={
          <div className="flex flex-wrap gap-2">
            <Link href="/jobs">
              <Button variant="outline">Open jobs console</Button>
            </Link>
            <Link href="/listings">
              <Button>Open listings</Button>
            </Link>
          </div>
        }
      />

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Awaiting approval" value={approvalRows.length} detail="Drafts that still need a human sign-off." />
        <MetricCard label="Queued to publish" value={queueRows.length} detail="Marketplace rows still moving through queue or posting." />
        <MetricCard label="Live now" value={liveRows.length} detail="Listings already posted or live in marketplace feeds." />
        <MetricCard label="Sync issues" value={syncRows.filter((row) => row.errors).length} detail="Marketplace channels with publish or sync errors." />
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
        <SectionPanel id="publish-policy" title="Publish policy" description="Current operator workflow and queue behavior.">
          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Approval gate</p>
              <p className="mt-2 text-sm font-semibold text-[#101828]">{workflowPreferences.review_before_publish ? 'Required before publish' : 'Direct publish allowed'}</p>
            </div>
            <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Bulk approvals</p>
              <p className="mt-2 text-sm font-semibold text-[#101828]">{workflowPreferences.bulk_approval_enabled ? 'Enabled' : 'Disabled'}</p>
            </div>
            <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Draft preview</p>
              <p className="mt-2 text-sm font-semibold text-[#101828]">{workflowPreferences.listing_preview_mode === 'marketplace' ? 'Marketplace preview first' : 'Editor-first preview'}</p>
            </div>
          </div>
        </SectionPanel>
        <SectionPanel id="publish-next" title="What to do next" description="Shortest path from draft queue to live listings.">
          <div className="space-y-3">
            <Link href="/listings?tab=review" className="block rounded-[12px] border border-[#e5e7eb] bg-white p-4 transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
              <p className="text-sm font-semibold text-[#101828]">Review waiting drafts</p>
              <p className="mt-1 text-sm text-[#667085]">Open the review queue, validate photos and pricing, then approve the clean batch.</p>
            </Link>
            <Link href="/settings?tab=workflow" className="block rounded-[12px] border border-[#e5e7eb] bg-white p-4 transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
              <p className="text-sm font-semibold text-[#101828]">Adjust publish policy</p>
              <p className="mt-1 text-sm text-[#667085]">Change whether operators must approve drafts before any live publish call is queued.</p>
            </Link>
            <Link href="/jobs" className="block rounded-[12px] border border-[#e5e7eb] bg-white p-4 transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
              <p className="text-sm font-semibold text-[#101828]">Review import and cross-post jobs</p>
              <p className="mt-1 text-sm text-[#667085]">Use the jobs console to inspect queued work, structured handoff results, and retry failed jobs.</p>
            </Link>
          </div>
        </SectionPanel>
      </section>

      <Toolbar
        left={
          <div className="relative w-full sm:w-[220px] md:hidden">
            <select
              value={activeTab}
              onChange={(event) => setActiveTab(event.target.value)}
              className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
            >
              {PUBLISHING_TABS.map((tab) => (
                <option key={tab.value} value={tab.value}>
                  {tab.label}
                </option>
              ))}
            </select>
            <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
          </div>
        }
        right={
          <span>
            {activeTab === 'approvals' ? approvalRows.length : activeTab === 'queue' ? queueRows.length : activeTab === 'live' ? liveRows.length : syncRows.length} visible
          </span>
        }
      />

      <Tabs
        className="hidden md:flex"
        items={[
          { value: 'approvals', label: 'Approvals', count: approvalRows.length },
          { value: 'queue', label: 'Publishing Queue', count: queueRows.length },
          { value: 'live', label: 'Live Listings', count: liveRows.length },
          { value: 'sync', label: 'Sync Status', count: syncRows.length },
        ]}
        value={activeTab}
        onChange={selectTab}
      />

      <div id="publishing-table">
      {activeTab === 'approvals' ? (
        <DataTable
          columns={[
            {
              key: 'thumbnail',
              label: 'Thumbnail',
              render: (row) => (
                <div className="h-10 w-10 overflow-hidden rounded-[10px] bg-[#f2f4f7]">
                  {row.thumbnail ? <img src={toPublicImageUrl(row.thumbnail)} alt={row.title} className="h-full w-full object-cover" /> : null}
                </div>
              ),
            },
            {
              key: 'title',
              label: 'Listing',
              cellClassName: 'min-w-[220px]',
              render: (row) => (
                <div>
                  <p className="truncate font-medium text-[#101828]">{row.title}</p>
                  <p className="mt-1 text-xs text-[#667085]">#{row.listing_id}</p>
                </div>
              ),
            },
            { key: 'review_status', label: 'Review status', render: (row) => <StatusPill status={row.review_status === 'Restricted review' ? 'warning' : 'info'} label={row.review_status} /> },
            { key: 'targets', label: 'Targets' },
            { key: 'price', label: 'Draft price', render: (row) => `$${Number(row.price || 0).toFixed(0)}` },
            { key: 'updated_at', label: 'Updated', render: (row) => formatTime(row.updated_at) },
          ]}
          rows={approvalRows}
          rowKey={(row) => row.id}
          emptyState={<EmptyState title="No drafts waiting for approval" description="When review-first workflow is active, new drafts that need sign-off will appear here." className="border-0 p-0 py-6" />}
        />
      ) : activeTab === 'sync' ? (
        <DataTable
          columns={[
            { key: 'marketplace', label: 'Marketplace', render: (row) => formatMarketplace(row.marketplace) },
            { key: 'status', label: 'Status', render: (row) => <StatusPill status={row.errors ? 'error' : 'success'} label={row.status} /> },
            { key: 'errors', label: 'Errors' },
            { key: 'live_count', label: 'Live listings' },
            { key: 'queued_count', label: 'Queued' },
            { key: 'last_sync', label: 'Last sync', render: (row) => formatTime(row.last_sync) },
          ]}
          rows={syncRows}
          rowKey={(row) => row.marketplace}
          emptyState={<EmptyState title="No marketplace activity yet" description="Publishing and sync data will appear once listings start moving out to marketplaces." className="border-0 p-0 py-6" />}
        />
      ) : (
        <DataTable
          columns={tableColumns}
          rows={activeTab === 'queue' ? queueRows : liveRows}
          rowKey={(row) => row.id}
          emptyState={<EmptyState title={`No ${activeTab === 'queue' ? 'queued' : 'live'} marketplace rows`} description="Listings will appear here as they move through publishing." className="border-0 p-0 py-6" />}
        />
      )}
      </div>
    </AppShell>
  );
}

PublishingPage.requireAuth = true;
