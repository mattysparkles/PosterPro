import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
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
  applyPricingRecommendation,
  bulkPricingAction,
  createListingTemplate,
  fetchCrosspostPreview,
  fetchMarketplaceJobsOverview,
  fetchPricingRecommendation,
  fetchListingIntelligence,
  fetchMarketplacePayloadPreview,
  fetchMarketplacePreflight,
  fetchSettingsPanels,
  refreshPricingRecommendation,
  backfillVineListingImages,
  deleteListing as deleteListingApi,
  deleteListingsBulk as deleteListingsBulkApi,
  generateListing,
  processListingPhoto,
  toPublicImageUrl,
  approveListing as approveListingApi,
  approveListingsBulk,
  exportMarketplacePreflightCsv,
  fetchEbayAccountReadiness,
  fetchEbayLaunchRepairQueue,
  fetchLaunchCandidates,
  fetchMarketplacePreflightBulk,
  approveListingPhotos,
  publishListingsBulk,
  publishMarketplaceReadyBulk,
  rejectListingPhotos,
  runLaunchDrillDryRun,
  setPrimaryListingPhoto,
  syncEbayListing,
  applyEbayLaunchRepair,
  publishListingEbay,
  toggleAutonomousMode,
  uploadListingPhotos,
  updateListing,
} from '../lib/api';

const LISTING_TABS = [
  { value: 'review', label: 'Needs Review' },
  { value: 'drafts', label: 'Drafts' },
  { value: 'ready', label: 'Ready' },
  { value: 'published', label: 'Published' },
  { value: 'sold', label: 'Sold' },
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

const READINESS_FILTER_OPTIONS = [
  { value: 'all', label: 'All readiness states' },
  { value: 'missing_photos', label: 'Missing actual photos' },
  { value: 'reference_only', label: 'Reference-only images' },
  { value: 'missing_weight', label: 'Missing weight' },
  { value: 'missing_dimensions', label: 'Missing dimensions' },
  { value: 'missing_condition', label: 'Missing condition review' },
  { value: 'missing_category', label: 'Missing category' },
  { value: 'missing_price', label: 'Missing price' },
  { value: 'weak_pricing', label: 'Weak pricing confidence' },
  { value: 'stale_pricing', label: 'Stale pricing' },
  { value: 'ready_for_ebay', label: 'Ready for eBay' },
  { value: 'ready_for_facebook', label: 'Ready for Facebook' },
  { value: 'ebay_ready', label: 'eBay ready' },
  { value: 'ebay_blocked', label: 'eBay blocked' },
  { value: 'ebay_warning_only', label: 'eBay warning-only' },
  { value: 'ebay_missing_category', label: 'eBay missing category' },
  { value: 'ebay_missing_aspects', label: 'eBay missing aspects' },
  { value: 'ebay_missing_policies', label: 'eBay missing policies' },
  { value: 'ebay_missing_shipping', label: 'eBay missing shipping' },
  { value: 'ebay_missing_photos', label: 'eBay missing photos' },
  { value: 'facebook_ready', label: 'Facebook ready' },
  { value: 'facebook_blocked', label: 'Facebook blocked' },
  { value: 'facebook_warning_only', label: 'Facebook warning-only' },
  { value: 'facebook_missing_photos', label: 'Facebook missing photos' },
  { value: 'facebook_missing_price', label: 'Facebook missing price' },
  { value: 'facebook_missing_category', label: 'Facebook missing category' },
  { value: 'high_confidence_ready', label: 'High-confidence ready' },
  { value: 'likely_low_value', label: 'Likely low value' },
  { value: 'oversize_low_margin', label: 'Oversize / margin risk' },
  { value: 'ready_except_shipping', label: 'Ready except shipping' },
  { value: 'ready_except_photos', label: 'Ready except photos' },
  { value: 'ready_except_policies', label: 'Ready except policies' },
];

function isAmazonVineSource(listing) {
  const source = String(listing?.source_type || '').toLowerCase();
  const hint = String(listing?.source || listing?.ingest_source || listing?.marketplace_source || '').toLowerCase();
  return source === 'amazon_vine' || hint.includes('vine');
}

function isArchivedListing(listing) {
  return (listing.custom_labels || []).some((label) => ['archived_vine', 'archived_sold'].includes(label)) || listing.status === 'rejected';
}

function isSoldListing(listing) {
  return Boolean(listing?.sold_at) || Number(listing?.quantity ?? 1) <= 0;
}

function getListingThumbnail(listing) {
  const sourceMetadata = listing?.source_metadata || {};
  if (Array.isArray(listing?.listing_images) && listing.listing_images.length) {
    const approved = listing.listing_images.filter((image) => image?.operator_state !== 'rejected');
    const actual = approved.find((image) => image?.role === 'primary' && !image?.is_reference)
      || approved.find((image) => !image?.is_reference)
      || approved.find((image) => image?.role === 'primary')
      || approved[0];
    if (actual?.storage_path) {
      return actual.storage_path;
    }
  }
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
  if (isSoldListing(listing)) return 'sold';
  if (isArchivedListing(listing)) return 'archived';
  if (listing.status === 'draft') return 'drafts';
  if (listing.status === 'error' || listing.ebay_publish_status === 'FAILED') return 'failed';
  if (listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id) return 'published';
  if (listing.status === 'ready') return 'ready';
  if (listing.restricted_review_required || listing.needs_review) return 'review';
  return 'review';
}

function matchesTab(listing, tab) {
  if (tab === 'sold') return isSoldListing(listing);
  if (isSoldListing(listing)) return false;
  if (tab === 'archived') return isArchivedListing(listing);
  if (isArchivedListing(listing)) return false;
  if (tab === 'drafts') return listing.status === 'draft';
  if (tab === 'review') return Boolean((listing.needs_review || listing.restricted_review_required) && listing.status !== 'ready' && !(listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id));
  if (tab === 'ready') return Boolean(listing.status === 'ready' && !(listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id));
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
  if (Array.isArray(listing?.listing_images) && listing.listing_images.length) {
    return listing.listing_images.filter((image) => image?.operator_state !== 'rejected').length;
  }
  const sourceMetadata = listing?.source_metadata || {};
  const urls = [
    ...(Array.isArray(listing?.image_urls) ? listing.image_urls : []),
    ...(Array.isArray(sourceMetadata?.image_urls) ? sourceMetadata.image_urls : []),
    ...(Array.isArray(sourceMetadata?.original_image_urls) ? sourceMetadata.original_image_urls : []),
  ];
  return new Set(urls.filter(Boolean)).size;
}

function getReadinessSummary(listing) {
  return listing?.readiness_summary || {};
}

function getMarketplacePreflightSummary(listing, marketplace) {
  const root = listing?.marketplace_preflight_summary || listing?.marketplace_data?.marketplace_preflight || {};
  const byMarketplace = root?.by_marketplace || root;
  if (!byMarketplace || typeof byMarketplace !== 'object') return null;
  return byMarketplace[marketplace] || null;
}

function getMarketplacePreflightStatus(listing, marketplace) {
  const summary = getMarketplacePreflightSummary(listing, marketplace);
  if (!summary) return 'not_checked';
  const status = String(summary.status || '').toLowerCase();
  if (!status) return 'not_checked';
  return status;
}

function getMarketplacePreflightAgeLabel(summary) {
  if (!summary?.last_checked_at) return 'not checked';
  const checkedAt = new Date(summary.last_checked_at);
  if (Number.isNaN(checkedAt.getTime())) return 'not checked';
  const deltaMs = Date.now() - checkedAt.getTime();
  if (deltaMs < 60_000) return 'just now';
  const minutes = Math.round(deltaMs / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function getMarketplacePreflightTone(summary) {
  const status = String(summary?.status || '').toLowerCase();
  if (!summary) return 'info';
  if (status === 'blocked' || (summary.blocker_count || 0) > 0) return 'danger';
  if (status === 'ready_with_warnings' || (summary.warning_count || 0) > 0) return 'warning';
  if (status === 'needs_review') return 'warning';
  if (status === 'ready' || status === 'published') return 'success';
  if (status === 'failed') return 'danger';
  return 'info';
}

function getMarketplaceTopBlocker(summary) {
  if (!summary) return '';
  return summary.top_blocker_code || summary.blocker_codes?.[0] || summary.top_blocker_message || '';
}

function getMarketplaceTopWarning(summary) {
  if (!summary) return '';
  return summary.top_warning_code || summary.warning_codes?.[0] || summary.top_warning_message || '';
}

function isMarketplaceReady(summary) {
  return Boolean(summary && ['ready', 'ready_with_warnings', 'published'].includes(String(summary.status || '').toLowerCase()));
}

function isMarketplaceBlocked(summary) {
  return Boolean(summary && (String(summary.status || '').toLowerCase() === 'blocked' || (summary.blocker_count || 0) > 0));
}

function isMarketplaceWarningOnly(summary) {
  if (!summary) return false;
  const status = String(summary.status || '').toLowerCase();
  return status === 'ready_with_warnings' || (!(summary.blocker_count || 0) && (summary.warning_count || 0) > 0);
}

function getBulkScopeRows(selectedRows, filteredListings) {
  return selectedRows.length ? selectedRows : filteredListings;
}

function summarizeBlockerCodes(rows, marketplace = null) {
  const counter = new Map();
  rows.forEach((listing) => {
    const markets = marketplace ? [marketplace] : ['ebay', 'facebook'];
    markets.forEach((market) => {
      const summary = getMarketplacePreflightSummary(listing, market);
      if (!summary) return;
      (summary.blocker_codes || []).forEach((code) => {
        if (!code) return;
        counter.set(code, (counter.get(code) || 0) + 1);
      });
    });
  });
  return Array.from(counter.entries()).sort((a, b) => b[1] - a[1]);
}

function matchesReadinessFilter(listing, filterValue) {
  if (filterValue === 'all') return true;
  const summary = getReadinessSummary(listing);
  const shippingChecklist = summary.shipping_checklist || {};
  const conditionData = listing?.condition_data || {};
  const pricing = listing?.marketplace_data?.pricing_analysis || {};
  const quality = listing?.quality_summary || {};
  const ebayPreflight = getMarketplacePreflightSummary(listing, 'ebay');
  const facebookPreflight = getMarketplacePreflightSummary(listing, 'facebook');
  switch (filterValue) {
    case 'missing_photos':
      return Boolean(summary.images_missing || summary.manual_photo_needed);
    case 'reference_only':
      return Boolean(summary.reference_image_count) && !summary.actual_image_count;
    case 'missing_weight':
      return !shippingChecklist.weight_present;
    case 'missing_dimensions':
      return !shippingChecklist.package_dimensions_present;
    case 'missing_condition':
      return Boolean(conditionData.operator_review_required) || !listing?.condition;
    case 'missing_category':
      return !(listing?.category_id || listing?.category_suggestion);
    case 'missing_price':
      return !(listing?.listing_price || listing?.suggested_price);
    case 'weak_pricing':
      return Number(pricing.price_confidence || pricing.confidence || 0) < 0.45;
    case 'stale_pricing':
      return Boolean(pricing.stale);
    case 'ready_for_ebay':
      return Boolean(quality.ready_for_ebay);
    case 'ready_for_facebook':
      return Boolean(quality.ready_for_facebook);
    case 'high_confidence_ready':
      return Boolean(quality.ready_for_publish_queue) && Number(pricing.price_confidence || pricing.confidence || 0) >= 0.7;
    case 'likely_low_value':
      return Number(pricing.recommended_price || listing?.listing_price || 0) > 0 && Number(pricing.recommended_price || listing?.listing_price || 0) <= 20;
    case 'oversize_low_margin':
      return Boolean(listing?.shipping_profile?.oversize || listing?.shipping_profile?.local_pickup_recommended)
        && Number(pricing.recommended_price || listing?.listing_price || 0) <= 40;
    case 'ebay_ready':
      return isMarketplaceReady(ebayPreflight);
    case 'ebay_blocked':
      return isMarketplaceBlocked(ebayPreflight);
    case 'ebay_warning_only':
      return isMarketplaceWarningOnly(ebayPreflight);
    case 'ebay_missing_category':
      return Boolean(!ebayPreflight || (ebayPreflight.missing_fields || []).some((field) => String(field).includes('category')));
    case 'ebay_missing_aspects':
      return Boolean(ebayPreflight && (ebayPreflight.blocker_codes || []).some((code) => code === 'EBAY_REQUIRED_ASPECT_MISSING'));
    case 'ebay_missing_policies':
      return Boolean(ebayPreflight && (ebayPreflight.blocker_codes || []).some((code) => String(code).includes('POLICY')));
    case 'ebay_missing_shipping':
      return Boolean(ebayPreflight && (ebayPreflight.blocker_codes || []).some((code) => String(code).includes('SHIPPING') || String(code).includes('WEIGHT') || String(code).includes('DIMENSIONS')));
    case 'ebay_missing_photos':
      return Boolean(ebayPreflight && (ebayPreflight.blocker_codes || []).some((code) => String(code).includes('PHOTOS') || String(code).includes('IMAGE')));
    case 'facebook_ready':
      return isMarketplaceReady(facebookPreflight);
    case 'facebook_blocked':
      return isMarketplaceBlocked(facebookPreflight);
    case 'facebook_warning_only':
      return isMarketplaceWarningOnly(facebookPreflight);
    case 'facebook_missing_photos':
      return Boolean(facebookPreflight && (facebookPreflight.blocker_codes || []).some((code) => String(code).includes('PHOTOS')));
    case 'facebook_missing_price':
      return Boolean(facebookPreflight && (facebookPreflight.blocker_codes || []).some((code) => String(code).includes('PRICE')));
    case 'facebook_missing_category':
      return Boolean(facebookPreflight && (facebookPreflight.blocker_codes || []).some((code) => String(code).includes('CATEGORY')));
    case 'ready_except_shipping':
      return Boolean((isMarketplaceReady(ebayPreflight) || isMarketplaceReady(facebookPreflight)) && ((ebayPreflight && (ebayPreflight.blocker_codes || []).some((code) => String(code).includes('SHIPPING') || String(code).includes('WEIGHT') || String(code).includes('DIMENSIONS'))) || (facebookPreflight && (facebookPreflight.blocker_codes || []).some((code) => String(code).includes('SHIPPING') || String(code).includes('WEIGHT') || String(code).includes('DIMENSIONS')))));
    case 'ready_except_photos':
      return Boolean((isMarketplaceReady(ebayPreflight) || isMarketplaceReady(facebookPreflight)) && ((ebayPreflight && (ebayPreflight.blocker_codes || []).some((code) => String(code).includes('PHOTOS') || String(code).includes('IMAGE'))) || (facebookPreflight && (facebookPreflight.blocker_codes || []).some((code) => String(code).includes('PHOTOS') || String(code).includes('IMAGE')))));
    case 'ready_except_policies':
      return Boolean((isMarketplaceReady(ebayPreflight) || isMarketplaceReady(facebookPreflight)) && ((ebayPreflight && (ebayPreflight.blocker_codes || []).some((code) => String(code).includes('POLICY'))) || (facebookPreflight && (facebookPreflight.blocker_codes || []).some((code) => String(code).includes('POLICY')))));
    default:
      return true;
  }
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
  const [readinessFilter, setReadinessFilter] = useState('all');
  const [selectedIds, setSelectedIds] = useState([]);
  const [selectedListingId, setSelectedListingId] = useState(null);
  const [viewMode, setViewMode] = useState('table');
  const [pricingRecommendation, setPricingRecommendation] = useState(null);
  const [listingIntelligence, setListingIntelligence] = useState(null);
  const [crosspostPreview, setCrosspostPreview] = useState([]);
  const [crosspostPreviewLoading, setCrosspostPreviewLoading] = useState(false);
  const [marketplacePreflights, setMarketplacePreflights] = useState({});
  const [marketplacePayloadPreviews, setMarketplacePayloadPreviews] = useState({});
  const [jobsOverview, setJobsOverview] = useState({ import_jobs: [], crosspost_jobs: [] });
  const [bulkPreflightReport, setBulkPreflightReport] = useState(null);
  const [bulkPreflightLoading, setBulkPreflightLoading] = useState(false);
  const [bulkPublishReport, setBulkPublishReport] = useState(null);
  const [launchCandidatesReport, setLaunchCandidatesReport] = useState(null);
  const [launchCandidatesLoading, setLaunchCandidatesLoading] = useState(false);
  const [ebayAccountReadiness, setEbayAccountReadiness] = useState(null);
  const [repairQueueReport, setRepairQueueReport] = useState(null);
  const [repairQueueLoading, setRepairQueueLoading] = useState(false);
  const [repairQueueImageStatusFilter, setRepairQueueImageStatusFilter] = useState('all');
  const [workflowPreferences, setWorkflowPreferences] = useState({
    review_before_publish: true,
    auto_publish_after_approval: false,
    bulk_approval_enabled: true,
    listing_preview_mode: 'marketplace',
  });

  const loadMarketplacePreviewData = useCallback(async (listingId) => {
    if (!listingId) return;
    try {
      const [ebayPreflight, facebookPreflight, ebayPayload, facebookPayload] = await Promise.all([
        fetchMarketplacePreflight(listingId, 'ebay'),
        fetchMarketplacePreflight(listingId, 'facebook'),
        fetchMarketplacePayloadPreview(listingId, 'ebay'),
        fetchMarketplacePayloadPreview(listingId, 'facebook'),
      ]);
      setMarketplacePreflights({
        ebay: ebayPreflight,
        facebook: facebookPreflight,
      });
      setMarketplacePayloadPreviews({
        ebay: ebayPayload,
        facebook: facebookPayload,
      });
    } catch {
      setMarketplacePreflights({});
      setMarketplacePayloadPreviews({});
    }
  }, []);

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
    if (!user?.id) return;
    fetchEbayAccountReadiness()
      .then((data) => setEbayAccountReadiness(data))
      .catch(() => setEbayAccountReadiness(null));
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
      const matchesReadiness = matchesReadinessFilter(listing, readinessFilter);
      return matchesTab(listing, activeTab) && matchesSearch && matchesMarket && matchesSource && matchesReadiness;
    });
  }, [activeTab, enabledPlatforms, listings, marketFilter, readinessFilter, search, sourceFilter]);

  const baseFilteredListings = useMemo(() => {
    return listings.filter((listing) => {
      const text = `${getListingTitle(listing)} ${listing.id}`.toLowerCase();
      const marketplaces = getListingMarketplaces(listing, enabledPlatforms, { allowFallback: false });
      const matchesSearch = !search || text.includes(search.toLowerCase());
      const matchesMarket = marketFilter === 'all' || marketplaces.includes(marketFilter);
      const matchesSource = sourceFilter === 'all' || (sourceFilter === 'amazon_vine' ? isAmazonVineSource(listing) : listing.source_type === sourceFilter);
      const matchesReadiness = matchesReadinessFilter(listing, readinessFilter);
      return matchesSearch && matchesMarket && matchesSource && matchesReadiness;
    });
  }, [enabledPlatforms, listings, marketFilter, readinessFilter, search, sourceFilter]);

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
        return bucket === 'ready' && !(listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id);
      }
      return bucket === 'drafts' || bucket === 'ready';
    });
  }, [selectedRows, workflowPreferences.review_before_publish]);

  const selectedNeedsApprovalRows = useMemo(() => {
    return selectedRows.filter((listing) => getListingBucket(listing) === 'review' || listing.status === 'draft');
  }, [selectedRows]);

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
      blockedCount: baseFilteredListings.filter((listing) => (listing.readiness_summary?.blockers || []).length > 0).length,
      weakPricingCount: baseFilteredListings.filter((listing) => Number(listing?.marketplace_data?.pricing_analysis?.price_confidence || listing?.marketplace_data?.pricing_analysis?.confidence || 0) < 0.45).length,
      stalePricingCount: baseFilteredListings.filter((listing) => Boolean(listing?.marketplace_data?.pricing_analysis?.stale)).length,
      readyForEbayCount: baseFilteredListings.filter((listing) => Boolean(listing?.quality_summary?.ready_for_ebay)).length,
      byMarket,
    };
  }, [baseFilteredListings, enabledPlatforms]);

  const bulkScopeRows = useMemo(() => (selectedRows.length ? selectedRows : filteredListings), [filteredListings, selectedRows]);
  const bulkPreflightSummary = useMemo(() => {
    const ebayReadyCount = bulkScopeRows.filter((listing) => isMarketplaceReady(getMarketplacePreflightSummary(listing, 'ebay'))).length;
    const facebookReadyCount = bulkScopeRows.filter((listing) => isMarketplaceReady(getMarketplacePreflightSummary(listing, 'facebook'))).length;
    const blockedCount = bulkScopeRows.filter((listing) => isMarketplaceBlocked(getMarketplacePreflightSummary(listing, 'ebay')) || isMarketplaceBlocked(getMarketplacePreflightSummary(listing, 'facebook'))).length;
    const warningOnlyCount = bulkScopeRows.filter((listing) => {
      const ebay = getMarketplacePreflightSummary(listing, 'ebay');
      const facebook = getMarketplacePreflightSummary(listing, 'facebook');
      return (isMarketplaceWarningOnly(ebay) || isMarketplaceWarningOnly(facebook)) && !isMarketplaceBlocked(ebay) && !isMarketplaceBlocked(facebook);
    }).length;
    const checkedCount = bulkScopeRows.filter((listing) => getMarketplacePreflightSummary(listing, 'ebay') || getMarketplacePreflightSummary(listing, 'facebook')).length;
    const readyToQueueCount = bulkScopeRows.filter((listing) => Boolean(listing?.quality_summary?.ready_for_publish_queue)).length;
    const common = summarizeBlockerCodes(bulkScopeRows);
    return {
      selectedCount: selectedRows.length,
      checkedCount,
      ebayReadyCount,
      facebookReadyCount,
      blockedCount,
      warningOnlyCount,
      readyToQueueCount,
      mostCommonBlocker: common[0]?.[0] || '',
      mostCommonBlockerCount: common[0]?.[1] || 0,
    };
  }, [bulkScopeRows, selectedRows.length]);

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
    loadMarketplacePreviewData(selectedListingId);
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
  }, [enabledPlatforms, loadMarketplacePreviewData, refreshStatus, selectedListingId]);

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

  const liveEbayConfirmationPhrase = 'QUEUE LIVE EBAY READY LISTINGS';

  const confirmLiveEbayQueue = ({ count, marketplaces, allowWarnings, skipAlreadyQueued, context = 'queue live eBay listings' }) => {
    const marketLabel = Array.isArray(marketplaces) && marketplaces.length ? marketplaces.join(', ') : 'ebay';
    const message = [
      `You are about to LIVE QUEUE ${count} listing${count === 1 ? '' : 's'} for ${marketLabel.toUpperCase()}.`,
      `This can publish real eBay listings.`,
      `Warnings allowed: ${allowWarnings ? 'yes' : 'no'}.`,
      `Skip already queued: ${skipAlreadyQueued ? 'yes' : 'no'}.`,
      `Confirmation phrase required: ${liveEbayConfirmationPhrase}`,
      ``,
      `Type exactly: ${liveEbayConfirmationPhrase}`,
      context ? `Context: ${context}` : '',
    ].filter(Boolean).join('\n');
    const typed = window.prompt(message, '');
    return String(typed || '').trim() === liveEbayConfirmationPhrase;
  };

  const syncListingWithEbay = async (listingId) => {
    const listing = listings.find((row) => row.id === listingId);
    if (!listing?.ebay_listing_id) {
      toast.error('This listing does not have an eBay listing id yet.');
      return null;
    }
    const confirmed = confirmLiveEbayQueue({
      count: 1,
      marketplaces: ['ebay'],
      allowWarnings: false,
      skipAlreadyQueued: false,
      context: `Push listing #${listingId} updates back to eBay`,
    });
    if (!confirmed) {
      toast.error('Live eBay update canceled.');
      return null;
    }
    try {
      const result = await syncEbayListing(listingId, {
        confirm_live_publish: true,
        confirmation_phrase: liveEbayConfirmationPhrase,
      });
      await reload();
      if (selectedListingId === listingId) {
        await loadMarketplacePreviewData(listingId);
      }
      toast.success(`Pushed updates for ${getListingTitle(listing)} to eBay.`);
      return result;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'eBay sync failed.');
      return null;
    }
  };

  const publishSingleListing = async (listing, event) => {
    event?.stopPropagation?.();
    try {
      const targets = getQuickPublishTargets(listing, enabledPlatforms);
      if (targets.length === 1 && targets[0] === 'ebay') {
        if (!confirmLiveEbayQueue({ count: 1, marketplaces: ['ebay'], allowWarnings: false, skipAlreadyQueued: false, context: `Listing #${listing.id} · ${getListingTitle(listing)}` })) {
          toast.error('Live eBay publish canceled.');
          return;
        }
        const result = await publishListingEbay(listing.id, {
          confirm_live_publish: true,
          confirmation_phrase: liveEbayConfirmationPhrase,
        });
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
      if (marketplaces.includes('ebay')) {
        const confirmed = confirmLiveEbayQueue({
          count: publishableRows.length,
          marketplaces,
          allowWarnings: false,
          skipAlreadyQueued: true,
          context: `Bulk publish ${publishableRows.length} row${publishableRows.length === 1 ? '' : 's'}`,
        });
        if (!confirmed) {
          toast.error('Live eBay publish canceled.');
          return { failed: 0, total: publishableRows.length, canceled: true };
        }
      }
      const response = await publishListingsBulk(
        publishableRows.map((listing) => listing.id),
        marketplaces,
        marketplaces.includes('ebay')
          ? {
              confirm_live_publish: true,
              confirmation_phrase: liveEbayConfirmationPhrase,
            }
          : {},
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
    if (!selectedRows.length) {
      toast.error('Select one or more listings first.');
      return;
    }

    if (workflowPreferences.review_before_publish && selectedNeedsApprovalRows.length) {
      await approveAndPublishSelected();
      return;
    }

    if (!selectedPublishableRows.length) {
      toast.error('No selected listings are publishable.');
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

  const updateSelectedListings = async (updater, successMessage) => {
    const targetRows = listings.filter((listing) => selectedIds.includes(listing.id));
    if (!targetRows.length) {
      toast.error('Select one or more listings first.');
      return;
    }
    const results = await Promise.allSettled(
      targetRows.map((listing) => updateListing(listing.id, updater(listing))),
    );
    const failed = results.filter((result) => result.status === 'rejected').length;
    await reload();
    if (failed) {
      toast.error(`${successMessage} partially completed. ${failed} rows failed.`);
    } else {
      toast.success(successMessage);
    }
  };

  const runBulkPricingAction = async (action, successMessage) => {
    const targetIds = selectedIds.slice();
    if (!targetIds.length) {
      toast.error('Select one or more listings first.');
      return;
    }
    await bulkPricingAction({ listing_ids: targetIds, action });
    await reload();
    toast.success(successMessage);
  };

  const runBulkMarketplacePreflight = async (marketplaces, options = {}) => {
    const targetIds = Array.isArray(options.targetIds) && options.targetIds.length
      ? options.targetIds.slice()
      : selectedIds.length
        ? selectedIds.slice()
        : filteredListings.map((listing) => listing.id);
    if (!targetIds.length) {
      toast.error('Select one or more listings first.');
      return null;
    }
    setBulkPreflightLoading(true);
    try {
      const report = await fetchMarketplacePreflightBulk({
        listing_ids: targetIds,
        marketplaces,
        force_refresh: Boolean(options.forceRefresh),
        only_drafts: Boolean(options.onlyDrafts),
        selected_statuses: options.selectedStatuses || null,
        only_missing_preflight: Boolean(options.onlyMissingPreflight),
        only_stale_preflight: Boolean(options.onlyStalePreflight),
        only_ready_candidates: Boolean(options.onlyReadyCandidates),
        only_blocked_candidates: Boolean(options.onlyBlockedCandidates),
      });
      setBulkPreflightReport(report);
      await reload();
      return report;
    } finally {
      setBulkPreflightLoading(false);
    }
  };

  const exportBulkPreflightCsv = async () => {
    const targetIds = selectedIds.length ? selectedIds.slice() : filteredListings.map((listing) => listing.id);
    if (!targetIds.length) {
      toast.error('Select one or more listings first.');
      return;
    }
    try {
      const csv = await exportMarketplacePreflightCsv({
        listing_ids: targetIds,
        marketplaces: ['ebay', 'facebook'],
        force_refresh: false,
      });
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'posterpro-preflight-report.csv';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      toast.success('Downloaded blocker report CSV.');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'CSV export failed.');
    }
  };

  const copyBulkBlockerSummary = async () => {
    const scopeRows = getBulkScopeRows(selectedRows, filteredListings);
    if (!scopeRows.length) {
      toast.error('Select one or more listings first.');
      return;
    }
    const eBayCodes = summarizeBlockerCodes(scopeRows, 'ebay').slice(0, 5).map(([code, count]) => `${code} (${count})`).join(', ');
    const fbCodes = summarizeBlockerCodes(scopeRows, 'facebook').slice(0, 5).map(([code, count]) => `${code} (${count})`).join(', ');
    const text = [
      `Listings: ${scopeRows.length}`,
      `eBay blockers: ${eBayCodes || 'none'}`,
      `Facebook blockers: ${fbCodes || 'none'}`,
    ].join('\n');
    try {
      await navigator.clipboard.writeText(text);
      toast.success('Copied blocker summary to clipboard.');
    } catch {
      toast.error('Clipboard copy failed.');
    }
  };

  const runBulkPublishReady = async (marketplaces, { dryRun = true, allowWarnings = false, forceRefresh = false, skipAlreadyQueued = true } = {}) => {
    const targetIds = selectedIds.length ? selectedIds.slice() : filteredListings.map((listing) => listing.id);
    if (!targetIds.length) {
      toast.error('Select one or more listings first.');
      return null;
    }
    if (!dryRun && marketplaces.includes('ebay')) {
      const confirmed = confirmLiveEbayQueue({
        count: targetIds.length,
        marketplaces,
        allowWarnings,
        skipAlreadyQueued,
        context: 'Ready-only bulk publish queue',
      });
      if (!confirmed) {
        toast.error('Live eBay publish canceled.');
        return null;
      }
    }
    try {
      const report = await publishMarketplaceReadyBulk({
        listing_ids: targetIds,
        marketplaces,
        allow_warnings: Boolean(allowWarnings),
        dry_run: Boolean(dryRun),
        force_preflight_refresh: Boolean(forceRefresh),
        skip_already_queued: Boolean(skipAlreadyQueued),
        confirm_live_publish: !dryRun && marketplaces.includes('ebay'),
        confirmation_phrase: !dryRun && marketplaces.includes('ebay') ? liveEbayConfirmationPhrase : undefined,
      });
      setBulkPublishReport(report);
      await reload();
      const summary = report?.summary || {};
      if (dryRun) {
        toast.success(`Dry run complete. ${summary.dry_run_ready || 0} ready, ${summary.dry_run_blocked || 0} blocked.`);
      } else {
        toast.success(`Queued ${summary.queued || 0} marketplace listing${(summary.queued || 0) === 1 ? '' : 's'}.`);
      }
      return report;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Bulk publish-ready failed.');
      return null;
    }
  };

  const refreshLaunchCandidates = async () => {
    setLaunchCandidatesLoading(true);
    try {
      const report = await fetchLaunchCandidates({
        marketplace: 'ebay',
        max_items: 10,
        max_price: 50,
        include_warning_only: false,
        include_local_pickup: false,
        include_risky_shipping: false,
      });
      setLaunchCandidatesReport(report);
      return report;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Launch candidate refresh failed.');
      return null;
    } finally {
      setLaunchCandidatesLoading(false);
    }
  };

  const refreshRepairQueue = async () => {
    setRepairQueueLoading(true);
    try {
      const report = await fetchEbayLaunchRepairQueue({
        max_items: 25,
        max_price: 50,
        image_status: repairQueueImageStatusFilter !== 'all' ? repairQueueImageStatusFilter : undefined,
      });
      setRepairQueueReport(report);
      return report;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Repair queue refresh failed.');
      return null;
    } finally {
      setRepairQueueLoading(false);
    }
  };

  const applyRepairAction = async (listingId, action) => {
    try {
      const payload =
        action === 'category'
          ? { apply_category_suggestion: true }
          : { validate_images: true };
      const result = await applyEbayLaunchRepair(listingId, payload);
      await reload();
      await refreshRepairQueue();
      toast.success(
        action === 'category'
          ? `Applied suggested category for listing #${listingId}.`
          : `Validated image metadata for listing #${listingId}.`,
      );
      return result;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Repair action failed.');
      return null;
    }
  };

  const uploadActualPhotosToListing = async (listingId, files) => {
    if (!files?.length) return null;
    try {
      await uploadListingPhotos(listingId, {
        files,
        source: 'actual_upload',
        operatorState: 'suggested',
      });
      await reload();
      await refreshRepairQueue();
      if (selectedListingId === listingId) {
        await Promise.allSettled([
          fetchPricingRecommendation(listingId).then(setPricingRecommendation),
          fetchListingIntelligence(listingId).then(setListingIntelligence),
          loadMarketplacePreviewData(listingId),
        ]);
      }
      toast.success(`Uploaded ${files.length} actual photo${files.length === 1 ? '' : 's'} to listing #${listingId}.`);
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Photo upload failed.');
      return null;
    }
  };

  const approvePhotosForListing = async (listingId, storagePaths) => {
    if (!storagePaths?.length) return null;
    try {
      await approveListingPhotos(listingId, storagePaths);
      await reload();
      await refreshRepairQueue();
      toast.success(`Approved ${storagePaths.length} actual photo${storagePaths.length === 1 ? '' : 's'} for listing #${listingId}.`);
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Approve photo failed.');
      return null;
    }
  };

  const rejectPhotosForListing = async (listingId, storagePaths) => {
    if (!storagePaths?.length) return null;
    try {
      await rejectListingPhotos(listingId, storagePaths);
      await reload();
      await refreshRepairQueue();
      toast.success(`Rejected ${storagePaths.length} photo${storagePaths.length === 1 ? '' : 's'} for listing #${listingId}.`);
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Reject photo failed.');
      return null;
    }
  };

  const setPrimaryPhotoForListing = async (listingId, storagePath) => {
    if (!storagePath) return null;
    try {
      await setPrimaryListingPhoto(listingId, storagePath);
      await reload();
      await refreshRepairQueue();
      toast.success(`Set primary photo for listing #${listingId}.`);
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Set primary photo failed.');
      return null;
    }
  };

  const runLaunchDrillForCandidates = async () => {
    const report = launchCandidatesReport || await refreshLaunchCandidates();
    const candidateIds = (report?.candidates || []).slice(0, 5).map((item) => item.listing_id);
    if (!candidateIds.length) {
      toast.error('No launch candidates available.');
      return;
    }
    try {
      const drill = await runLaunchDrillDryRun({
        listing_ids: candidateIds,
        marketplace: 'ebay',
        max_items: 5,
        require_ready: true,
        include_payload_preview: true,
      });
      setBulkPublishReport((current) => current ? { ...current, launch_drill: drill } : { launch_drill: drill });
      toast.success(`Launch drill complete for ${candidateIds.length} candidate${candidateIds.length === 1 ? '' : 's'}.`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Launch drill failed.');
    }
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
            <Button href="/listings/new" variant="outline">
              New item
            </Button>
            <Button href="/settings?tab=marketplaces" variant="outline">
              Import listings
            </Button>
            <Button href="/intake">
              Import photos
            </Button>
            {user?.can_access_vine_import ? (
              <Button href="/imports/vine" variant="outline">
                Import Vine data
              </Button>
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
            <div className="relative w-full sm:w-[220px]">
              <select
                value={readinessFilter}
                onChange={(event) => setReadinessFilter(event.target.value)}
                className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
              >
                {READINESS_FILTER_OPTIONS.map((option) => (
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
        title="Listing pipeline triage"
        description="Use these counts to push high-volume draft review toward pricing, shipping, and publish readiness."
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Blocked" value={listingMetrics.blockedCount} detail="Drafts with publish blockers still unresolved." />
          <MetricCard label="Weak pricing" value={listingMetrics.weakPricingCount} detail="Drafts with low confidence or weak comps." />
          <MetricCard label="Stale pricing" value={listingMetrics.stalePricingCount} detail="Drafts that should be repriced before publish." />
          <MetricCard label="Ready for eBay" value={listingMetrics.readyForEbayCount} detail="Drafts with pricing and shipping ready for eBay queueing." />
        </div>
      </SectionPanel>

      <SectionPanel
        title="Bulk marketplace preflight"
        description="Run preflight across selected drafts, read exact blockers, and queue only items that are safe to publish."
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Selected / scoped" value={bulkPreflightSummary.selectedCount} detail={selectedRows.length ? 'Selected rows in the action bar.' : 'Visible filtered rows in the current view.'} />
          <MetricCard label="Checked" value={bulkPreflightSummary.checkedCount} detail="Rows with cached or freshly generated marketplace preflight summaries." />
          <MetricCard label="eBay ready" value={bulkPreflightSummary.ebayReadyCount} detail="Rows that can move to the eBay queue." />
          <MetricCard label="Facebook ready" value={bulkPreflightSummary.facebookReadyCount} detail="Rows that can be handed off to the Facebook assisted workflow." />
          <MetricCard label="Blocked" value={bulkPreflightSummary.blockedCount} detail="Rows with at least one marketplace blocker." />
          <MetricCard label="Warning only" value={bulkPreflightSummary.warningOnlyCount} detail="Rows that are ready but still need operator confirmation or review." />
          <MetricCard label="Ready to queue" value={bulkPreflightSummary.readyToQueueCount} detail="Rows with sufficient quality/readiness to queue once you choose live mode." />
          <MetricCard label="Most common blocker" value={bulkPreflightSummary.mostCommonBlocker || 'None'} detail={bulkPreflightSummary.mostCommonBlocker ? `${bulkPreflightSummary.mostCommonBlockerCount} occurrences in scope` : 'No blocker codes found in the current scope.'} />
        </div>
      </SectionPanel>

      <SectionPanel
        title="eBay launch repair queue"
        description="Unpublished eBay drafts under the launch threshold, grouped by actionable blockers so you can create the first safe live queue."
        action={
          <div className="flex flex-wrap gap-2">
            <select
              value={repairQueueImageStatusFilter}
              onChange={(event) => setRepairQueueImageStatusFilter(event.target.value)}
              className="h-9 rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]"
            >
              <option value="all">All image states</option>
              <option value="no_images">No images</option>
              <option value="reference_only">Reference only</option>
              <option value="actual_pending_review">Actual pending review</option>
              <option value="actual_approved">Actual approved</option>
              <option value="actual_file_invalid">Actual file invalid</option>
            </select>
            <Button variant="outline" size="sm" onClick={refreshRepairQueue} disabled={repairQueueLoading}>
              {repairQueueLoading ? 'Refreshing…' : 'Refresh repair queue'}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                const report = repairQueueReport || await refreshRepairQueue();
                const rows = report?.items || [];
                if (!rows.length) {
                  toast.error('Repair queue is empty.');
                  return;
                }
                const header = ['listing_id', 'title', 'price', 'blockers', 'suggested_category', 'image_status', 'recommended_action'];
                const csv = [
                  header.join(','),
                  ...rows.map((item) => [
                    item.listing_id,
                    `"${String(item.title || '').replaceAll('"', '""')}"`,
                    Number(item.price || 0).toFixed(2),
                    `"${(item.blocker_codes || []).join('|')}"`,
                    `"${String(item.suggested_category?.label || '').replaceAll('"', '""')}"`,
                    item.image_status || '',
                    `"${String(item.recommended_next_repair_action || '').replaceAll('"', '""')}"`,
                  ].join(',')),
                ].join('\n');
                const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
                const url = URL.createObjectURL(blob);
                const anchor = document.createElement('a');
                anchor.href = url;
                anchor.download = 'posterpro-ebay-launch-repair-queue.csv';
                document.body.appendChild(anchor);
                anchor.click();
                anchor.remove();
                URL.revokeObjectURL(url);
                toast.success('Downloaded eBay repair queue CSV.');
              }}
              disabled={repairQueueLoading}
            >
              Export repair CSV
            </Button>
          </div>
        }
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Repair rows" value={repairQueueReport?.summary?.included || 0} detail="Unpublished eBay drafts with actionable blockers under the current launch threshold." />
          <MetricCard label="No images" value={repairQueueReport?.summary?.no_images || 0} detail="Drafts with no saved image metadata at all." />
          <MetricCard label="Missing actual photos" value={repairQueueReport?.summary?.missing_actual_photos || 0} detail="Drafts that still need operator-approved actual item photos." />
          <MetricCard label="Reference only" value={repairQueueReport?.summary?.reference_only_images || 0} detail="Drafts with source images but no actual approved item photos." />
          <MetricCard label="Actual pending" value={repairQueueReport?.summary?.actual_pending_review || 0} detail="Drafts with uploaded actual photos still waiting for operator approval." />
          <MetricCard label="Actual approved" value={repairQueueReport?.summary?.actual_approved || 0} detail="Drafts with approved actual photos already attached." />
          <MetricCard label="Image URL invalid" value={repairQueueReport?.summary?.invalid_image_url || 0} detail="Drafts whose actual item image files or publish URLs still fail validation." />
          <MetricCard label="Missing category" value={repairQueueReport?.summary?.missing_category || 0} detail="Drafts that still need an eBay category suggestion or operator category review." />
          <MetricCard label="Missing aspects" value={repairQueueReport?.summary?.missing_required_aspects || 0} detail="Drafts missing required eBay item specifics for their current category." />
          <MetricCard label="Most common blocker" value={repairQueueReport?.summary?.most_common_blocker || 'None'} detail="Top issue in the current repair queue." />
          <MetricCard label="Ready for image preflight" value={repairQueueReport?.summary?.ready_for_image_preflight || 0} detail="Drafts whose actual photos are approved and can move to full eBay preflight." />
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {(repairQueueReport?.items || []).slice(0, 8).map((item) => (
            <div key={`repair-queue-${item.listing_id}`} className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
              <p className="text-sm font-semibold text-[#101828]">{item.title || `Listing #${item.listing_id}`}</p>
              <p className="mt-1 text-xs text-[#667085]">#{item.listing_id} · ${Number(item.price || 0).toFixed(2)} · {String(item.current_preflight_status || 'unknown').replaceAll('_', ' ')}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {(item.blocker_codes || []).slice(0, 4).map((code) => (
                  <span key={`${item.listing_id}-${code}`} className="pp-chip">{code}</span>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap gap-2 text-xs text-[#475467]">
                <span className="pp-chip">Image status: {String(item.image_status || 'unknown').replaceAll('_', ' ')}</span>
                <span className="pp-chip">Approved actual: {Number(item.photo_counts?.actual_approved_images || 0)}</span>
                <span className="pp-chip">Pending actual: {Number(item.photo_counts?.actual_pending_images || 0)}</span>
                <span className="pp-chip">Reference: {Number(item.photo_counts?.reference_source_images || 0)}</span>
              </div>
              {item.suggested_category?.label ? (
                <p className="mt-2 text-xs text-[#475467]">Suggested category: {item.suggested_category.label}</p>
              ) : null}
              <p className="mt-2 text-xs text-[#475467]">Recommended repair: {item.recommended_next_repair_action || 'Review blockers'}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => setSelectedListingId(item.listing_id)}>
                  Open editor
                </Button>
                <Button variant="outline" size="sm" onClick={() => runBulkMarketplacePreflight(['ebay'], { forceRefresh: true, targetIds: [item.listing_id] })}>
                  Run preflight
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => applyRepairAction(item.listing_id, 'category')}
                  disabled={!item.suggested_category?.can_apply}
                >
                  Apply suggested category
                </Button>
                <label className="inline-flex cursor-pointer items-center rounded-[10px] border border-[#d0d5dd] bg-white px-3 py-2 text-sm font-medium text-[#101828]">
                  Upload actual photos
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    multiple
                    className="hidden"
                    onChange={async (event) => {
                      const files = Array.from(event.target.files || []);
                      if (files.length) {
                        await uploadActualPhotosToListing(item.listing_id, files);
                      }
                      event.target.value = '';
                    }}
                  />
                </label>
                {Number(item.photo_counts?.actual_pending_images || 0) > 0 ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      const targetListing = listings.find((row) => row.id === item.listing_id);
                      const pendingPaths = (targetListing?.listing_images || [])
                        .filter((image) => !image?.is_reference && image?.operator_state !== 'approved' && image?.operator_state !== 'rejected')
                        .map((image) => image.storage_path)
                        .filter(Boolean);
                      await approvePhotosForListing(item.listing_id, pendingPaths);
                    }}
                  >
                    Approve actual photos
                  </Button>
                ) : null}
                <Button variant="outline" size="sm" onClick={() => applyRepairAction(item.listing_id, 'images')}>
                  Validate images
                </Button>
              </div>
            </div>
          ))}
          {!(repairQueueReport?.items || []).length ? (
            <div className="rounded-[12px] border border-dashed border-[#d0d5dd] bg-white p-4 text-sm text-[#667085]">
              Refresh the repair queue to find unpublished eBay drafts that are closest to launch-ready.
            </div>
          ) : null}
        </div>
      </SectionPanel>

      <SectionPanel
        title="Launch QA candidates"
        description="Small low-risk eBay launch set for safe production acceptance and drill testing."
        action={
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={refreshLaunchCandidates} disabled={launchCandidatesLoading}>
              {launchCandidatesLoading ? 'Refreshing…' : 'Run candidate selector'}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                const candidateIds = (launchCandidatesReport?.candidates || []).map((item) => item.listing_id);
                if (!candidateIds.length) {
                  toast.error('Run the candidate selector first.');
                  return;
                }
                setSelectedIds(candidateIds);
                await runBulkMarketplacePreflight(['ebay', 'facebook'], { forceRefresh: true, targetIds: candidateIds });
              }}
              disabled={launchCandidatesLoading}
            >
              Run preflight on candidates
            </Button>
            <Button variant="outline" size="sm" onClick={runLaunchDrillForCandidates} disabled={launchCandidatesLoading}>
              Dry-run launch drill
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                const first = launchCandidatesReport?.candidates?.[0];
                if (!first?.listing_id) {
                  toast.error('Run the candidate selector first.');
                  return;
                }
                setSelectedListingId(first.listing_id);
                document.getElementById('listing-results')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
              }}
              disabled={launchCandidatesLoading}
            >
              Open first candidate
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                const candidateIds = (launchCandidatesReport?.candidates || []).map((item) => item.listing_id);
                if (!candidateIds.length) {
                  toast.error('Run the candidate selector first.');
                  return;
                }
                try {
                  const csv = await exportMarketplacePreflightCsv({
                    listing_ids: candidateIds,
                    marketplaces: ['ebay'],
                    force_refresh: false,
                    only_drafts: false,
                  });
                  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
                  const url = URL.createObjectURL(blob);
                  const anchor = document.createElement('a');
                  anchor.href = url;
                  anchor.download = 'posterpro-launch-candidates.csv';
                  document.body.appendChild(anchor);
                  anchor.click();
                  anchor.remove();
                  URL.revokeObjectURL(url);
                  toast.success('Downloaded launch candidate QA CSV.');
                } catch (error) {
                  toast.error(error instanceof Error ? error.message : 'Launch candidate CSV export failed.');
                }
              }}
              disabled={launchCandidatesLoading}
            >
              Export candidate QA CSV
            </Button>
          </div>
        }
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="eBay readiness" value={ebayAccountReadiness?.publish_ready ? 'Ready' : 'Needs setup'} detail={ebayAccountReadiness?.status_note || 'Account readiness unavailable.'} />
          <MetricCard label="Policies present" value={ebayAccountReadiness?.policies_present ? 'Yes' : 'No'} detail="Payment, fulfillment, and return policies." />
          <MetricCard label="Location key" value={ebayAccountReadiness?.location_present ? 'Yes' : 'No'} detail="Merchant location configured for eBay inventory." />
          <MetricCard label="Candidates" value={(launchCandidatesReport?.candidates || []).length || 0} detail="Low-risk listings selected for a controlled drill." />
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {(launchCandidatesReport?.candidates || []).slice(0, 6).map((item) => (
            <div key={`launch-candidate-${item.listing_id}`} className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
              <p className="text-sm font-semibold text-[#101828]">{item.title || `Listing #${item.listing_id}`}</p>
              <p className="mt-1 text-xs text-[#667085]">#{item.listing_id} · ${Number(item.price || 0).toFixed(2)} · {String(item.preflight_status || 'unknown').replaceAll('_', ' ')}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {(item.top_warnings || []).slice(0, 3).map((warning) => (
                  <span key={`${item.listing_id}-${warning}`} className="pp-chip">{warning}</span>
                ))}
              </div>
              <p className="mt-2 text-xs text-[#475467]">{item.reason_selected || 'Selected for launch drill.'}</p>
            </div>
          ))}
          {!(launchCandidatesReport?.candidates || []).length ? (
            <div className="rounded-[12px] border border-dashed border-[#d0d5dd] bg-white p-4 text-sm text-[#667085]">
              Run the candidate selector to identify 5-10 low-risk eBay drafts for the launch drill.
            </div>
          ) : null}
        </div>
      </SectionPanel>

      {bulkPreflightReport ? (
        <SectionPanel
          title="Last bulk preflight report"
          description="Review the latest bulk run without opening every listing."
        >
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Ready for eBay" value={bulkPreflightReport?.summary?.ready_for_ebay || 0} detail="Rows whose current preflight is ready for eBay queueing." />
            <MetricCard label="Ready for Facebook" value={bulkPreflightReport?.summary?.ready_for_facebook || 0} detail="Rows whose current preflight is ready for Facebook assisted handoff." />
            <MetricCard label="Blocked" value={bulkPreflightReport?.summary?.blocked || 0} detail="Rows blocked by at least one marketplace." />
            <MetricCard label="Preflight failed" value={bulkPreflightReport?.summary?.preflight_failed || 0} detail="Rows that returned a backend validation or provider failure." />
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {(bulkPreflightReport?.items || []).slice(0, 6).map((item) => (
              <div key={`bulk-preflight-${item.listing_id}`} className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                <p className="text-sm font-semibold text-[#101828]">{item.title || `Listing #${item.listing_id}`}</p>
                <p className="mt-1 text-xs text-[#667085]">#{item.listing_id}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {(Object.entries(item.marketplaces || {})).map(([market, data]) => (
                    <span key={`${item.listing_id}-${market}`} className="pp-chip">
                      {market.toUpperCase()}: {String(data?.status || 'unknown').replaceAll('_', ' ')}
                    </span>
                  ))}
                </div>
                {item.top_blocker_code ? <p className="mt-2 text-xs text-[#b42318]">Top blocker: {item.top_blocker_code}</p> : null}
              </div>
            ))}
          </div>
        </SectionPanel>
      ) : null}

      {bulkPublishReport ? (
        <SectionPanel
          title="Last bulk publish-ready run"
          description={`Dry-run ${bulkPublishReport?.dry_run ? 'preview' : 'live queue'} for ready-only marketplace publishing.`}
        >
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Queued" value={bulkPublishReport?.summary?.queued || 0} detail="Listings queued successfully in live mode." />
            <MetricCard label="Skipped blocked" value={bulkPublishReport?.summary?.skipped_blocked || 0} detail="Listings blocked by a marketplace preflight issue." />
            <MetricCard label="Warnings need confirm" value={bulkPublishReport?.summary?.skipped_warning_requires_confirmation || 0} detail="Warning-only listings that were not allowed into live mode." />
            <MetricCard label="Already queued" value={bulkPublishReport?.summary?.skipped_already_queued || 0} detail="Listings already had pending marketplace work." />
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {(bulkPublishReport?.items || []).slice(0, 6).map((item) => (
              <div key={`bulk-publish-${item.listing_id}`} className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                <p className="text-sm font-semibold text-[#101828]">{item.title || `Listing #${item.listing_id}`}</p>
                <p className="mt-1 text-xs text-[#667085]">#{item.listing_id}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {(Object.entries(item.marketplaces || {})).map(([market, data]) => (
                    <span key={`${item.listing_id}-${market}`} className="pp-chip">
                      {market.toUpperCase()}: {String(data?.status || 'unknown').replaceAll('_', ' ')}
                    </span>
                  ))}
                </div>
                {item.blocked_marketplaces?.length ? (
                  <p className="mt-2 text-xs text-[#b42318]">Blocked: {item.blocked_marketplaces.join(', ')}</p>
                ) : null}
                {item.warning_marketplaces?.length ? (
                  <p className="mt-1 text-xs text-[#8a4b10]">Warnings: {item.warning_marketplaces.join(', ')}</p>
                ) : null}
              </div>
            ))}
          </div>
        </SectionPanel>
      ) : null}

      <SectionPanel
        title="Launch QA checklist"
        description="Operator-safe production drill steps for a tiny controlled eBay launch set."
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {[
            'Confirm eBay account is connected.',
            'Confirm policy IDs are present.',
            'Confirm merchant location key.',
            'Confirm selected draft titles and prices.',
            'Confirm condition and notes.',
            'Confirm photos are actual item photos.',
            'Confirm shipping weight and dimensions.',
            'Confirm category and required aspects.',
            'Confirm no source/reference-only image warning.',
            'Run dry-run publish-ready or launch drill.',
            'Queue only ready eBay listings with explicit confirmation.',
            'Inspect translated errors and retry only retryable failures.',
          ].map((item) => (
            <div key={item} className="rounded-[12px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#475467]">{item}</div>
          ))}
        </div>
        {bulkPublishReport?.launch_drill ? (
          <div className="mt-4 rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
            <p className="text-sm font-semibold text-[#101828]">Latest launch drill result</p>
            <div className="mt-3 grid gap-3 md:grid-cols-3">
              <MetricCard label="Checked" value={bulkPublishReport.launch_drill.summary?.checked || 0} detail="Listings inspected in the drill." />
              <MetricCard label="Ready" value={bulkPublishReport.launch_drill.summary?.ready || 0} detail="Listings that passed the dry-run drill." />
              <MetricCard label="Blocked" value={bulkPublishReport.launch_drill.summary?.blocked || 0} detail="Listings that still need operator fixes." />
            </div>
          </div>
        ) : null}
      </SectionPanel>

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
          right={
            <div className="flex flex-wrap gap-2">
              {workflowPreferences.bulk_approval_enabled ? (
                <Button variant="outline" size="sm" onClick={approveSelected}>
                  Approve selected
                </Button>
              ) : null}
              <Button variant="outline" size="sm" onClick={approveAndPublishSelected}>
                Approve &amp; publish selected
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkMarketplacePreflight(['ebay'], { forceRefresh: true })} disabled={bulkPreflightLoading}>
                Run eBay preflight
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkMarketplacePreflight(['facebook'], { forceRefresh: true })} disabled={bulkPreflightLoading}>
                Run Facebook preflight
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkMarketplacePreflight(['ebay', 'facebook'], { forceRefresh: true })} disabled={bulkPreflightLoading}>
                Run both preflights
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkPublishReady(['ebay', 'facebook'], { dryRun: true, forceRefresh: true })}>
                Dry-run publish-ready
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkPublishReady(['ebay'], { dryRun: false, forceRefresh: true })}>
                Queue eBay-ready only
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkPublishReady(['facebook'], { dryRun: false, forceRefresh: true })}>
                Facebook assisted handoff
              </Button>
              <Button variant="outline" size="sm" onClick={exportBulkPreflightCsv}>
                Export blocker CSV
              </Button>
              <Button variant="outline" size="sm" onClick={copyBulkBlockerSummary}>
                Copy blocker summary
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => updateSelectedListings(
                  (listing) => ({
                    condition: 'Open Box',
                    condition_data: {
                      ...(listing.condition_data || {}),
                      open_box: true,
                      used: false,
                      new_in_box: false,
                      operator_review_required: true,
                      condition_bucket: 'open_box',
                    },
                  }),
                  'Marked selected drafts as open box.',
                )}
              >
                Mark open box
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => updateSelectedListings(
                  (listing) => ({
                    condition: 'Used',
                    condition_data: {
                      ...(listing.condition_data || {}),
                      open_box: false,
                      used: true,
                      new_in_box: false,
                      operator_review_required: true,
                      condition_bucket: 'used',
                    },
                  }),
                  'Marked selected drafts as used.',
                )}
              >
                Mark used
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => updateSelectedListings(
                  (listing) => ({
                    shipping_profile: {
                      ...(listing.shipping_profile || {}),
                      manual_measurement_needed: true,
                    },
                  }),
                  'Marked selected drafts as needing weighing.',
                )}
              >
                Needs weighing
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => updateSelectedListings(
                  (listing) => ({
                    shipping_profile: {
                      ...(listing.shipping_profile || {}),
                      local_pickup_recommended: true,
                      shipping_class_suggestion: 'local_pickup_only',
                    },
                  }),
                  'Marked selected drafts as local-pickup ready.',
                )}
              >
                Local pickup only
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => updateSelectedListings(
                  (listing) => ({
                    listing_images: (listing.listing_images || []).map((image) => ({
                      ...image,
                      operator_state: image.operator_state === 'rejected' ? 'rejected' : 'approved',
                    })),
                  }),
                  'Approved selected image suggestions.',
                )}
              >
                Approve image suggestions
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkPricingAction('refresh', 'Refreshed pricing for selected drafts.')}>
                Price selected
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkPricingAction('apply_recommended', 'Applied suggested list pricing to selected drafts.')}>
                Apply list pricing
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkPricingAction('apply_quick_sale', 'Applied quick-sale pricing to selected drafts.')}>
                Apply quick-sale
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkPricingAction('apply_floor', 'Applied floor pricing to selected drafts.')}>
                Apply floor
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
            </div>
          }
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
                  {getReadinessSummary(listing).blockers?.length ? (
                    <p className="mt-1 text-xs text-[#b42318]">{getReadinessSummary(listing).blockers[0]}</p>
                  ) : null}
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <p className="text-xs text-[#667085]">#{listing.id}</p>
                    {listing.sku ? <span className="pp-chip">SKU {listing.sku}</span> : null}
                    {isAmazonVineSource(listing) ? <span className="pp-chip">Vine</span> : null}
                    <span className="pp-chip">{getListingImageCount(listing)} image{getListingImageCount(listing) === 1 ? '' : 's'}</span>
                    {listing?.quality_summary?.score != null ? <span className="pp-chip">Quality {listing.quality_summary.score}</span> : null}
                    {listing?.marketplace_data?.pricing_analysis?.price_confidence ? <span className="pp-chip">{Math.round(listing.marketplace_data.pricing_analysis.price_confidence * 100)}% pricing</span> : null}
                    {listing.needs_review ? <span className="pp-chip">Needs Review</span> : null}
                    {listing.restricted_review_required ? <span className="pp-chip">Restricted Review</span> : null}
                    {listing.custom_labels?.includes('needs_photos') ? <span className="pp-chip">Image Missing</span> : null}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {['ebay', 'facebook'].map((market) => {
                      const summary = getMarketplacePreflightSummary(listing, market);
                      const status = getMarketplacePreflightStatus(listing, market);
                      const label = `${market.toUpperCase()}: ${status.replaceAll('_', ' ')}`;
                      return (
                        <span
                          key={`${listing.id}-${market}-preflight`}
                          className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
                            getMarketplacePreflightTone(summary) === 'danger'
                              ? 'border-[#fecdca] bg-[#fff6ed] text-[#b54708]'
                              : getMarketplacePreflightTone(summary) === 'warning'
                              ? 'border-[#fef0c7] bg-[#fffaeb] text-[#8a4b10]'
                              : getMarketplacePreflightTone(summary) === 'success'
                              ? 'border-[#abefc6] bg-[#ecfdf3] text-[#027a48]'
                              : 'border-[#d0d5dd] bg-[#f9fafb] text-[#475467]'
                          }`}
                        >
                          {label}
                        </span>
                      );
                    })}
                    {(() => {
                      const ebaySummary = getMarketplacePreflightSummary(listing, 'ebay');
                      const blocker = getMarketplaceTopBlocker(ebaySummary) || getMarketplaceTopBlocker(getMarketplacePreflightSummary(listing, 'facebook'));
                      return blocker ? <span className="pp-chip">Top blocker: {blocker}</span> : null;
                    })()}
                    {(() => {
                      const summary = getMarketplacePreflightSummary(listing, 'ebay') || getMarketplacePreflightSummary(listing, 'facebook');
                      return summary?.last_checked_at ? <span className="pp-chip">Checked {getMarketplacePreflightAgeLabel(summary)}</span> : null;
                    })()}
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
                          {getReadinessSummary(listing).blockers?.length ? (
                            <p className="mt-1 text-xs text-[#b42318]">{getReadinessSummary(listing).blockers[0]}</p>
                          ) : null}
                          <p className="mt-1 text-xs text-[#667085]">#{listing.id}{listing.sku ? ` · SKU ${listing.sku}` : ''}</p>
                          <p className="mt-1 text-xs text-[#667085]">{getListingImageCount(listing)} image{getListingImageCount(listing) === 1 ? '' : 's'}</p>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {['ebay', 'facebook'].map((market) => {
                              const summary = getMarketplacePreflightSummary(listing, market);
                              const status = getMarketplacePreflightStatus(listing, market);
                              const tone = getMarketplacePreflightTone(summary);
                              return (
                                <span
                                  key={`${listing.id}-${market}-preflight-grid`}
                                  className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
                                    tone === 'danger'
                                      ? 'border-[#fecdca] bg-[#fff6ed] text-[#b54708]'
                                      : tone === 'warning'
                                      ? 'border-[#fef0c7] bg-[#fffaeb] text-[#8a4b10]'
                                      : tone === 'success'
                                      ? 'border-[#abefc6] bg-[#ecfdf3] text-[#027a48]'
                                      : 'border-[#d0d5dd] bg-[#f9fafb] text-[#475467]'
                                  }`}
                                >
                                  {market.toUpperCase()}: {status.replaceAll('_', ' ')}
                                </span>
                              );
                            })}
                            {(() => {
                              const summary = getMarketplacePreflightSummary(listing, 'ebay') || getMarketplacePreflightSummary(listing, 'facebook');
                              const blocker = getMarketplaceTopBlocker(summary);
                              return blocker ? <span className="pp-chip">Top blocker: {blocker}</span> : null;
                            })()}
                            {(() => {
                              const summary = getMarketplacePreflightSummary(listing, 'ebay') || getMarketplacePreflightSummary(listing, 'facebook');
                              return summary?.last_checked_at ? <span className="pp-chip">Checked {getMarketplacePreflightAgeLabel(summary)}</span> : null;
                            })()}
                          </div>
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
                      {listing?.quality_summary?.status ? <span className="pp-chip">{String(listing.quality_summary.status).replaceAll('_', ' ')}</span> : null}
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
              marketplacePreflights={marketplacePreflights}
              marketplacePayloadPreviews={marketplacePayloadPreviews}
              workflowPreferences={workflowPreferences}
              onRefreshMarketplacePreflight={loadMarketplacePreviewData}
              onSyncEbayListing={syncListingWithEbay}
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
              onRefreshPricing={async (id) => {
                await refreshPricingRecommendation(id);
                setPricingRecommendation(await fetchPricingRecommendation(id));
                setListingIntelligence(await fetchListingIntelligence(id));
                await reload();
              }}
              onApplyPricing={async (id, payload) => {
                await applyPricingRecommendation(id, payload);
                setPricingRecommendation(await fetchPricingRecommendation(id));
                setListingIntelligence(await fetchListingIntelligence(id));
                await reload();
              }}
              onGenerate={async (id) => {
                await generateListing(id);
                setPricingRecommendation(await fetchPricingRecommendation(id));
                setListingIntelligence(await fetchListingIntelligence(id));
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
              onUploadPhotos={uploadActualPhotosToListing}
              onApprovePhotos={approvePhotosForListing}
              onRejectPhotos={rejectPhotosForListing}
              onSetPrimaryPhoto={setPrimaryPhotoForListing}
            />
          ) : null}
        </Drawer>
      </div>
    </AppShell>
  );
}

ListingsPage.requireAuth = true;
