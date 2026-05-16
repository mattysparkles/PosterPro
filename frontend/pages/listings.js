import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, Grid2X2, List, MoreHorizontal, PencilLine, Search, Send } from 'lucide-react';
import toast from 'react-hot-toast';
import { useRouter } from 'next/router';

import AppShell from '../components/layout/AppShell';
import ListingEditor from '../components/ListingEditor';
import Button from '../components/ui/button';
import DataTable from '../components/ui/data-table';
import DataTableRowAction from '../components/ui/data-table-row-action';
import Drawer from '../components/ui/drawer';
import EmptyState from '../components/ui/empty-state';
import { Tabs } from '../components/ui/tabs';
import Input from '../components/ui/input';
import PageHeader from '../components/ui/page-header';
import StatusPill from '../components/ui/status-pill';
import Toolbar from '../components/ui/toolbar';
import { useAuth } from '../contexts/AuthContext';
import { useMarketplacePublish } from '../hooks/useMarketplacePublish';
import useDashboardData from '../hooks/useDashboardData';
import {
  applyListingTemplate,
  createListingTemplate,
  fetchPricingRecommendation,
  fetchListingIntelligence,
  fetchSettingsPanels,
  generateListing,
  processListingPhoto,
  toggleAutonomousMode,
  updateListing,
} from '../lib/api';

const LISTING_TABS = [
  { value: 'review', label: 'Needs Review' },
  { value: 'drafts', label: 'Drafts' },
  { value: 'ready', label: 'Ready' },
  { value: 'published', label: 'Published' },
  { value: 'failed', label: 'Failed' },
];

const FILTER_OPTIONS = [
  { value: 'all', label: 'All marketplaces' },
  { value: 'ebay', label: 'eBay' },
  { value: 'etsy', label: 'Etsy' },
  { value: 'poshmark', label: 'Poshmark' },
  { value: 'mercari', label: 'Mercari' },
  { value: 'depop', label: 'Depop' },
  { value: 'whatnot', label: 'Whatnot' },
  { value: 'vinted', label: 'Vinted' },
];

const SOURCE_OPTIONS = [
  { value: 'all', label: 'All sources' },
  { value: 'amazon_vine', label: 'Amazon Vine' },
];

function getListingBucket(listing) {
  if (listing.restricted_review_required || listing.needs_review) return 'review';
  if (listing.status === 'error' || listing.ebay_publish_status === 'FAILED') return 'failed';
  if (listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id) return 'published';
  if (listing.status === 'ready') return 'ready';
  return 'drafts';
}

function getListingTitle(listing) {
  return listing.title || listing.suggested_title || `Listing #${listing.id}`;
}

function getListingPrice(listing) {
  return Number(listing.suggested_price || listing.price || 0).toFixed(0);
}

function getListingMarketplaces(listing, enabledPlatforms) {
  const names = new Set();
  if (listing.ebay_publish_status || listing.ebay_listing_id) names.add('ebay');
  (listing.marketplace_data?.targets || []).forEach((target) => names.add(target));
  if (!names.size) {
    (enabledPlatforms || ['ebay']).slice(0, 2).forEach((platform) => names.add(platform));
  }
  return Array.from(names).slice(0, 2);
}

function formatMarketplace(name) {
  if (name === 'ebay') return 'eBay';
  if (!name) return 'Draft';
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export default function ListingsPage() {
  const { user } = useAuth();
  const router = useRouter();
  const { publish, publishing, errors, statusByListing, refreshStatus } = useMarketplacePublish();
  const { listings, autonomousConfig, enabledPlatforms, listingTemplates, reload } = useDashboardData(user?.id);
  const [activeTab, setActiveTab] = useState('drafts');
  const [search, setSearch] = useState('');
  const [marketFilter, setMarketFilter] = useState('all');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [selectedIds, setSelectedIds] = useState([]);
  const [selectedListingId, setSelectedListingId] = useState(null);
  const [viewMode, setViewMode] = useState('table');
  const [pricingRecommendation, setPricingRecommendation] = useState(null);
  const [listingIntelligence, setListingIntelligence] = useState(null);
  const [workflowPreferences, setWorkflowPreferences] = useState({
    review_before_publish: true,
    auto_publish_after_approval: false,
    bulk_approval_enabled: true,
    listing_preview_mode: 'marketplace',
  });

  useEffect(() => {
    setSearch(typeof router.query.q === 'string' ? router.query.q : '');
  }, [router.query.q]);

  useEffect(() => {
    const tab = typeof router.query.tab === 'string' ? router.query.tab : '';
    if (tab && LISTING_TABS.some((item) => item.value === tab)) {
      setActiveTab(tab);
    }
  }, [router.query.tab]);

  const selectTab = (nextTab) => {
    setActiveTab(nextTab);
    setSelectedIds([]);
    if (!router.isReady) return;
    router.replace({ pathname: router.pathname, query: { ...router.query, tab: nextTab } }, undefined, { shallow: true });
  };

  useEffect(() => {
    if (!user?.id) return;
    fetchSettingsPanels()
      .then((panels) => setWorkflowPreferences(panels.workflow || workflowPreferences))
      .catch(() => undefined);
  }, [user?.id]);

  const filteredListings = useMemo(() => {
    return listings.filter((listing) => {
      const bucket = getListingBucket(listing);
      const text = `${getListingTitle(listing)} ${listing.id}`.toLowerCase();
      const marketplaces = getListingMarketplaces(listing, enabledPlatforms);
      const matchesSearch = !search || text.includes(search.toLowerCase());
      const matchesMarket = marketFilter === 'all' || marketplaces.includes(marketFilter);
      const matchesSource = sourceFilter === 'all' || listing.source_type === sourceFilter;
      return bucket === activeTab && matchesSearch && matchesMarket && matchesSource;
    });
  }, [activeTab, enabledPlatforms, listings, marketFilter, search, sourceFilter]);

  const selectedListing = useMemo(
    () => listings.find((listing) => listing.id === selectedListingId) || null,
    [listings, selectedListingId],
  );

  const tabCounts = useMemo(
    () =>
      Object.fromEntries(LISTING_TABS.map((tab) => [tab.value, listings.filter((listing) => getListingBucket(listing) === tab.value).length])),
    [listings],
  );

  useEffect(() => {
    if (!selectedListingId) return;
    refreshStatus(selectedListingId).catch(() => undefined);
    fetchPricingRecommendation(selectedListingId)
      .then(setPricingRecommendation)
      .catch(() => setPricingRecommendation(null));
    fetchListingIntelligence(selectedListingId)
      .then(setListingIntelligence)
      .catch(() => setListingIntelligence(null));
  }, [refreshStatus, selectedListingId]);

  const listingsSubnav = useMemo(
    () => ({
      eyebrow: 'Listings CMS',
      title: 'Listings Manager',
      description: 'Navigate review queues, draft states, and editor workflows from a dedicated listings rail.',
      sections: [
        {
          label: 'Queues',
          items: LISTING_TABS.map((tab) => ({
            key: tab.value,
            label: tab.label,
            active: activeTab === tab.value,
            badge: tabCounts[tab.value] || 0,
            description:
              tab.value === 'review'
                ? 'Items waiting for approval'
                : tab.value === 'drafts'
                ? 'Editable in-progress listings'
                : tab.value === 'ready'
                ? 'Publishable listings'
                : tab.value === 'published'
                ? 'Live marketplace records'
                : 'Rows with publish problems',
            onClick: () => selectTab(tab.value),
          })),
        },
        {
          label: 'Sections',
          items: [
            { key: 'listing-toolbar', label: 'Filters', active: false, description: 'Search and filter controls', onClick: () => document.getElementById('listing-toolbar')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
            { key: 'listing-status', label: 'Workflow Status', active: false, description: 'Approval and preview policy', onClick: () => document.getElementById('listing-status')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
            { key: 'listing-results', label: 'Results', active: false, description: 'Table or grid listing results', onClick: () => document.getElementById('listing-results')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
          ],
        },
      ],
    }),
    [activeTab, tabCounts],
  );

  const approveListing = async (listingId) => {
    await updateListing(listingId, { status: 'ready', needs_review: false });
    await reload();
  };

  const toggleRow = (id) => {
    setSelectedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  };

  const publishSelected = async () => {
    const selectedRows = listings.filter((listing) => selectedIds.includes(listing.id));
    const publishable = selectedRows.filter((listing) => {
      const bucket = getListingBucket(listing);
      if (workflowPreferences.review_before_publish) {
        return bucket === 'ready';
      }
      return bucket === 'drafts' || bucket === 'ready';
    });
    if (!publishable.length) {
      toast.error(workflowPreferences.review_before_publish ? 'Approve selected drafts before publishing them.' : 'No selected listings are publishable.');
      return;
    }

    const results = await Promise.allSettled(
      publishable.map((listing) => publish(listing.id, getListingMarketplaces(listing, enabledPlatforms).length ? getListingMarketplaces(listing, enabledPlatforms) : ['ebay'])),
    );

    const failed = results.filter((result) => result.status === 'rejected').length;
    if (failed) {
      toast.error(`${failed} publish action${failed === 1 ? '' : 's'} failed.`);
    } else {
      toast.success(`Queued ${publishable.length} listing${publishable.length === 1 ? '' : 's'} for publish.`);
    }

    await reload();
  };

  const approveSelected = async () => {
    const reviewRows = listings.filter((listing) => selectedIds.includes(listing.id) && getListingBucket(listing) === 'review');
    if (!reviewRows.length) {
      toast.error('Select one or more review-queue listings first.');
      return;
    }
    await Promise.all(reviewRows.map((listing) => approveListing(listing.id)));
    toast.success(`Approved ${reviewRows.length} listing${reviewRows.length === 1 ? '' : 's'}.`);
    setSelectedIds([]);
  };

  return (
    <AppShell
      active="/listings"
      title="Listings"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
      subnav={listingsSubnav}
    >
      <PageHeader
        title="Listings"
        description="Edit, review, and publish marketplace listing drafts."
        actions={
          <div className="flex flex-wrap gap-2">
            <Link href="/listings/new">
              <Button variant="outline">New item</Button>
            </Link>
            <Link href="/intake">
              <Button>Import photos</Button>
            </Link>
          </div>
        }
      />

      <div id="listing-toolbar">
      <Toolbar
        left={
          <>
            <div className="relative w-full sm:max-w-[320px]">
              <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" size={16} />
              <Input placeholder="Search listings" className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} />
            </div>
            <div className="relative w-full sm:w-[220px]">
              <select
                value={marketFilter}
                onChange={(event) => setMarketFilter(event.target.value)}
                className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
              >
                {FILTER_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
            </div>
            <div className="relative w-full sm:w-[180px]">
              <select
                value={sourceFilter}
                onChange={(event) => setSourceFilter(event.target.value)}
                className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
              >
                {SOURCE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
            </div>
            <div className="relative w-full sm:w-[180px]">
              <select
                value={activeTab}
                onChange={(event) => {
                  setActiveTab(event.target.value);
                  setSelectedIds([]);
                }}
                className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12 md:hidden"
              >
                {LISTING_TABS.map((tab) => (
                  <option key={tab.value} value={tab.value}>
                    {tab.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3] md:hidden" />
            </div>
          </>
        }
        right={
          <div className="flex items-center gap-2">
            <span>{filteredListings.length} visible</span>
            <div className="hidden rounded-[10px] border border-[#e5e7eb] bg-white p-1 md:flex">
              <button
                type="button"
                onClick={() => setViewMode('table')}
                className={`inline-flex h-8 items-center gap-2 rounded-[8px] px-3 text-xs font-medium ${
                  viewMode === 'table' ? 'bg-[#eef4ff] text-[#2563eb]' : 'text-[#667085]'
                }`}
              >
                <List size={14} />
                Table
              </button>
              <button
                type="button"
                onClick={() => setViewMode('grid')}
                className={`inline-flex h-8 items-center gap-2 rounded-[8px] px-3 text-xs font-medium ${
                  viewMode === 'grid' ? 'bg-[#eef4ff] text-[#2563eb]' : 'text-[#667085]'
                }`}
              >
                <Grid2X2 size={14} />
                Grid
              </button>
            </div>
          </div>
        }
      />
      </div>

      <Tabs
        className="hidden md:flex"
        items={LISTING_TABS.map((tab) => ({ ...tab, count: tabCounts[tab.value] || 0 }))}
        value={activeTab}
        onChange={selectTab}
      />

      <section id="listing-status" className="grid gap-4 xl:grid-cols-3">
        <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
          <p className="text-sm font-semibold text-[#101828]">Approval mode</p>
          <p className="mt-2 text-sm text-[#667085]">
            {workflowPreferences.review_before_publish
              ? 'Drafts must be approved before they can be published.'
              : 'Direct draft publishing is allowed for this workspace.'}
          </p>
        </div>
        <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
          <p className="text-sm font-semibold text-[#101828]">Bulk review</p>
          <p className="mt-2 text-sm text-[#667085]">
            {workflowPreferences.bulk_approval_enabled
              ? 'Use row selection to approve many reviewed drafts together.'
              : 'Operators should approve listings one at a time.'}
          </p>
        </div>
        <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
          <p className="text-sm font-semibold text-[#101828]">Preview layout</p>
          <p className="mt-2 text-sm text-[#667085]">
            {workflowPreferences.listing_preview_mode === 'marketplace'
              ? 'Review opens in a marketplace-style preview first.'
              : 'Review opens in editor-first mode.'}
          </p>
        </div>
      </section>

      {selectedIds.length ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-3">
          <p className="text-sm font-medium text-[#101828]">{selectedIds.length} selected</p>
          <div className="flex flex-wrap gap-2">
            {workflowPreferences.bulk_approval_enabled ? (
              <Button variant="outline" size="sm" onClick={approveSelected}>
                Approve selected
              </Button>
            ) : null}
            <Button variant="outline" size="sm" onClick={() => setSelectedIds([])}>
              Clear
            </Button>
            <Button size="sm" onClick={publishSelected}>
              Publish selected
            </Button>
          </div>
        </div>
      ) : null}

      <div id="listing-results" className={`grid gap-5 ${selectedListing ? 'xl:grid-cols-[minmax(0,1fr)_480px]' : 'grid-cols-1'}`}>
        {viewMode === 'table' ? (
          <DataTable
          columns={[
            {
              key: 'thumbnail',
              label: 'Thumbnail',
              render: (listing) => (
                <div className="h-10 w-10 overflow-hidden rounded-[10px] bg-[#f2f4f7]">
                  {listing.image_urls?.[0] ? (
                    <img src={listing.image_urls[0]} alt={getListingTitle(listing)} className="h-full w-full object-cover" />
                  ) : null}
                </div>
              ),
            },
            {
              key: 'title',
              label: 'Title',
              cellClassName: 'min-w-[220px]',
              render: (listing) => (
                <div>
                  <p className="truncate font-medium text-[#101828]">{getListingTitle(listing)}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <p className="text-xs text-[#667085]">#{listing.id}</p>
                    {listing.sku ? <span className="pp-chip">SKU {listing.sku}</span> : null}
                    {listing.source_type === 'amazon_vine' ? <span className="pp-chip">Vine</span> : null}
                    {listing.needs_review ? <span className="pp-chip">Needs Review</span> : null}
                    {listing.restricted_review_required ? <span className="pp-chip">Restricted Review</span> : null}
                    {listing.custom_labels?.includes('needs_photos') ? <span className="pp-chip">Image Missing</span> : null}
                  </div>
                </div>
              ),
            },
            {
              key: 'price',
              label: 'Price',
              render: (listing) => `$${getListingPrice(listing)}`,
            },
            {
              key: 'quantity',
              label: 'Quantity',
              render: (listing) => listing.quantity ?? 1,
            },
            {
              key: 'marketplaces',
              label: 'Marketplaces',
              render: (listing) => (
                <div className="flex flex-wrap gap-2">
                  {getListingMarketplaces(listing, enabledPlatforms).map((marketplace) => (
                    <span key={`${listing.id}-${marketplace}`} className="pp-chip">
                      {formatMarketplace(marketplace)}
                    </span>
                  ))}
                </div>
              ),
            },
            {
              key: 'status',
              label: 'Status',
              render: (listing) => {
                const bucket = getListingBucket(listing);
                return (
                  <div>
                    <StatusPill status={bucket} label={bucket.charAt(0).toUpperCase() + bucket.slice(1)} />
                    {errors[listing.id] ? <p className="mt-1 text-xs text-[#b42318]">{errors[listing.id]}</p> : null}
                  </div>
                );
              },
            },
            {
              key: 'updated',
              label: 'Updated',
              render: (listing) => new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(new Date(listing.updated_at || listing.created_at || Date.now())),
            },
            {
              key: 'actions',
              label: 'Actions',
              cellClassName: 'w-[180px]',
              render: (listing) => {
                const bucket = getListingBucket(listing);
                const marketplaces = getListingMarketplaces(listing, enabledPlatforms);
                const canPublish = workflowPreferences.review_before_publish ? bucket === 'ready' : bucket === 'drafts' || bucket === 'ready';
                return (
                  <div className="flex items-center gap-2">
                    <DataTableRowAction variant="outline" onClick={() => setSelectedListingId(listing.id)}>
                      <PencilLine size={14} />
                      Edit
                    </DataTableRowAction>
                    <DataTableRowAction variant="outline" onClick={() => router.push(`/listings/${listing.id}`)}>
                      Open
                    </DataTableRowAction>
                    {bucket === 'review' ? (
                      <DataTableRowAction
                        variant="outline"
                        onClick={async () => {
                          await approveListing(listing.id);
                        }}
                      >
                        Approve
                      </DataTableRowAction>
                    ) : null}
                    {canPublish ? (
                      <DataTableRowAction
                        onClick={async () => {
                          await publish(listing.id, marketplaces.length ? marketplaces : ['ebay']);
                          await reload();
                        }}
                      >
                        <Send size={14} />
                        {publishing[listing.id] ? 'Publishing…' : 'Publish'}
                      </DataTableRowAction>
                    ) : (
                      <Button variant="ghost" size="sm" title="More actions">
                        <MoreHorizontal size={14} />
                      </Button>
                    )}
                  </div>
                );
              },
            },
          ]}
          rows={filteredListings}
          rowKey={(row) => row.id}
          selectedRows={selectedIds}
          onToggleRow={toggleRow}
          allSelected={filteredListings.length > 0 && selectedIds.length === filteredListings.length}
          onToggleAll={() => setSelectedIds(selectedIds.length === filteredListings.length ? [] : filteredListings.map((listing) => listing.id))}
          onRowClick={(listing) => setSelectedListingId(listing.id)}
          emptyState={<EmptyState title="No listings found" description="Adjust the current filters or import new inventory to create drafts." className="border-0 p-0 py-6" />}
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {filteredListings.length ? (
              filteredListings.map((listing) => {
                const bucket = getListingBucket(listing);
                const marketplaces = getListingMarketplaces(listing, enabledPlatforms);
                const canPublish = workflowPreferences.review_before_publish ? bucket === 'ready' : bucket === 'drafts' || bucket === 'ready';
                const selected = selectedIds.includes(listing.id);
                return (
                  <button
                    key={listing.id}
                    type="button"
                    onClick={() => setSelectedListingId(listing.id)}
                    className={`rounded-[16px] border bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff] ${
                      selected ? 'border-[#bfd2ff] ring-2 ring-[#dbe7ff]' : 'border-[#e5e7eb]'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <div className="h-16 w-16 overflow-hidden rounded-[14px] bg-[#f2f4f7]">
                          {listing.image_urls?.[0] ? <img src={listing.image_urls[0]} alt={getListingTitle(listing)} className="h-full w-full object-cover" /> : null}
                        </div>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-[#101828]">{getListingTitle(listing)}</p>
                          <p className="mt-1 text-xs text-[#667085]">#{listing.id}{listing.sku ? ` · SKU ${listing.sku}` : ''}</p>
                        </div>
                      </div>
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={(event) => {
                          event.stopPropagation();
                          toggleRow(listing.id);
                        }}
                        onClick={(event) => event.stopPropagation()}
                        className="mt-1 h-4 w-4 rounded border-[#cbd5e1] text-[#2563eb]"
                      />
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <StatusPill status={bucket} label={bucket.charAt(0).toUpperCase() + bucket.slice(1)} />
                      {marketplaces.map((marketplace) => (
                        <span key={`${listing.id}-${marketplace}`} className="pp-chip">
                          {formatMarketplace(marketplace)}
                        </span>
                      ))}
                    </div>
                    <div className="mt-4 grid grid-cols-3 gap-3">
                      <div>
                        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#667085]">Price</p>
                        <p className="mt-1 text-sm font-semibold text-[#101828]">${getListingPrice(listing)}</p>
                      </div>
                      <div>
                        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#667085]">Qty</p>
                        <p className="mt-1 text-sm font-semibold text-[#101828]">{listing.quantity ?? 1}</p>
                      </div>
                      <div>
                        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#667085]">Updated</p>
                        <p className="mt-1 text-sm font-semibold text-[#101828]">
                          {new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(new Date(listing.updated_at || listing.created_at || Date.now()))}
                        </p>
                      </div>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      {bucket === 'review' ? (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={async (event) => {
                            event.stopPropagation();
                            await approveListing(listing.id);
                          }}
                        >
                          Approve
                        </Button>
                      ) : null}
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={(event) => {
                          event.stopPropagation();
                          router.push(`/listings/${listing.id}`);
                        }}
                      >
                        Open
                      </Button>
                      {canPublish ? (
                        <Button
                          size="sm"
                          onClick={async (event) => {
                            event.stopPropagation();
                            await publish(listing.id, marketplaces.length ? marketplaces : ['ebay']);
                            await reload();
                          }}
                        >
                          {publishing[listing.id] ? 'Publishing…' : 'Publish'}
                        </Button>
                      ) : null}
                    </div>
                  </button>
                );
              })
            ) : (
              <EmptyState title="No listings found" description="Adjust the current filters or import new inventory to create drafts." />
            )}
          </div>
        )}

        <Drawer
          open={!!selectedListing}
          title={workflowPreferences.listing_preview_mode === 'marketplace' ? 'Listing review preview' : 'Listing editor'}
          description="Inspect, review pricing reasoning, edit listing data, and approve or publish the draft."
          onClose={() => setSelectedListingId(null)}
          widthClassName="xl:w-[720px]"
        >
          {selectedListing ? (
            <ListingEditor
              listing={selectedListing}
              pricingRecommendation={pricingRecommendation}
              listingIntelligence={listingIntelligence}
              workflowPreferences={workflowPreferences}
              templates={listingTemplates.filter((tpl) => !tpl.category_id || tpl.category_id === selectedListing.category_id)}
              statuses={statusByListing[selectedListing.id] || []}
              publishState={{
                loading: !!publishing[selectedListing.id],
                error: errors[selectedListing.id] || '',
              }}
              onApprove={async (id) => {
                await approveListing(id);
              }}
              onSave={async (id, form) => {
                await updateListing(id, form);
                await reload();
              }}
              onApplyTemplate={async (id, templateId) => {
                await applyListingTemplate(id, templateId);
                await reload();
              }}
              onSaveTemplate={async (payload) => {
                await createListingTemplate(payload);
                await reload();
              }}
              onGenerate={async (id) => {
                await generateListing(id);
                await reload();
              }}
              onPublish={async (id, targets) => {
                await publish(id, targets);
                await reload();
              }}
              onPhotoUpdated={async ({ listingId, sourceImage, file, removeBackground, edits }) => {
                await processListingPhoto({ listingId, sourceImage, file, removeBackground, edits });
                await reload();
              }}
            />
          ) : null}
        </Drawer>
      </div>
    </AppShell>
  );
}

ListingsPage.requireAuth = true;
