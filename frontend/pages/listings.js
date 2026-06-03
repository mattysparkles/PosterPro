import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, Grid2X2, List, Search } from 'lucide-react';
import toast from 'react-hot-toast';
import { useRouter } from 'next/router';

import AppShell from '../components/layout/AppShell';
import ActionBar from '../components/ui/action-bar';
import ListingEditor from '../components/ListingEditor';
import Button from '../components/ui/button';
import DataTable from '../components/ui/data-table';
import Drawer from '../components/ui/drawer';
import EmptyState from '../components/ui/empty-state';
import MetricCard from '../components/ui/metric-card';
import { Tabs } from '../components/ui/tabs';
import Input from '../components/ui/input';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import StatusPill from '../components/ui/status-pill';
import Toolbar from '../components/ui/toolbar';
import { useAuth } from '../contexts/AuthContext';
import { useMarketplacePublish } from '../hooks/useMarketplacePublish';
import useDashboardData from '../hooks/useDashboardData';
import { formatPublishFailureMessage, isEbayReconnectRequiredError } from '../lib/publish-status';
import {
  applyListingTemplate,
  createListingTemplate,
  fetchCrosspostPreview,
  fetchMarketplaceJobsOverview,
  fetchPricingRecommendation,
  fetchListingIntelligence,
  fetchSettingsPanels,
  backfillVineListingImages,
  deleteListing as deleteListingApi,
  deleteListingsBulk as deleteListingsBulkApi,
  generateListing,
  processListingPhoto,
  toPublicImageUrl,
  approveListing as approveListingApi,
  approveListingsBulk,
  publishListingsBulk,
  publishListingEbay,
  toggleAutonomousMode,
  updateListing,
} from '../lib/api';

const LISTING_TABS = [
  { value: 'review', label: 'Needs Review' },
  { value: 'drafts', label: 'Drafts' },
  { value: 'ready', label: 'Ready' },
  { value: 'published', label: 'Published' },
  { value: 'failed', label: 'Failed' },
  { value: 'archived', label: 'Archived' },
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

function isAmazonVineSource(listing) {
  const source = String(listing?.source_type || '').toLowerCase();
  const hint = String(listing?.source || listing?.ingest_source || listing?.marketplace_source || '').toLowerCase();
  return source === 'amazon_vine' || hint.includes('vine');
}

function isArchivedListing(listing) {
  return (listing.custom_labels || []).includes('archived_vine') || listing.status === 'rejected';
}

function getListingThumbnail(listing) {
  const sourceMetadata = listing?.source_metadata || {};
  // For Vine rows, prefer explicit listing images only to avoid mismatched legacy source previews.
  if (isAmazonVineSource(listing)) {
    return listing?.image_urls?.[0] || '';
  }
  return (
    listing?.image_urls?.[0]
    || sourceMetadata?.original_image_urls?.[0]
    || sourceMetadata?.image_urls?.[0]
    || sourceMetadata?.primary_image_url
    || sourceMetadata?.source_image_url
    || ''
  );
}

function getListingBucket(listing) {
  if (isArchivedListing(listing)) return 'archived';
  if (listing.status === 'draft') return 'drafts';
  if (listing.status === 'error' || listing.ebay_publish_status === 'FAILED') return 'failed';
  if (listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id) return 'published';
  if (listing.status === 'ready') return 'ready';
  if (listing.restricted_review_required || listing.needs_review) return 'review';
  return 'review';
}

function matchesTab(listing, tab) {
  if (tab === 'archived') return isArchivedListing(listing);
  if (isArchivedListing(listing)) return false;
  if (tab === 'drafts') return listing.status === 'draft';
  if (tab === 'review') return Boolean((listing.needs_review || listing.restricted_review_required) && listing.status !== 'ready');
  if (tab === 'ready') return listing.status === 'ready';
  if (tab === 'published') return Boolean(listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id);
  if (tab === 'failed') return Boolean(listing.status === 'error' || listing.ebay_publish_status === 'FAILED');
  return false;
}

function getListingTitle(listing) {
  return listing.title || listing.suggested_title || `Listing #${listing.id}`;
}

function getListingPrice(listing) {
  return Number(listing.suggested_price || listing.price || 0).toFixed(0);
}

function getListingImageCount(listing) {
  const sourceMetadata = listing?.source_metadata || {};
  const urls = [
    ...(Array.isArray(listing?.image_urls) ? listing.image_urls : []),
    ...(Array.isArray(sourceMetadata?.image_urls) ? sourceMetadata.image_urls : []),
    ...(Array.isArray(sourceMetadata?.original_image_urls) ? sourceMetadata.original_image_urls : []),
  ];
  return new Set(urls.filter(Boolean)).size;
}

function getListingMarketplaces(listing, enabledPlatforms, { allowFallback = true } = {}) {
  const names = new Set();
  if (listing.ebay_publish_status || listing.ebay_listing_id) names.add('ebay');
  (listing.marketplace_data?.targets || []).forEach((target) => names.add(target));
  if (!names.size && allowFallback) {
    (enabledPlatforms || ['ebay']).slice(0, 2).forEach((platform) => names.add(platform));
  }
  return Array.from(names).slice(0, 2);
}

function formatMarketplace(name) {
  if (name === 'ebay') return 'eBay';
  if (name === 'unassigned') return 'Unassigned';
  if (!name) return 'Draft';
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function getQuickPublishTargets(listing, enabledPlatforms) {
  const marketplaces = getListingMarketplaces(listing, enabledPlatforms);
  if (marketplaces.includes('ebay')) {
    return ['ebay'];
  }
  return marketplaces.length ? [marketplaces[0]] : ['ebay'];
}

function getListingFailureMessage(listing) {
  if (listing.ebay_publish_status === 'FAILED') {
    return formatPublishFailureMessage(listing.marketplace_data?.error, 'ebay');
  }
  return listing.marketplace_data?.error || '';
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
  const [crosspostPreview, setCrosspostPreview] = useState([]);
  const [crosspostPreviewLoading, setCrosspostPreviewLoading] = useState(false);
  const [jobsOverview, setJobsOverview] = useState({ import_jobs: [], crosspost_jobs: [] });
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

  useEffect(() => {
    let active = true;
    const loadJobs = async () => {
      try {
        const overview = await fetchMarketplaceJobsOverview();
        if (active) {
          setJobsOverview(overview || { import_jobs: [], crosspost_jobs: [] });
        }
      } catch {
        if (active) setJobsOverview({ import_jobs: [], crosspost_jobs: [] });
      }
    };
    loadJobs();
    const timer = setInterval(loadJobs, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const filteredListings = useMemo(() => {
    return listings.filter((listing) => {
      const text = `${getListingTitle(listing)} ${listing.id}`.toLowerCase();
      const marketplaces = getListingMarketplaces(listing, enabledPlatforms, { allowFallback: false });
      const matchesSearch = !search || text.includes(search.toLowerCase());
      const matchesMarket = marketFilter === 'all' || marketplaces.includes(marketFilter);
      const matchesSource = sourceFilter === 'all' || (sourceFilter === 'amazon_vine' ? isAmazonVineSource(listing) : listing.source_type === sourceFilter);
      return matchesTab(listing, activeTab) && matchesSearch && matchesMarket && matchesSource;
    });
  }, [activeTab, enabledPlatforms, listings, marketFilter, search, sourceFilter]);

  const baseFilteredListings = useMemo(() => {
    return listings.filter((listing) => {
      const text = `${getListingTitle(listing)} ${listing.id}`.toLowerCase();
      const marketplaces = getListingMarketplaces(listing, enabledPlatforms, { allowFallback: false });
      const matchesSearch = !search || text.includes(search.toLowerCase());
      const matchesMarket = marketFilter === 'all' || marketplaces.includes(marketFilter);
      const matchesSource = sourceFilter === 'all' || (sourceFilter === 'amazon_vine' ? isAmazonVineSource(listing) : listing.source_type === sourceFilter);
      return matchesSearch && matchesMarket && matchesSource;
    });
  }, [enabledPlatforms, listings, marketFilter, search, sourceFilter]);

  const selectedListing = useMemo(
    () => listings.find((listing) => listing.id === selectedListingId) || null,
    [listings, selectedListingId],
  );

  const selectedRows = useMemo(
    () => listings.filter((listing) => selectedIds.includes(listing.id)),
    [listings, selectedIds],
  );

  const selectedReviewRows = useMemo(
    () => selectedRows.filter((listing) => getListingBucket(listing) === 'review' || listing.status === 'draft'),
    [selectedRows],
  );

  const selectedPublishableRows = useMemo(() => {
    return selectedRows.filter((listing) => {
      const bucket = getListingBucket(listing);
      if (workflowPreferences.review_before_publish) {
        return bucket === 'ready';
      }
      return bucket === 'drafts' || bucket === 'ready';
    });
  }, [selectedRows, workflowPreferences.review_before_publish]);

  const tabCounts = useMemo(
    () =>
      Object.fromEntries(LISTING_TABS.map((tab) => [tab.value, listings.filter((listing) => matchesTab(listing, tab.value)).length])),
    [listings],
  );

  const listingMetrics = useMemo(() => {
    const visibleDrafts = baseFilteredListings.filter((listing) => !isArchivedListing(listing) && matchesTab(listing, 'drafts'));
    const readyToPublish = baseFilteredListings.filter((listing) => !isArchivedListing(listing) && matchesTab(listing, 'ready'));
    const reviewQueue = baseFilteredListings.filter((listing) => !isArchivedListing(listing) && matchesTab(listing, 'review'));

    const sumAmount = (rows) => rows.reduce((total, row) => total + Number(row.listing_price || row.suggested_price || row.price || 0), 0);
    const byMarket = {};
    baseFilteredListings
      .filter((listing) => !isArchivedListing(listing) && (getListingBucket(listing) === 'drafts' || getListingBucket(listing) === 'ready' || getListingBucket(listing) === 'published'))
      .forEach((listing) => {
        const explicitMarkets = getListingMarketplaces(listing, enabledPlatforms, { allowFallback: false });
        if (!explicitMarkets.length) {
          if (!byMarket.unassigned) byMarket.unassigned = { count: 0, amount: 0 };
          byMarket.unassigned.count += 1;
          byMarket.unassigned.amount += Number(listing.listing_price || listing.suggested_price || listing.price || 0);
          return;
        }
        explicitMarkets.forEach((market) => {
          if (!byMarket[market]) byMarket[market] = { count: 0, amount: 0 };
          byMarket[market].count += 1;
          byMarket[market].amount += Number(listing.listing_price || listing.suggested_price || listing.price || 0);
        });
      });

    return {
      reviewCount: reviewQueue.length,
      reviewAmount: sumAmount(reviewQueue),
      draftCount: visibleDrafts.length,
      draftAmount: sumAmount(visibleDrafts),
      readyCount: readyToPublish.length,
      readyAmount: sumAmount(readyToPublish),
      byMarket,
    };
  }, [baseFilteredListings, enabledPlatforms]);

  const publishJobStats = useMemo(() => {
    const allJobs = [...(jobsOverview.import_jobs || []), ...(jobsOverview.crosspost_jobs || [])];
    const publishJobs = allJobs.filter((job) => String(job?.source_marketplace || '').toLowerCase() !== 'import');
    const queued = publishJobs.filter((job) => ['queued', 'running'].includes(String(job.status).toLowerCase())).length;
    const completed = publishJobs.filter((job) => String(job.status).toLowerCase() === 'completed').length;
    const failed = publishJobs.filter((job) => String(job.status).toLowerCase() === 'failed').length;
    const total = publishJobs.length;
    const progress = total ? Math.round((completed / total) * 100) : 0;
    return { queued, completed, failed, total, progress };
  }, [jobsOverview]);

  useEffect(() => {
    if (!selectedListingId) return;
    refreshStatus(selectedListingId).catch(() => undefined);
    setCrosspostPreviewLoading(true);
    fetchCrosspostPreview(selectedListingId, enabledPlatforms || [])
      .then((rows) => setCrosspostPreview(Array.isArray(rows) ? rows : []))
      .catch(() => setCrosspostPreview([]))
      .finally(() => setCrosspostPreviewLoading(false));
    fetchPricingRecommendation(selectedListingId)
      .then(setPricingRecommendation)
      .catch(() => setPricingRecommendation(null));
    fetchListingIntelligence(selectedListingId)
      .then(setListingIntelligence)
      .catch(() => setListingIntelligence(null));
  }, [enabledPlatforms, refreshStatus, selectedListingId]);

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
          label: 'Listing actions',
          items: [
            { key: 'all-listings', label: 'All listings', active: false, description: 'Open the full listings table.', onClick: () => router.push('/listings') },
            { key: 'new-listing', label: 'Create new listing', active: false, description: 'Start a blank listing draft.', onClick: () => router.push('/listings/new') },
            { key: 'import-listings', label: 'Import marketplace listings', active: false, description: 'Import existing marketplace listings.', onClick: () => router.push('/settings?tab=marketplaces') },
            { key: 'intake-photos', label: 'Intake photos', active: false, description: 'Upload photos and create new draft rows.', onClick: () => router.push('/intake') },
            ...(user?.can_access_vine_import
              ? [
                  {
                    key: 'vine-import',
                    label: 'Import Vine data',
                    active: false,
                    description: 'Create listing drafts from Vine files.',
                    onClick: () => router.push('/imports/vine'),
                  },
                ]
              : []),
          ],
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
    [activeTab, router, tabCounts, user?.can_access_vine_import],
  );

  const approveListing = async (listingId) => {
    await approveListingApi(listingId);
    await reload();
  };

  const approveListingAndMoveNext = async (listingId) => {
    await approveListing(listingId);
    const orderedVisibleIds = filteredListings.map((listing) => listing.id);
    const currentIndex = orderedVisibleIds.indexOf(listingId);
    if (currentIndex === -1) {
      setSelectedListingId(null);
      return;
    }
    const nextId = orderedVisibleIds[currentIndex + 1];
    setSelectedListingId(nextId || null);
  };

  const publishSingleListing = async (listing, event) => {
    event?.stopPropagation?.();
    try {
      const targets = getQuickPublishTargets(listing, enabledPlatforms);
      if (targets.length === 1 && targets[0] === 'ebay') {
        const result = await publishListingEbay(listing.id);
        if (result?.status === 'POSTED') {
          await refreshStatus(listing.id).catch(() => undefined);
          await reload();
          toast.success(`Published ${getListingTitle(listing)} to eBay.`);
          return;
        }
      }
      const { failed } = await queuePublishRows([listing]);
      if (failed) {
        toast.error(`${failed} publish action${failed === 1 ? '' : 's'} failed.`);
        return;
      }
      await reload();
      toast.success(`Queued ${getListingTitle(listing)} for publish${targets.length ? ` to ${targets.map(formatMarketplace).join(', ')}` : ''}.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Publish request failed.';
      toast.error(message);
    }
  };

  const toggleRow = (id) => {
    setSelectedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  };

  const queuePublishRows = async (rows) => {
    const publishableRows = rows.filter(Boolean);
    if (!publishableRows.length) {
      return { failed: 0, total: 0 };
    }

    const targetSets = publishableRows.map((listing) => {
      const targets = getQuickPublishTargets(listing, enabledPlatforms);
      return JSON.stringify(targets.length ? targets : ['ebay']);
    });
    const uniqueTargetSets = new Set(targetSets);
    if (uniqueTargetSets.size === 1) {
      const marketplaces = JSON.parse(targetSets[0]);
      const response = await publishListingsBulk(
        publishableRows.map((listing) => listing.id),
        marketplaces,
      );
      const failed = Array.isArray(response?.results)
        ? response.results.filter((item) => item && item.error).length
        : 0;
      return { failed, total: publishableRows.length };
    }

    const results = await Promise.allSettled(
      publishableRows.map((listing) => publish(
        listing.id,
        getQuickPublishTargets(listing, enabledPlatforms),
      )),
    );
    const failed = results.filter((result) => result.status === 'rejected').length;
    return { failed, total: publishableRows.length };
  };

  const publishSelected = async () => {
    if (!selectedPublishableRows.length) {
      toast.error(workflowPreferences.review_before_publish ? 'Select a ready listing, or approve the selected drafts first.' : 'No selected listings are publishable.');
      return;
    }

    const { failed, total } = await queuePublishRows(selectedPublishableRows);
    if (failed) {
      toast.error(`${failed} publish action${failed === 1 ? '' : 's'} failed.`);
    } else {
      toast.success(`Queued ${total} listing${total === 1 ? '' : 's'} for publish.`);
    }
    await reload();
  };

  const approveSelected = async () => {
    if (!selectedReviewRows.length) {
      toast.error('Select one or more draft/review listings first.');
      return;
    }
    await approveListingsBulk(selectedReviewRows.map((listing) => listing.id));
    await reload();
    toast.success(`Approved ${selectedReviewRows.length} listing${selectedReviewRows.length === 1 ? '' : 's'}.`);
    setSelectedIds([]);
  };

  const approveAndPublishSelected = async () => {
    if (!selectedRows.length) {
      toast.error('Select one or more listings first.');
      return;
    }
    const reviewIds = selectedRows
      .filter((listing) => getListingBucket(listing) === 'review' || listing.status === 'draft')
      .map((listing) => listing.id);
    if (reviewIds.length) {
      await approveListingsBulk(reviewIds);
    }
    const publishable = selectedRows.filter((listing) => {
      const bucket = getListingBucket(listing);
      return bucket === 'ready' || bucket === 'drafts' || bucket === 'review' || listing.status === 'draft';
    });
    if (!publishable.length) {
      toast.error('Select at least one listing that can move to publish.');
      return;
    }
    const { failed, total } = await queuePublishRows(publishable);
    await reload();
    toast.success(
      failed
        ? `Approved and queued ${total - failed} listing${total - failed === 1 ? '' : 's'} for publish; ${failed} failed.`
        : `Approved and queued ${total} listing${total === 1 ? '' : 's'} for publish.`,
    );
    setSelectedIds([]);
  };

  const confirmDeleteListings = (count) => {
    if (!count) return false;
    const noun = count === 1 ? 'listing' : 'listings';
    return window.confirm(
      `Delete ${count} ${noun}? This will permanently remove the listing record, marketplace rows, and attached media. This cannot be undone.`,
    );
  };

  const archiveListing = async (listingId, { silent = false } = {}) => {
    const listing = listings.find((row) => row.id === listingId);
    if (!listing) return;
    const currentLabels = Array.isArray(listing.custom_labels) ? listing.custom_labels : [];
    const labels = Array.from(new Set([...currentLabels, 'archived_vine']));
    await updateListing(listingId, {
      custom_labels: labels,
      needs_review: false,
      status: listing.status === 'draft' ? 'draft' : listing.status,
    });
    if (!silent) {
      await reload();
      toast.success('Listing archived from active review queues.');
    }
  };

  const unarchiveListing = async (listingId, { silent = false } = {}) => {
    const listing = listings.find((row) => row.id === listingId);
    if (!listing) return;
    const currentLabels = Array.isArray(listing.custom_labels) ? listing.custom_labels : [];
    const labels = currentLabels.filter((label) => label !== 'archived_vine');
    await updateListing(listingId, {
      custom_labels: labels,
      needs_review: true,
      status: listing.status || 'draft',
    });
    if (!silent) {
      await reload();
      toast.success('Listing restored to active review queues.');
    }
  };

  const archiveSelected = async () => {
    const targetRows = listings.filter(
      (listing) => selectedIds.includes(listing.id) && isAmazonVineSource(listing) && !isArchivedListing(listing),
    );
    if (!targetRows.length) {
      toast.error('Select one or more active Vine listings to archive.');
      return;
    }
    let archived = 0;
    let failed = 0;
    const chunkSize = 20;
    for (let i = 0; i < targetRows.length; i += chunkSize) {
      const chunk = targetRows.slice(i, i + chunkSize);
      const results = await Promise.allSettled(chunk.map((listing) => archiveListing(listing.id, { silent: true })));
      results.forEach((result) => {
        if (result.status === 'fulfilled') archived += 1;
        else failed += 1;
      });
    }
    setSelectedIds([]);
    await reload();
    if (failed) {
      toast.error(`Archived ${archived}, failed ${failed}. Please retry failed rows.`);
    } else {
      toast.success(`Archived ${archived} Vine listing${archived === 1 ? '' : 's'}.`);
    }
  };

  const unarchiveSelected = async () => {
    const targetRows = listings.filter((listing) => selectedIds.includes(listing.id) && isArchivedListing(listing));
    if (!targetRows.length) {
      toast.error('Select one or more archived listings to restore.');
      return;
    }
    let restored = 0;
    let failed = 0;
    const chunkSize = 20;
    for (let i = 0; i < targetRows.length; i += chunkSize) {
      const chunk = targetRows.slice(i, i + chunkSize);
      const results = await Promise.allSettled(chunk.map((listing) => unarchiveListing(listing.id, { silent: true })));
      results.forEach((result) => {
        if (result.status === 'fulfilled') restored += 1;
        else failed += 1;
      });
    }
    setSelectedIds([]);
    await reload();
    if (failed) {
      toast.error(`Restored ${restored}, failed ${failed}. Please retry failed rows.`);
    } else {
      toast.success(`Restored ${restored} archived listing${restored === 1 ? '' : 's'}.`);
    }
  };

  const deleteListing = async (listingId, { silent = false } = {}) => {
    await deleteListingApi(listingId);
    if (!silent) {
      setSelectedIds((current) => current.filter((id) => id !== listingId));
      if (selectedListingId === listingId) {
        setSelectedListingId(null);
      }
      await reload();
      toast.success('Listing deleted.');
    }
  };

  const deleteSelected = async () => {
    const targetRows = listings.filter((listing) => selectedIds.includes(listing.id));
    if (!targetRows.length) {
      toast.error('Select one or more listings to delete.');
      return;
    }
    if (!confirmDeleteListings(targetRows.length)) {
      return;
    }
    const deletedIds = targetRows.map((listing) => listing.id);
    await deleteListingsBulkApi(deletedIds);
    setSelectedIds([]);
    if (selectedListingId && deletedIds.includes(selectedListingId)) {
      setSelectedListingId(null);
    }
    await reload();
    toast.success(`Deleted ${targetRows.length} listing${targetRows.length === 1 ? '' : 's'}.`);
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
        description="Manage all listing drafts in one workspace: create, import, intake photos, preview per marketplace, approve, and publish."
        actions={
          <div className="flex flex-wrap gap-2">
            <Link href="/listings/new">
              <Button variant="outline">New item</Button>
            </Link>
            <Link href="/settings?tab=marketplaces">
              <Button variant="outline">Import listings</Button>
            </Link>
            <Link href="/intake">
              <Button>Import photos</Button>
            </Link>
            {user?.can_access_vine_import ? (
              <Link href="/imports/vine">
                <Button variant="outline">Import Vine data</Button>
              </Link>
            ) : null}
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
            {sourceFilter === 'amazon_vine' ? (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    const result = await backfillVineListingImages({
                      includeArchived: false,
                      forceRefresh: true,
                      strictMatch: true,
                    });
                    await reload();
                    toast.success(
                      `Vine image retry complete: ${Number(result?.updated || 0)} updated across ${Number(result?.processed || 0)} drafts, ${Number(result?.discovered || 0)} discovered, ${Number(result?.no_cache || 0)} still missing.`,
                    );
                  }}
                >
                  Bulk retry fetch images
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={async () => {
                    const result = await backfillVineListingImages({
                      includeArchived: false,
                      forceRefresh: true,
                      strictMatch: true,
                      onlyMissingImages: true,
                    });
                    await reload();
                    toast.success(
                      `Missing-image retry complete: ${Number(result?.updated || 0)} updated across ${Number(result?.processed || 0)} drafts, ${Number(result?.no_cache || 0)} still missing.`,
                    );
                  }}
                >
                  Retry missing Vine images
                </Button>
              </>
            ) : null}
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

      <section aria-label="Listing queues">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[#101828]">Listing Queues</h2>
          <p className="text-xs text-[#667085]">Review, draft, ready, published, failed, and Vine workflows.</p>
        </div>
        <Tabs
        className="hidden md:flex"
        items={LISTING_TABS.map((tab) => ({ ...tab, count: tabCounts[tab.value] || 0 }))}
        value={activeTab}
        onChange={selectTab}
        />
      </section>

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

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Draft value" value={`$${Math.round(listingMetrics.draftAmount).toLocaleString()}`} detail={`${listingMetrics.draftCount} drafts pending review/publish`} />
        <MetricCard label="Review queue value" value={`$${Math.round(listingMetrics.reviewAmount).toLocaleString()}`} detail={`${listingMetrics.reviewCount} listings needing approval`} />
        <MetricCard label="Ready value" value={`$${Math.round(listingMetrics.readyAmount).toLocaleString()}`} detail={`${listingMetrics.readyCount} listings ready to publish`} />
        <MetricCard
          label="Marketplace split"
          value={Object.keys(listingMetrics.byMarket).length || 0}
          detail={Object.entries(listingMetrics.byMarket)
            .sort((a, b) => b[1].count - a[1].count)
            .slice(0, 2)
            .map(([market, stats]) => `${formatMarketplace(market)} ${stats.count} · $${Math.round(stats.amount).toLocaleString()}`)
            .join(' | ') || 'No market targets yet'}
        />
      </section>

      <SectionPanel
        id="listing-publish-queue"
        title="Publish queue progress"
        description="Live worker state for queued, running, completed, and failed publish jobs."
        action={<Link href="/jobs" className="text-sm font-medium text-[#2563eb]">Open jobs console</Link>}
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Queued / running" value={publishJobStats.queued} detail="Jobs still moving through the worker." />
          <MetricCard label="Completed" value={publishJobStats.completed} detail="Publish jobs that finished successfully." />
          <MetricCard label="Failed" value={publishJobStats.failed} detail="Jobs that need attention or retry." />
          <MetricCard label="Progress" value={`${publishJobStats.progress}%`} detail={`${publishJobStats.completed} of ${publishJobStats.total} publish jobs completed`} />
        </div>
      </SectionPanel>

      {selectedIds.length ? (
        <ActionBar
          left={<p className="text-sm font-medium text-[#101828]">{selectedIds.length} selected</p>}
        right={<div className="flex flex-wrap gap-2">
          {workflowPreferences.bulk_approval_enabled ? (
            <Button variant="outline" size="sm" onClick={approveSelected}>
              Approve selected
            </Button>
          ) : null}
          <Button variant="outline" size="sm" onClick={approveAndPublishSelected}>
            Approve &amp; publish selected
          </Button>
          {activeTab !== 'archived' ? (
            <Button variant="outline" size="sm" onClick={archiveSelected}>
              Archive selected
            </Button>
          ) : (
            <Button variant="outline" size="sm" onClick={unarchiveSelected}>
              Unarchive selected
            </Button>
          )}
          <Button variant="danger" size="sm" onClick={deleteSelected}>
            Delete selected
          </Button>
          <Button variant="outline" size="sm" onClick={() => setSelectedIds([])}>
            Clear
          </Button>
          <Button size="sm" onClick={publishSelected}>
            Publish selected
            </Button>
          </div>}
        />
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
                  {getListingThumbnail(listing) ? (
                    <img src={toPublicImageUrl(getListingThumbnail(listing))} alt={getListingTitle(listing)} className="h-full w-full object-cover" />
                  ) : null}
                </div>
              ),
            },
            {
              key: 'title',
              label: 'Title',
              cellClassName: 'min-w-[280px]',
              render: (listing) => (
                <div>
                  <p className="truncate font-medium text-[#101828]">{getListingTitle(listing)}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <p className="text-xs text-[#667085]">#{listing.id}</p>
                    {listing.sku ? <span className="pp-chip">SKU {listing.sku}</span> : null}
                    {isAmazonVineSource(listing) ? <span className="pp-chip">Vine</span> : null}
                    <span className="pp-chip">{getListingImageCount(listing)} image{getListingImageCount(listing) === 1 ? '' : 's'}</span>
                    {listing.needs_review ? <span className="pp-chip">Needs Review</span> : null}
                    {listing.restricted_review_required ? <span className="pp-chip">Restricted Review</span> : null}
                    {listing.custom_labels?.includes('needs_photos') ? <span className="pp-chip">Image Missing</span> : null}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={(event) => {
                        event.stopPropagation();
                        router.push(`/listings/${listing.id}?mode=preview`);
                      }}
                    >
                      Preview/Edit
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={(event) => {
                        event.stopPropagation();
                        router.push(`/listings/${listing.id}`);
                      }}
                    >
                      Full page
                    </Button>
                    {(getListingBucket(listing) === 'ready' || getListingBucket(listing) === 'drafts' || listing.status === 'draft') ? (
                      <Button
                        size="sm"
                        onClick={(event) => {
                          publishSingleListing(listing, event);
                        }}
                        disabled={publishing[listing.id]}
                      >
                        {publishing[listing.id] ? 'Publishing…' : 'Publish'}
                      </Button>
                    ) : null}
                    {getListingBucket(listing) === 'review' || listing.status === 'draft' ? (
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
                      variant="danger"
                      size="sm"
                      onClick={async (event) => {
                        event.stopPropagation();
                        if (!confirmDeleteListings(1)) return;
                        await deleteListing(listing.id);
                      }}
                    >
                      Delete
                    </Button>
                    {isAmazonVineSource(listing) && !isArchivedListing(listing) ? (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={async (event) => {
                          event.stopPropagation();
                          await archiveListing(listing.id);
                        }}
                      >
                        Archive
                      </Button>
                    ) : null}
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
              key: 'images',
              label: 'Images',
              render: (listing) => `${getListingImageCount(listing)} image${getListingImageCount(listing) === 1 ? '' : 's'}`,
            },
            {
              key: 'marketplaces',
              label: 'Marketplaces',
              render: (listing) => (
                <div className="flex flex-wrap gap-2">
                  {(getListingMarketplaces(listing, enabledPlatforms, { allowFallback: false }).length
                    ? getListingMarketplaces(listing, enabledPlatforms, { allowFallback: false })
                    : ['unassigned']).map((marketplace) => (
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
                    {errors[listing.id] ? (
                      <p className="mt-1 text-xs text-[#b42318]">
                        {isEbayReconnectRequiredError(errors[listing.id])
                          ? 'eBay token invalid. Reconnect eBay in Settings, then retry publish.'
                          : errors[listing.id]}
                      </p>
                    ) : getListingFailureMessage(listing) ? (
                      <p className="mt-1 text-xs text-[#b42318]">{getListingFailureMessage(listing)}</p>
                    ) : null}
                  </div>
                );
              },
            },
            {
              key: 'updated',
              label: 'Updated',
              render: (listing) => new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(new Date(listing.updated_at || listing.created_at || Date.now())),
            },
          ]}
          rows={filteredListings}
          rowKey={(row) => row.id}
          selectedRows={selectedIds}
          onToggleRow={toggleRow}
          allSelected={filteredListings.length > 0 && selectedIds.length === filteredListings.length}
          onToggleAll={() => setSelectedIds(selectedIds.length === filteredListings.length ? [] : filteredListings.map((listing) => listing.id))}
          onRowClick={(listing) => setSelectedListingId(listing.id)}
          emptyState={<EmptyState title={`No ${LISTING_TABS.find((t) => t.value === activeTab)?.label || 'listings'} found`} description="Adjust the current filters or import new inventory to create drafts." className="border-0 p-0 py-6" />}
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {filteredListings.length ? (
              filteredListings.map((listing) => {
                const bucket = getListingBucket(listing);
                const explicitMarketplaces = getListingMarketplaces(listing, enabledPlatforms, { allowFallback: false });
                const canPublish = workflowPreferences.review_before_publish ? bucket === 'ready' : bucket === 'drafts' || bucket === 'ready';
                const selected = selectedIds.includes(listing.id);
                return (
                  <div
                    key={listing.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedListingId(listing.id)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        setSelectedListingId(listing.id);
                      }
                    }}
                    className={`rounded-[16px] border bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff] ${
                      selected ? 'border-[#bfd2ff] ring-2 ring-[#dbe7ff]' : 'border-[#e5e7eb]'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <div className="h-16 w-16 overflow-hidden rounded-[14px] bg-[#f2f4f7]">
                          {getListingThumbnail(listing) ? <img src={toPublicImageUrl(getListingThumbnail(listing))} alt={getListingTitle(listing)} className="h-full w-full object-cover" /> : null}
                        </div>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-[#101828]">{getListingTitle(listing)}</p>
                          <p className="mt-1 text-xs text-[#667085]">#{listing.id}{listing.sku ? ` · SKU ${listing.sku}` : ''}</p>
                          <p className="mt-1 text-xs text-[#667085]">{getListingImageCount(listing)} image{getListingImageCount(listing) === 1 ? '' : 's'}</p>
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
                      {(explicitMarketplaces.length ? explicitMarketplaces : ['unassigned']).map((marketplace) => (
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
                      {(bucket === 'review' || listing.status === 'draft') ? (
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
                      {isAmazonVineSource(listing) && !isArchivedListing(listing) ? (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={async (event) => {
                            event.stopPropagation();
                            await archiveListing(listing.id);
                          }}
                        >
                          Archive
                        </Button>
                      ) : null}
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={async (event) => {
                          event.stopPropagation();
                          if (!confirmDeleteListings(1)) return;
                          await deleteListing(listing.id);
                        }}
                      >
                        Delete
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={(event) => {
                          event.stopPropagation();
                          router.push(`/listings/${listing.id}?mode=preview`);
                        }}
                      >
                        Preview/Edit
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={(event) => {
                          event.stopPropagation();
                          router.push(`/listings/${listing.id}`);
                        }}
                      >
                        Full page
                      </Button>
                      {canPublish ? (
                        <Button
                          size="sm"
                          onClick={async (event) => {
                            publishSingleListing(listing, event);
                          }}
                          disabled={publishing[listing.id]}
                        >
                          {publishing[listing.id] ? 'Publishing…' : 'Publish'}
                        </Button>
                      ) : null}
                      {getListingBucket(listing) === 'failed' ? (
                        <p className="mt-1 text-xs text-[#b42318]">
                          {getListingFailureMessage(listing) || errors[listing.id] || 'Publish failed'}
                        </p>
                      ) : null}
                    </div>
                  </div>
                );
              })
            ) : (
              <EmptyState title={`No ${LISTING_TABS.find((t) => t.value === activeTab)?.label || 'listings'} found`} description="Adjust the current filters or import new inventory to create drafts." />
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
              crosspostPreview={crosspostPreview}
              crosspostPreviewLoading={crosspostPreviewLoading}
              publishState={{
                loading: !!publishing[selectedListing.id],
                error: errors[selectedListing.id] || '',
              }}
              onApprove={async (id) => {
                await approveListing(id);
              }}
              onApproveAndNext={async (id) => {
                await approveListingAndMoveNext(id);
              }}
              onDelete={async (id) => {
                if (!confirmDeleteListings(1)) return;
                await deleteListing(id);
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
