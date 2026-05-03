import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, PencilLine, Search, Send, X } from 'lucide-react';
import toast from 'react-hot-toast';

import AppShell from '../components/layout/AppShell';
import ListingEditor from '../components/ListingEditor';
import Badge from '../components/ui/badge';
import Button from '../components/ui/button';
import Input from '../components/ui/input';
import { Tabs } from '../components/ui/tabs';
import { useAuth } from '../contexts/AuthContext';
import { useMarketplacePublish } from '../hooks/useMarketplacePublish';
import useDashboardData from '../hooks/useDashboardData';
import {
  applyListingTemplate,
  createListingTemplate,
  generateListing,
  processListingPhoto,
  toggleAutonomousMode,
  updateListing,
} from '../lib/api';

const LISTING_TABS = [
  { value: 'drafts', label: 'Drafts' },
  { value: 'ready', label: 'Ready' },
  { value: 'published', label: 'Published' },
  { value: 'failed', label: 'Failed' },
];

const FILTER_OPTIONS = [
  { value: 'all', label: 'All marketplaces' },
  { value: 'ebay', label: 'eBay' },
  { value: 'poshmark', label: 'Poshmark' },
  { value: 'mercari', label: 'Mercari' },
  { value: 'depop', label: 'Depop' },
];

function getListingBucket(listing) {
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

export default function ListingsPage({ theme, setTheme }) {
  const { user } = useAuth();
  const { publish, publishing, errors, statusByListing, refreshStatus } = useMarketplacePublish();
  const { listings, autonomousConfig, enabledPlatforms, listingTemplates, reload } = useDashboardData(user?.id);
  const [activeTab, setActiveTab] = useState('drafts');
  const [search, setSearch] = useState('');
  const [marketFilter, setMarketFilter] = useState('all');
  const [selectedIds, setSelectedIds] = useState([]);
  const [selectedListingId, setSelectedListingId] = useState(null);

  const filteredListings = useMemo(() => {
    return listings.filter((listing) => {
      const bucket = getListingBucket(listing);
      const text = `${getListingTitle(listing)} ${listing.id}`.toLowerCase();
      const marketplaces = getListingMarketplaces(listing, enabledPlatforms);
      const matchesSearch = !search || text.includes(search.toLowerCase());
      const matchesMarket = marketFilter === 'all' || marketplaces.includes(marketFilter);
      return bucket === activeTab && matchesSearch && matchesMarket;
    });
  }, [activeTab, enabledPlatforms, listings, marketFilter, search]);

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
  }, [refreshStatus, selectedListingId]);

  const toggleRow = (id) => {
    setSelectedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  };

  const publishSelected = async () => {
    const selectedRows = listings.filter((listing) => selectedIds.includes(listing.id));
    const publishable = selectedRows.filter((listing) => {
      const bucket = getListingBucket(listing);
      return bucket === 'drafts' || bucket === 'ready';
    });
    if (!publishable.length) {
      toast.error('No selected listings are publishable.');
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

  return (
    <AppShell
      active="/listings"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
      theme={theme}
      onToggleTheme={() => {
        const next = theme === 'dark' ? 'light' : 'dark';
        setTheme(next);
        localStorage.setItem('posterpro-theme', next);
        document.documentElement.classList.toggle('dark', next === 'dark');
      }}
    >
      <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <p className="text-sm font-semibold text-[#667085]">Listings</p>
          <h1 className="mt-1 text-[2rem] font-semibold tracking-[-0.04em] text-[#111827]">Listings</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href="/inventory">
            <Button variant="outline">
              Open inventory
            </Button>
          </Link>
        </div>
      </div>

      <div className="rounded-[18px] border border-[#e5e7eb] bg-white p-4">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex flex-1 flex-col gap-3 sm:flex-row">
            <div className="relative w-full sm:max-w-[320px]">
              <Search className="pointer-events-none absolute left-4 top-3.5 text-[#98a2b3]" size={18} />
              <Input placeholder="Search listings" className="pl-11" value={search} onChange={(event) => setSearch(event.target.value)} />
            </div>
            <div className="relative w-full sm:w-[220px]">
              <select
                value={marketFilter}
                onChange={(event) => setMarketFilter(event.target.value)}
                className="pp-input h-12 w-full appearance-none pr-10 text-sm text-[#111827]"
              >
                {FILTER_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={16} className="pointer-events-none absolute right-4 top-4 text-[#98a2b3]" />
            </div>
          </div>
          <div className="text-sm text-[#667085]">{filteredListings.length} visible</div>
        </div>
      </div>

      <Tabs
        items={LISTING_TABS.map((tab) => ({ ...tab, count: tabCounts[tab.value] || 0 }))}
        value={activeTab}
        onChange={(value) => {
          setActiveTab(value);
          setSelectedIds([]);
        }}
      />

      {selectedIds.length ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-[18px] border border-[#e5e7eb] bg-white px-4 py-3">
          <p className="text-sm font-semibold text-[#111827]">{selectedIds.length} selected</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => setSelectedIds([])}>
              Clear
            </Button>
            <Button size="sm" onClick={publishSelected}>
              Publish selected
            </Button>
          </div>
        </div>
      ) : null}

      <div className={`grid gap-5 ${selectedListing ? 'xl:grid-cols-[minmax(0,1fr)_430px]' : 'grid-cols-1'}`}>
        <div className="overflow-hidden rounded-[18px] border border-[#e5e7eb] bg-white">
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-[#f8fafc]">
                <tr>
                  <th className="w-12 px-4 py-3">
                    <input
                      type="checkbox"
                      checked={filteredListings.length > 0 && selectedIds.length === filteredListings.length}
                      onChange={() =>
                        setSelectedIds(selectedIds.length === filteredListings.length ? [] : filteredListings.map((listing) => listing.id))
                      }
                      className="h-4 w-4 rounded border-[#cbd5e1] text-[#2563eb] focus:ring-[#2563eb]"
                    />
                  </th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Thumbnail</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Title</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Price</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Marketplace</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Status</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredListings.length ? (
                  filteredListings.map((listing) => {
                    const bucket = getListingBucket(listing);
                    const marketplaces = getListingMarketplaces(listing, enabledPlatforms);
                    return (
                      <tr key={listing.id} className="border-t border-[#e5e7eb] hover:bg-[#fbfdff]">
                        <td className="px-4 py-3">
                          <input
                            type="checkbox"
                            checked={selectedIds.includes(listing.id)}
                            onChange={() => toggleRow(listing.id)}
                            className="h-4 w-4 rounded border-[#cbd5e1] text-[#2563eb] focus:ring-[#2563eb]"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <div className="h-11 w-11 overflow-hidden rounded-[12px] bg-[#f2f4f7]">
                            {listing.image_urls?.[0] ? (
                              <img src={listing.image_urls[0]} alt={getListingTitle(listing)} className="h-full w-full object-cover" />
                            ) : null}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="min-w-[240px]">
                            <p className="truncate font-semibold text-[#111827]">{getListingTitle(listing)}</p>
                            <p className="mt-1 text-xs text-[#667085]">#{listing.id}</p>
                          </div>
                        </td>
                        <td className="px-4 py-3 font-medium text-[#111827]">${getListingPrice(listing)}</td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap gap-2">
                            {marketplaces.map((marketplace) => (
                              <span key={`${listing.id}-${marketplace}`} className="pp-chip border border-[#e5e7eb] bg-[#f8fafc] px-3 py-1 text-xs font-semibold text-[#475467]">
                                {formatMarketplace(marketplace)}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <Badge tone={bucket === 'failed' ? 'danger' : bucket === 'ready' || bucket === 'published' ? 'success' : 'default'}>
                            {bucket}
                          </Badge>
                          {errors[listing.id] ? <p className="mt-1 text-xs text-[#b42318]">{errors[listing.id]}</p> : null}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap gap-2">
                            <Button variant="outline" size="sm" onClick={() => setSelectedListingId(listing.id)}>
                              <PencilLine size={14} />
                              Edit
                            </Button>
                            <Button
                              size="sm"
                              disabled={bucket === 'failed' || bucket === 'published' || !!publishing[listing.id]}
                              onClick={async () => {
                                await publish(listing.id, marketplaces.length ? marketplaces : ['ebay']);
                                await reload();
                              }}
                            >
                              <Send size={14} />
                              {publishing[listing.id] ? 'Publishing…' : 'Publish'}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={7} className="px-4 py-10 text-center text-sm text-[#667085]">
                      No listings in this view.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {selectedListing ? (
          <aside className="rounded-[18px] border border-[#e5e7eb] bg-white p-4 xl:sticky xl:top-[92px] xl:max-h-[calc(100vh-120px)] xl:overflow-auto">
            <div className="mb-4 flex items-center justify-between gap-3 border-b border-[#e5e7eb] pb-4">
              <div>
                <p className="text-sm font-semibold text-[#111827]">Listing detail</p>
                <p className="text-sm text-[#667085]">Inspect, edit, and publish this listing.</p>
              </div>
              <Button variant="ghost" size="icon" onClick={() => setSelectedListingId(null)} title="Close listing panel">
                <X size={16} />
              </Button>
            </div>

            <ListingEditor
              listing={selectedListing}
              templates={listingTemplates.filter((tpl) => !tpl.category_id || tpl.category_id === selectedListing.category_id)}
              statuses={statusByListing[selectedListing.id] || []}
              publishState={{
                loading: !!publishing[selectedListing.id],
                error: errors[selectedListing.id] || '',
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
          </aside>
        ) : null}
      </div>
    </AppShell>
  );
}

ListingsPage.requireAuth = true;
