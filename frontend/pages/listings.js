import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { BriefcaseBusiness, ListChecks, Rocket, ShieldAlert, Sparkles } from 'lucide-react';
import toast from 'react-hot-toast';
import { useRouter } from 'next/router';

import AppShell from '../components/layout/AppShell';
import { PageAside, PageBand, PageFrame, PageMain, PageSplit } from '../components/layout/PageFrame';
import ListingEditor from '../components/ListingEditor';
import Button from '../components/ui/button';
import MetricCard from '../components/ui/metric-card';
import DataTable from '../components/ui/data-table';
import Drawer from '../components/ui/drawer';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import ListingsReportPanel from '../components/listings/panels/ListingsReportPanel';
import ListingsStatusCell from '../components/listings/table/ListingsStatusCell';
import ListingsCollapsibleSection from '../components/listings/workspace/ListingsCollapsibleSection';
import ListingsTitleCell from '../components/listings/table/ListingsTitleCell';
import ListingsBulkActionBar from '../components/listings/workspace/ListingsBulkActionBar';
import ListingsGridCard from '../components/listings/workspace/ListingsGridCard';
import ListingsOverviewBand from '../components/listings/workspace/ListingsOverviewBand';
import ListingsThumbnail from '../components/listings/workspace/ListingsThumbnail';
import ListingsWorkspaceEmptyState from '../components/listings/workspace/ListingsWorkspaceEmptyState';
import ListingsWorkspaceErrorBoundary from '../components/listings/workspace/ListingsWorkspaceErrorBoundary';
import ListingsLaunchCandidatesPanel from '../components/listings/panels/ListingsLaunchCandidatesPanel';
import ListingsPreflightPanel from '../components/listings/panels/ListingsPreflightPanel';
import ListingsRepairQueuePanel from '../components/listings/panels/ListingsRepairQueuePanel';
import ListingsStatusGrid from '../components/listings/panels/ListingsStatusGrid';
import ListingsToolbar from '../components/listings/toolbar/ListingsToolbar';
import ListingsQueueTabs from '../components/listings/workspace/ListingsQueueTabs';
import { useAuth } from '../contexts/AuthContext';
import { useMarketplacePublish } from '../hooks/useMarketplacePublish';
import useDashboardData from '../hooks/useDashboardData';
import { formatPublishFailureMessage } from '../lib/publish-status';
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
  approveListing as approveListingApi,
  approveListingsBulk,
  approveAndQueueListings,
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
  toThumbnailImageUrl,
  uploadListingPhotos,
  updateListing,
} from '../lib/api';

const LISTING_TABS = [
  { value: 'all', label: 'All Listings' },
  { value: 'vine', label: 'Amazon Vine' },
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
  { value: 'facebook', label: 'Facebook Marketplace' },
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
  { value: 'media_inventory_recovery', label: 'Recovered inventory' },
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
  const isRecovery = listing?.source_type === 'media_inventory_recovery';
  const explicitlyApproved = Boolean(listing?.source_metadata?.operator_approved_at);
  if (isRecovery && !explicitlyApproved) return 'drafts';
  if (listing.restricted_review_required || listing.needs_review) return isCompleteForOperatorReview(listing) ? 'review' : 'drafts';
  if (listing.status === 'ready') return explicitlyApproved || !isRecovery ? 'ready' : 'drafts';
  return 'review';
}

function isCompleteForOperatorReview(listing) {
  const readiness = getReadinessSummary(listing);
  const hasPrice = Boolean(listing?.listing_price || listing?.suggested_price || listing?.price);
  const hasCategory = Boolean(listing?.category_id || listing?.category_suggestion || listing?.detected_category_guess);
  const hasCondition = Boolean(listing?.condition || listing?.condition_data?.condition_bucket);
  const imageCount = Number(readiness?.actual_image_count || getListingImageCount(listing) || 0);
  return Boolean((listing?.title || listing?.suggested_title) && listing?.description && hasPrice && hasCategory && hasCondition && imageCount > 0);
}

function matchesTab(listing, tab) {
  if (tab === 'all') return true;
  if (tab === 'vine') return isAmazonVineSource(listing);
  if (tab === 'sold') return isSoldListing(listing);
  if (isSoldListing(listing)) return false;
  if (tab === 'archived') return isArchivedListing(listing);
  if (isArchivedListing(listing)) return false;
  if (tab === 'drafts') return getListingBucket(listing) === 'drafts';
  if (tab === 'review') return getListingBucket(listing) === 'review';
  if (tab === 'ready') return getListingBucket(listing) === 'ready';
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
  const alreadyPostedToEbay = listing?.ebay_publish_status === 'POSTED' || Boolean(listing?.ebay_listing_id);
  if (alreadyPostedToEbay) {
    const secondaryTargets = marketplaces.filter((market) => market !== 'ebay');
    if (secondaryTargets.length) return secondaryTargets;
    const inferred = (enabledPlatforms || []).filter((market) => market && market !== 'ebay');
    if (inferred.length) return inferred.slice(0, 2);
    return ['facebook'];
  }
  return marketplaces.length ? marketplaces : ['ebay'];
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
  const [catalogPage, setCatalogPage] = useState(1);
  const [catalogPageSize, setCatalogPageSize] = useState(25);
  // These filters are inputs to the paginated catalog request, so they must
  // exist before useDashboardData builds that request.  Keeping them below
  // the hook caused a temporal-dead-zone render failure on /listings.
  const [activeTab, setActiveTab] = useState('all');
  const [search, setSearch] = useState('');
  const [marketFilter, setMarketFilter] = useState('all');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [readinessFilter, setReadinessFilter] = useState('all');
  const { listings, listingError, listingPagination, autonomousConfig, enabledPlatforms, listingTemplates, reload } = useDashboardData(user?.id, {
    includeClusters: false,
    includeMarketplaces: false,
    includeAnalytics: false,
    includeAlerts: false,
    includeOfferDashboard: false,
    includeStorageBatches: false,
    paginateListings: true,
    listingPage: catalogPage,
    listingPageSize: catalogPageSize,
    listingSourceType: sourceFilter === 'amazon_vine' || activeTab === 'vine' ? 'amazon_vine' : sourceFilter,
    listingSearch: search,
    listingQueue: activeTab === 'vine' ? 'all' : activeTab,
  });
  const [selectedIds, setSelectedIds] = useState([]);
  const [selectedListingId, setSelectedListingId] = useState(null);
  const [viewMode, setViewMode] = useState('table');
  const [workspaceMode, setWorkspaceMode] = useState('results');
  const [pricingRecommendation, setPricingRecommendation] = useState(null);
  const [listingIntelligence, setListingIntelligence] = useState(null);
  const [crosspostPreview, setCrosspostPreview] = useState([]);
  const [crosspostPreviewLoading, setCrosspostPreviewLoading] = useState(false);
  const [marketplacePreflights, setMarketplacePreflights] = useState({});
  const [marketplacePayloadPreviews, setMarketplacePayloadPreviews] = useState({});
  const [jobsOverview, setJobsOverview] = useState({ import_jobs: [], crosspost_jobs: [] });
  const [bulkPreflightReport, setBulkPreflightReport] = useState(null);
  const [bulkPreflightLoading, setBulkPreflightLoading] = useState(false);
  const [bulkPreflightProgress, setBulkPreflightProgress] = useState(null);
  const [bulkPublishReport, setBulkPublishReport] = useState(null);
  const [pendingBulkPublish, setPendingBulkPublish] = useState(null);
  const [bulkPublishAcknowledged, setBulkPublishAcknowledged] = useState(false);
  const [bulkPublishSubmitting, setBulkPublishSubmitting] = useState(false);
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
    setCatalogPage(1);
    setSelectedIds([]);
    if (!router.isReady) return;
    router.replace({ pathname: router.pathname, query: { ...router.query, tab: nextTab } }, undefined, { shallow: true });
  };

  const clearAllFilters = () => {
    setActiveTab('all');
    setSearch('');
    setMarketFilter('all');
    setSourceFilter('all');
    setReadinessFilter('all');
    setSelectedIds([]);
    setCatalogPage(1);
    if (router.isReady) router.replace({ pathname: router.pathname }, undefined, { shallow: true });
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
        const overview = await fetchMarketplaceJobsOverview({ limit: 40, compact: true });
        if (active) {
          setJobsOverview(overview || { import_jobs: [], crosspost_jobs: [] });
        }
      } catch {
        if (active) setJobsOverview({ import_jobs: [], crosspost_jobs: [] });
      }
    };
    loadJobs();
    // Queue state is supporting context on the catalog page. Polling it every
    // five seconds made the page feel as though it was constantly reloading.
    const timer = setInterval(loadJobs, 30000);
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

  const isAlreadyPostedToEbay = useCallback(
    (listing) => listing?.ebay_publish_status === 'POSTED' || Boolean(listing?.ebay_listing_id),
    [],
  );

  const selectedReviewRows = useMemo(
    () => selectedRows.filter((listing) => getListingBucket(listing) === 'review' || listing.status === 'draft'),
    [selectedRows],
  );

  const selectedPublishableRows = useMemo(() => {
    return selectedRows.filter((listing) => {
      const bucket = getListingBucket(listing);
      const isReadyRow = bucket === 'ready' || listing?.status === 'ready';
      const isDraftRow = bucket === 'drafts' || listing?.status === 'draft';
      const hasCrosspostTargets = getQuickPublishTargets(listing, enabledPlatforms).length > 0;
      const crosspostOnly = isAlreadyPostedToEbay(listing) && hasCrosspostTargets;
      if (crosspostOnly) return true;
      if (workflowPreferences.review_before_publish) {
        return isReadyRow;
      }
      return isDraftRow || isReadyRow;
    });
  }, [enabledPlatforms, isAlreadyPostedToEbay, selectedRows, workflowPreferences.review_before_publish]);

  const selectedNeedsApprovalRows = useMemo(() => {
    return selectedRows.filter((listing) => getListingBucket(listing) === 'review' || listing.status === 'draft');
  }, [selectedRows]);

  const tabCounts = useMemo(
    () =>
      Object.fromEntries(LISTING_TABS.map((tab) => [tab.value, listings.filter((listing) => matchesTab(listing, tab.value)).length])),
    [listings],
  );
  const activeTabLabel = LISTING_TABS.find((tab) => tab.value === activeTab)?.label || 'Listings';

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
    const queued = Number(jobsOverview.crosspost_summary?.queued || 0) + Number(jobsOverview.crosspost_summary?.running || 0);
    const completed = Number(jobsOverview.crosspost_summary?.completed || 0);
    const failed = Number(jobsOverview.crosspost_summary?.failed || 0);
    const total = Number(jobsOverview.crosspost_summary?.total || 0);
    const progress = total ? Math.round((completed / total) * 100) : 0;
    return { queued, completed, failed, total, progress };
  }, [jobsOverview]);

  const workspaceModes = useMemo(() => ([
    {
      key: 'results',
      label: 'Results',
      description: 'Focus on the active queue first.',
      icon: ListChecks,
      count: filteredListings.length,
    },
    {
      key: 'repair',
      label: 'Repair',
      description: 'Preflight blockers, readiness, and fix queues.',
      icon: ShieldAlert,
      count: listingMetrics.blockedCount,
    },
    {
      key: 'launch',
      label: 'Launch',
      description: 'Dry-run launch QA and publish readiness.',
      icon: Rocket,
      count: launchCandidatesReport?.candidates?.length || 0,
    },
    {
      key: 'queue',
      label: 'Queue',
      description: 'Queued jobs and publish progress.',
      icon: BriefcaseBusiness,
      count: publishJobStats.queued,
    },
    {
      key: 'all',
      label: 'All',
      description: 'Open the full operator workspace.',
      icon: Sparkles,
      count: tabCounts[activeTab] || 0,
    },
  ]), [
    activeTab,
    filteredListings.length,
    launchCandidatesReport?.candidates?.length,
    listingMetrics.blockedCount,
    publishJobStats.queued,
    tabCounts,
  ]);

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

  const queuePublishRows = async (rows, { ebayConfirmationAlreadyGranted = false } = {}) => {
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
      if (marketplaces.includes('ebay') && !ebayConfirmationAlreadyGranted) {
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
      const queueResults = Array.isArray(response?.results) ? response.results : [];
      const marketplaceResults = queueResults.flatMap((item) => Array.isArray(item?.results) ? item.results : []);
      const queued = marketplaceResults.filter((item) => item?.status === 'QUEUED').length;
      const blocked = marketplaceResults.filter((item) => item?.status === 'BLOCKED').length;
      const alreadyQueued = marketplaceResults.filter((item) => item?.status === 'SKIPPED_ALREADY_QUEUED').length;
      setBulkPublishReport({
        dry_run: false,
        direct_queue: true,
        summary: { queued, skipped_blocked: blocked, skipped_already_queued: alreadyQueued, failed },
        items: queueResults.map((item) => ({
          listing_id: item?.listing_id,
          marketplaces: Object.fromEntries((item?.results || []).map((result) => [result.marketplace || 'unknown', result])),
          error: item?.error || null,
        })),
      });
      return { failed, total: publishableRows.length, queued, blocked, alreadyQueued };
    }

    const results = await Promise.allSettled(
      publishableRows.map((listing) => publish(
        listing.id,
        getQuickPublishTargets(listing, enabledPlatforms),
      )),
    );
    const failed = results.filter((result) => result.status === 'rejected').length;
    setBulkPublishReport({
      dry_run: false,
      direct_queue: true,
      summary: { queued: publishableRows.length - failed, skipped_blocked: 0, skipped_already_queued: 0, failed },
      items: results.map((result, index) => ({ listing_id: publishableRows[index]?.id, marketplaces: {}, error: result.status === 'rejected' ? String(result.reason || 'Publish request failed') : null })),
    });
    return { failed, total: publishableRows.length, queued: publishableRows.length - failed };
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

    const { failed, total, queued = 0, blocked = 0, canceled } = await queuePublishRows(selectedPublishableRows);
    if (canceled) return;
    if (failed) toast.error(`${failed} publish action${failed === 1 ? '' : 's'} failed.`);
    if (queued) toast.success(`Queued ${queued} of ${total} listing${total === 1 ? '' : 's'} for publish.${blocked ? ` ${blocked} blocked.` : ''}`);
    else if (!failed) toast.error(`Nothing was queued.${blocked ? ` ${blocked} listing${blocked === 1 ? '' : 's'} was blocked.` : ''}`);
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

  const approveAndPublishSelected = async ({ confirmedEbay = false, targetIds = null } = {}) => {
    const targetRows = Array.isArray(targetIds) && targetIds.length
      ? listings.filter((listing) => targetIds.includes(listing.id))
      : selectedRows;
    if (!targetRows.length) {
      toast.error('Select one or more listings first.');
      return;
    }
    const requestedMarketplaces = Array.from(new Set(targetRows.flatMap((listing) => {
      const targets = getQuickPublishTargets(listing, enabledPlatforms);
      return targets.length ? targets : ['ebay'];
    })));
    if (requestedMarketplaces.includes('ebay') && !confirmedEbay) {
      setBulkPublishAcknowledged(false);
      setPendingBulkPublish({ listingIds: targetRows.map((listing) => listing.id), marketplaces: requestedMarketplaces });
      return;
    }
    setBulkPublishSubmitting(true);
    try {
      setPendingBulkPublish(null);
      const response = await approveAndQueueListings(targetRows.map((listing) => listing.id), requestedMarketplaces);
      const outcomes = Array.isArray(response?.results) ? response.results : [];
      const queued = outcomes.filter((item) => String(item?.status || '').toLowerCase() === 'queued').length;
      const blocked = outcomes.filter((item) => String(item?.status || '').toLowerCase() === 'blocked').length;
      const failed = outcomes.filter((item) => String(item?.status || '').toLowerCase() === 'failed').length;
      const total = targetRows.length;
      setBulkPublishReport({ dry_run: false, direct_queue: true, summary: { queued, skipped_blocked: blocked, skipped_already_queued: 0, failed }, items: outcomes.map((item) => ({ listing_id: item.listing_id, marketplaces: Object.fromEntries((item.results || []).map((result) => [result.marketplace || 'unknown', result])), error: item.status === 'queued' ? null : item.status })) });
      await reload();
      if (queued) toast.success(`Approved and queued ${queued} of ${total} listing${total === 1 ? '' : 's'} for publish.${blocked ? ` ${blocked} blocked.` : ''}${failed ? ` ${failed} failed.` : ''}`);
      else toast.error(`Approved the selected listings, but nothing was queued.${blocked ? ` ${blocked} blocked by safeguards.` : ''}${failed ? ` ${failed} failed.` : ''}`);
      setSelectedIds([]);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'The approval and publish queue request failed.';
      setBulkPublishReport({ dry_run: false, direct_queue: true, summary: { queued: 0, skipped_blocked: 0, skipped_already_queued: 0, failed: targetRows.length }, items: targetRows.map((listing) => ({ listing_id: listing.id, marketplaces: {}, error: message })) });
      toast.error(message);
    } finally {
      setBulkPublishSubmitting(false);
    }
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
    setBulkPreflightProgress({ completed: 0, total: targetIds.length, marketplaces });
    const marketLabel = (marketplaces || []).map((market) => formatMarketplace(market)).join(', ') || 'Marketplace';
    const loadingToastId = toast.loading(`Running ${marketLabel} preflight for ${targetIds.length} listing${targetIds.length === 1 ? '' : 's'}...`);
    try {
      // eBay plan validation can make remote policy/category calls. Keep each
      // browser request deliberately small so a 25-row review cannot exceed a
      // proxy timeout; surface progress after every completed chunk.
      const chunkSize = (marketplaces || []).includes('ebay') ? 2 : 8;
      const reports = [];
      for (let offset = 0; offset < targetIds.length; offset += chunkSize) {
        const listingIds = targetIds.slice(offset, offset + chunkSize);
        try {
          reports.push(await fetchMarketplacePreflightBulk({
            listing_ids: listingIds,
            marketplaces,
            force_refresh: Boolean(options.forceRefresh),
            only_drafts: Boolean(options.onlyDrafts),
            selected_statuses: options.selectedStatuses || null,
            only_missing_preflight: Boolean(options.onlyMissingPreflight),
            only_stale_preflight: Boolean(options.onlyStalePreflight),
            only_ready_candidates: Boolean(options.onlyReadyCandidates),
            only_blocked_candidates: Boolean(options.onlyBlockedCandidates),
          }));
        } catch (error) {
          reports.push({
            items: listingIds.map((listingId) => ({ listing_id: listingId, marketplaces: {}, error: error instanceof Error ? error.message : 'Preflight request failed.' })),
            marketplaces,
            summary: { total_listings_checked: listingIds.length, total_marketplaces_checked: listingIds.length * (marketplaces || []).length, preflight_failed: listingIds.length },
          });
        }
        const completed = Math.min(offset + listingIds.length, targetIds.length);
        setBulkPreflightProgress({ completed, total: targetIds.length, marketplaces });
        toast.loading(`Running ${marketLabel} preflight: ${completed} of ${targetIds.length} checked…`, { id: loadingToastId });
      }
      const report = reports.reduce((combined, current) => {
        combined.items.push(...(current?.items || []));
        Object.entries(current?.summary || {}).forEach(([key, value]) => {
          if (typeof value === 'number') combined.summary[key] = (combined.summary[key] || 0) + value;
          else if (key === 'blocker_codes' || key === 'warning_codes') {
            combined.summary[key] = { ...(combined.summary[key] || {}) };
            Object.entries(value || {}).forEach(([code, count]) => { combined.summary[key][code] = (combined.summary[key][code] || 0) + Number(count || 0); });
          }
        });
        return combined;
      }, { items: [], marketplaces, summary: {} });
      setBulkPreflightReport(report);
      await reload();
      const summary = report?.summary || {};
      toast.success(
        `${marketLabel} preflight finished. ${summary.ready_listings || 0} ready, ${summary.warning_only_listings || 0} warning-only, ${summary.blocked_listings || 0} blocked.`,
        { id: loadingToastId },
      );
      return report;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Bulk marketplace preflight failed.', { id: loadingToastId });
      return null;
    } finally {
      setBulkPreflightLoading(false);
      setBulkPreflightProgress(null);
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

  const exportRepairQueueCsv = async () => {
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

  const approvePendingRepairQueuePhotos = async (listingId) => {
    const targetListing = listings.find((row) => row.id === listingId);
    const pendingPaths = (targetListing?.listing_images || [])
      .filter((image) => !image?.is_reference && image?.operator_state !== 'approved' && image?.operator_state !== 'rejected')
      .map((image) => image.storage_path)
      .filter(Boolean);
    await approvePhotosForListing(listingId, pendingPaths);
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

  const runPreflightOnLaunchCandidates = async () => {
    const candidateIds = (launchCandidatesReport?.candidates || []).map((item) => item.listing_id);
    if (!candidateIds.length) {
      toast.error('Run the candidate selector first.');
      return;
    }
    setSelectedIds(candidateIds);
    await runBulkMarketplacePreflight(['ebay', 'facebook'], { forceRefresh: true, targetIds: candidateIds });
  };

  const openFirstLaunchCandidate = () => {
    const first = launchCandidatesReport?.candidates?.[0];
    if (!first?.listing_id) {
      toast.error('Run the candidate selector first.');
      return;
    }
    setSelectedListingId(first.listing_id);
    document.getElementById('listing-results')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const exportLaunchCandidatesCsv = async () => {
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
  };

  // The catalog route is an entry point, not an operations dashboard. Keep
  // first paint deliberately small and isolated from expensive legacy
  // preflight/repair widgets; those tools remain available from the detail
  // workspace and their dedicated routes.
  const compactCatalog = true;
  if (compactCatalog) {
    return (
      <AppShell
        active="/listings"
        title="Listings"
        autonomousConfig={autonomousConfig}
        onToggleAutonomous={async () => {
          await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
          await reload();
        }}
      >
        <PageHeader
          eyebrow="Catalog"
          breadcrumbs={[{ label: 'Workspace' }, { label: 'Listings', active: true }]}
          title="Listings"
          description="A fast, paginated catalog for reviewing drafts, live listings, imports, and recovered inventory."
          actions={<div className="flex flex-wrap gap-2"><Button href="/listings/new">New listing</Button><Button href="/settings" variant="outline">Settings</Button></div>}
        />
        <PageFrame>
          <SectionPanel title="Find listings" description={`${listingPagination.total.toLocaleString()} records · ${catalogPageSize} per page`}>
            <div className="grid gap-3 lg:grid-cols-[minmax(220px,1fr)_190px_auto] lg:items-center">
              <input
                value={search}
                onChange={(event) => { setSearch(event.target.value); setCatalogPage(1); }}
                placeholder="Search title or description"
                className="h-10 rounded-[10px] border border-[#d0d5dd] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb]"
              />
              <select value={catalogPageSize} onChange={(event) => { setCatalogPageSize(Number(event.target.value)); setCatalogPage(1); }} className="h-10 rounded-[10px] border border-[#d0d5dd] bg-white px-3 text-sm text-[#101828]">
                {[25, 50, 100, 250].map((size) => <option key={size} value={size}>{size} per page</option>)}
              </select>
              <Button variant="outline" onClick={clearAllFilters}>Clear filters</Button>
            </div>
            <div className="mt-4 flex flex-wrap gap-2" aria-label="Listing filters">
              {LISTING_TABS.map((tab) => <button key={tab.value} type="button" onClick={() => { setActiveTab(tab.value); setCatalogPage(1); }} className={`rounded-full border px-3 py-2 text-sm font-semibold ${activeTab === tab.value ? 'border-[#2563eb] bg-[#eef4ff] text-[#1d4ed8]' : 'border-[#e5e7eb] bg-white text-[#475467]'}`}>{tab.label}</button>)}
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-[#eaecf0] pt-3">
              <Button
                variant="outline"
                size="sm"
                disabled={!filteredListings.length}
                onClick={() => {
                  const pageIds = filteredListings.map((listing) => listing.id);
                  const pageIsSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.includes(id));
                  setSelectedIds((current) => pageIsSelected
                    ? current.filter((id) => !pageIds.includes(id))
                    : Array.from(new Set([...current, ...pageIds])));
                }}
              >
                {filteredListings.length && filteredListings.every((listing) => selectedIds.includes(listing.id)) ? 'Clear page selection' : 'Select all on this page'}
              </Button>
              <p className="text-xs text-[#667085]">Select all applies to the current paginated page ({filteredListings.length} listings), not the entire catalog.</p>
            </div>
          </SectionPanel>
          {selectedIds.length ? (
            <SectionPanel
              title={`${selectedIds.length} listing${selectedIds.length === 1 ? '' : 's'} selected`}
              description="Approval changes only the selected local draft records. Publishing always asks for a separate explicit confirmation and remains subject to marketplace preflight and duplicate safeguards."
            >
              <div className="flex flex-wrap items-center gap-2">
                {workflowPreferences.bulk_approval_enabled && selectedReviewRows.length ? <Button variant="outline" size="sm" onClick={approveSelected}>Approve selected</Button> : null}
                <Button variant="outline" size="sm" onClick={approveAndPublishSelected}>{selectedReviewRows.length ? 'Approve & queue publish' : 'Queue selected for publishing'}</Button>
                <Button variant="outline" size="sm" onClick={() => runBulkMarketplacePreflight(['ebay'])} disabled={bulkPreflightLoading}>Run eBay preflight</Button>
                <Button variant="outline" size="sm" onClick={() => setSelectedIds([])}>Clear selection</Button>
              </div>
            </SectionPanel>
          ) : null}
          {pendingBulkPublish ? (
            <SectionPanel title="Confirm live eBay queue" tone="warning" description={`${pendingBulkPublish.listingIds.length} selected listing${pendingBulkPublish.listingIds.length === 1 ? '' : 's'} will be approved where needed, then sent to the eBay publishing worker. This is a real marketplace action; the result panel and Jobs page will show the actual outcome.`}>
              <div className="flex max-w-xl flex-col gap-3 sm:flex-row sm:items-end">
                <label className="flex flex-1 cursor-pointer items-start gap-2 text-sm text-[#344054]">
                  <input type="checkbox" checked={bulkPublishAcknowledged} onChange={(event) => setBulkPublishAcknowledged(event.target.checked)} className="mt-1 h-4 w-4" />
                  <span>I understand this queues real eBay publication work for these listings. I will review the resulting job statuses before assuming any listing is live.</span>
                </label>
                <Button
                  size="sm"
                  disabled={!bulkPublishAcknowledged || bulkPublishSubmitting}
                  onClick={() => approveAndPublishSelected({ confirmedEbay: true, targetIds: pendingBulkPublish.listingIds })}
                >
                  {bulkPublishSubmitting ? 'Queueing…' : `Confirm & queue ${pendingBulkPublish.listingIds.length}`}
                </Button>
                <Button variant="outline" size="sm" disabled={bulkPublishSubmitting} onClick={() => { setPendingBulkPublish(null); setBulkPublishAcknowledged(false); }}>Cancel</Button>
              </div>
            </SectionPanel>
          ) : null}
          {bulkPublishReport ? (
            <SectionPanel
              title={bulkPublishReport.direct_queue ? 'Latest publish queue result' : 'Latest ready-only publish result'}
              description={bulkPublishReport.direct_queue
                ? 'Queued means PosterPro handed the work to a marketplace worker. Open Jobs to see whether the marketplace accepted, blocked, or failed the remote action.'
                : 'This report shows the outcome of the latest marketplace-ready queue run.'}
            >
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <MetricCard label="Queued" value={bulkPublishReport?.summary?.queued || 0} detail="Worker tasks created." />
                <MetricCard label="Blocked" value={bulkPublishReport?.summary?.skipped_blocked || 0} detail="Preflight or duplicate safeguard stopped these." />
                <MetricCard label="Already queued" value={bulkPublishReport?.summary?.skipped_already_queued || 0} detail="Existing work was retained." />
                <MetricCard label="Failed" value={bulkPublishReport?.summary?.failed || 0} detail="Could not be queued." />
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button href="/jobs" variant="outline" size="sm">Open Jobs &amp; progress</Button>
                <Button variant="outline" size="sm" onClick={() => setBulkPublishReport(null)}>Dismiss</Button>
              </div>
            </SectionPanel>
          ) : null}
          {bulkPreflightProgress ? (
            <SectionPanel title="Marketplace preflight in progress" description={`Checking ${bulkPreflightProgress.completed} of ${bulkPreflightProgress.total} selected listing${bulkPreflightProgress.total === 1 ? '' : 's'} in small timeout-safe batches.`}>
              <div className="h-2 overflow-hidden rounded-full bg-[#eaecf0]" aria-label="Preflight progress">
                <div className="h-full rounded-full bg-[#2563eb] transition-all" style={{ width: `${Math.max(3, Math.round((bulkPreflightProgress.completed / Math.max(1, bulkPreflightProgress.total)) * 100))}%` }} />
              </div>
            </SectionPanel>
          ) : null}
          {listingError ? <SectionPanel tone="danger" title="Catalog could not load"><p className="text-sm">{listingError}</p></SectionPanel> : null}
          <SectionPanel title={`${activeTabLabel} · page ${listingPagination.page} of ${listingPagination.total_pages}`} description="Select listings for approval or publishing, or open one to review its marketplace preview, photos, details, and readiness.">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-[#eaecf0] pb-3">
              <p className="text-sm text-[#667085]">Showing {filteredListings.length} records on this page.</p>
              <div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={listingPagination.page <= 1} onClick={() => { setCatalogPage(Math.max(1, listingPagination.page - 1)); setSelectedIds([]); }}>Previous</Button><span className="text-sm font-semibold text-[#344054]">{listingPagination.page} / {listingPagination.total_pages}</span><Button size="sm" variant="outline" disabled={listingPagination.page >= listingPagination.total_pages} onClick={() => { setCatalogPage(Math.min(listingPagination.total_pages, listingPagination.page + 1)); setSelectedIds([]); }}>Next</Button></div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {filteredListings.map((listing) => (
                <article key={listing.id} className={`rounded-[16px] border bg-white p-3 transition ${selectedIds.includes(listing.id) ? 'border-[#2563eb] ring-2 ring-[#dbeafe]' : 'border-[#e5e7eb] hover:border-[#a9c5ff] hover:shadow-[0_10px_24px_rgba(37,99,235,0.08)]'}`}>
                  <div className="flex gap-2">
                    <label className="flex w-5 shrink-0 cursor-pointer items-start pt-1" title={`Select ${getListingTitle(listing)}`}>
                      <input
                        type="checkbox"
                        checked={selectedIds.includes(listing.id)}
                        onChange={() => toggleRow(listing.id)}
                        aria-label={`Select ${getListingTitle(listing)}`}
                        className="h-4 w-4 rounded border-[#98a2b3] text-[#2563eb]"
                      />
                    </label>
                    <Link href={`/listings/${listing.id}?mode=preview`} className="group min-w-0 flex-1">
                  <div className="flex gap-3">
                    <div className="h-20 w-20 shrink-0 overflow-hidden rounded-[10px] bg-[#f2f4f7]">
                      {getListingThumbnail(listing) ? <img src={toThumbnailImageUrl(getListingThumbnail(listing), 240, 240)} alt="" loading="lazy" decoding="async" className="h-full w-full object-cover" /> : <div className="flex h-full items-center justify-center text-xs text-[#98a2b3]">No image</div>}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="line-clamp-2 text-sm font-semibold text-[#101828]">{getListingTitle(listing)}</p>
                      <p className="mt-1 text-base font-bold text-[#101828]">${getListingPrice(listing)}</p>
                      <p className="mt-1 text-xs text-[#667085]">#{listing.id} · {getListingBucket(listing).replaceAll('_', ' ')}</p>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {(getListingMarketplaces(listing, enabledPlatforms, { allowFallback: false }).length ? getListingMarketplaces(listing, enabledPlatforms, { allowFallback: false }) : ['unassigned']).map((market) => <span key={market} className="rounded-full bg-[#f2f4f7] px-2 py-1 text-[11px] font-semibold text-[#475467]">{formatMarketplace(market)}</span>)}
                  </div>
                  </Link>
                  </div>
                </article>
              ))}
              {!filteredListings.length ? <p className="col-span-full rounded-[12px] border border-dashed border-[#d0d5dd] p-6 text-center text-sm text-[#667085]">No listings match this page’s filters.</p> : null}
            </div>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-[#eaecf0] pt-4">
              <p className="text-sm text-[#667085]">Showing {filteredListings.length} records on this page.</p>
              <div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={listingPagination.page <= 1} onClick={() => setCatalogPage(Math.max(1, listingPagination.page - 1))}>Previous</Button><span className="text-sm font-semibold text-[#344054]">{listingPagination.page} / {listingPagination.total_pages}</span><Button size="sm" variant="outline" disabled={listingPagination.page >= listingPagination.total_pages} onClick={() => setCatalogPage(Math.min(listingPagination.total_pages, listingPagination.page + 1))}>Next</Button></div>
            </div>
          </SectionPanel>
        </PageFrame>
      </AppShell>
    );
  }

  return (
    <AppShell
      active="/listings"
      title="Listings"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
    >
      <PageHeader
        eyebrow="Listings workspace"
        breadcrumbs={[{ label: 'Workspace' }, { label: 'Listings', active: true }]}
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

      <ListingsWorkspaceErrorBoundary
        resetKey={[
          activeTab,
          viewMode,
          filteredListings.length,
          selectedListingId || 'none',
          selectedIds.length,
          workspaceMode,
        ].join(':')}
      >
      <PageFrame>
      <PageBand id="listing-toolbar" className="overflow-hidden bg-[radial-gradient(circle_at_top_left,#fff1e6_0%,#ffffff_42%,#eef4ff_100%)]">
      <ListingsOverviewBand
        activeTabLabel={activeTabLabel}
        filteredCount={filteredListings.length}
        selectedCount={selectedIds.length}
        viewMode={viewMode}
        listingMetrics={listingMetrics}
        bulkPreflightSummary={bulkPreflightSummary}
        publishJobStats={publishJobStats}
        tabs={<ListingsQueueTabs listingTabs={LISTING_TABS} tabCounts={tabCounts} activeTab={activeTab} selectTab={selectTab} />}
        toolbar={(
          <ListingsToolbar
            search={search}
            setSearch={setSearch}
            marketFilter={marketFilter}
            setMarketFilter={setMarketFilter}
            sourceFilter={sourceFilter}
            setSourceFilter={setSourceFilter}
            readinessFilter={readinessFilter}
            setReadinessFilter={setReadinessFilter}
            activeTab={activeTab}
            setActiveTab={setActiveTab}
            setSelectedIds={setSelectedIds}
            filteredListingsLength={filteredListings.length}
            viewMode={viewMode}
            setViewMode={setViewMode}
            onBulkRetryFetchImages={async () => {
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
            onRetryMissingVineImages={async () => {
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
            filterOptions={FILTER_OPTIONS}
            sourceOptions={SOURCE_OPTIONS}
            readinessFilterOptions={READINESS_FILTER_OPTIONS}
            listingTabs={LISTING_TABS}
            onClearAllFilters={clearAllFilters}
            onFiltersChanged={() => setCatalogPage(1)}
            catalogTotal={listingPagination.total}
            catalogPage={listingPagination.page}
            catalogPageSize={catalogPageSize}
            onCatalogPageSizeChange={(nextSize) => {
              setCatalogPageSize(nextSize);
              setCatalogPage(1);
              setSelectedIds([]);
            }}
          />
        )}
      />
      <div className="border-t border-[#e5e7eb] bg-white/82 px-5 py-4 lg:px-6">
        <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] xl:items-center">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#667085]">Workspace mode</p>
            <h3 className="mt-1 text-lg font-semibold tracking-[-0.03em] text-[#101828]">Choose a focused workspace</h3>
            <p className="mt-1 max-w-xl text-sm leading-6 text-[#667085]">
              Results stays primary; repair, launch, and queue tools stay available without consuming the whole screen.
            </p>
            <div className="mt-3 inline-flex rounded-full border border-[#dbe5f5] bg-white px-3 py-2 text-xs font-semibold uppercase tracking-[0.12em] text-[#2563eb]">
              Current mode: {workspaceModes.find((mode) => mode.key === workspaceMode)?.label}
            </div>
          </div>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
          {workspaceModes.map((mode) => {
            const Icon = mode.icon;
            const active = workspaceMode === mode.key;
            return (
              <button
                key={mode.key}
                type="button"
                onClick={() => setWorkspaceMode(mode.key)}
                className={[
                  'rounded-[14px] border p-3 text-left transition',
                  active
                    ? 'border-[#bfd2ff] bg-[#f4f8ff] shadow-[0_14px_40px_rgba(37,99,235,0.12)]'
                    : 'border-[#e5e7eb] bg-white hover:border-[#cdd8ea] hover:bg-[#fbfcff]',
                ].join(' ')}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className={`inline-flex h-9 w-9 items-center justify-center rounded-xl border ${active ? 'border-[#bfd2ff] bg-white text-[#2563eb]' : 'border-[#e5e7eb] bg-[#f8fafc] text-[#667085]'}`}>
                    <Icon size={16} />
                  </span>
                  <span className="text-2xl font-semibold tracking-[-0.03em] text-[#101828]">{mode.count}</span>
                </div>
                <p className="mt-2 text-sm font-semibold text-[#101828]">{mode.label}</p>
                <p className="mt-1 hidden text-xs leading-5 text-[#667085] xl:block">{mode.description}</p>
              </button>
            );
          })}
        </div>
        </div>
      </div>
      </PageBand>

      {(workspaceMode === 'results' || workspaceMode === 'all') ? (
      <SectionPanel
        id="listing-results"
        title={`${activeTabLabel} workspace`}
        description={`Showing ${filteredListings.length} ${filteredListings.length === 1 ? 'listing' : 'listings'} in the current queue. This mode keeps the working list primary and pushes everything else out of the way.`}
        className="overflow-hidden"
      >
      <ListingsBulkActionBar
        selectedCount={selectedIds.length}
        left={<p className="text-sm font-medium text-[#101828]">{selectedIds.length} selected</p>}
        actions={
          <>
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
          </>
        }
      />

      <div className={`grid gap-5 ${selectedListing ? 'xl:grid-cols-[minmax(0,1fr)_480px]' : 'grid-cols-1'}`}>
        {viewMode === 'table' ? (
          <DataTable
          columns={[
            {
              key: 'thumbnail',
              label: 'Thumbnail',
              render: (listing) => (
                <ListingsThumbnail src={getListingThumbnail(listing)} alt={getListingTitle(listing)} />
              ),
            },
            {
              key: 'title',
              label: 'Title',
              cellClassName: 'min-w-[280px]',
              render: (listing) => (
                <ListingsTitleCell
                  listing={listing}
                  getListingTitle={getListingTitle}
                  getReadinessSummary={getReadinessSummary}
                  getListingImageCount={getListingImageCount}
                  isAmazonVineSource={isAmazonVineSource}
                  getListingBucket={getListingBucket}
                  publishSingleListing={publishSingleListing}
                  publishing={publishing}
                  approveListing={approveListing}
                  deleteListing={deleteListing}
                  confirmDeleteListings={confirmDeleteListings}
                  archiveListing={archiveListing}
                  isArchivedListing={isArchivedListing}
                  router={router}
                  getMarketplacePreflightSummary={getMarketplacePreflightSummary}
                  getMarketplacePreflightStatus={getMarketplacePreflightStatus}
                  getMarketplacePreflightTone={getMarketplacePreflightTone}
                  getMarketplaceTopBlocker={getMarketplaceTopBlocker}
                  getMarketplacePreflightAgeLabel={getMarketplacePreflightAgeLabel}
                />
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
              render: (listing) => (
                <ListingsStatusCell
                  listing={listing}
                  getListingBucket={getListingBucket}
                  errors={errors}
                  getListingFailureMessage={getListingFailureMessage}
                />
              ),
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
          emptyState={<ListingsWorkspaceEmptyState activeTabLabel={LISTING_TABS.find((t) => t.value === activeTab)?.label} compact />}
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {filteredListings.length ? (
              filteredListings.map((listing) => {
                const selected = selectedIds.includes(listing.id);
                return (
                  <ListingsGridCard
                    key={listing.id}
                    listing={listing}
                    selected={selected}
                    setSelectedListingId={setSelectedListingId}
                    toggleRow={toggleRow}
                    getListingThumbnail={getListingThumbnail}
                    getListingTitle={getListingTitle}
                    getReadinessSummary={getReadinessSummary}
                    getListingImageCount={getListingImageCount}
                    getMarketplacePreflightSummary={getMarketplacePreflightSummary}
                    getMarketplacePreflightStatus={getMarketplacePreflightStatus}
                    getMarketplacePreflightTone={getMarketplacePreflightTone}
                    getMarketplaceTopBlocker={getMarketplaceTopBlocker}
                    getMarketplacePreflightAgeLabel={getMarketplacePreflightAgeLabel}
                    getListingBucket={getListingBucket}
                    getListingMarketplaces={getListingMarketplaces}
                    enabledPlatforms={enabledPlatforms}
                    formatMarketplace={formatMarketplace}
                    workflowPreferences={workflowPreferences}
                    publishSingleListing={publishSingleListing}
                    publishing={publishing}
                    approveListing={approveListing}
                    isAmazonVineSource={isAmazonVineSource}
                    isArchivedListing={isArchivedListing}
                    archiveListing={archiveListing}
                    deleteListing={deleteListing}
                    confirmDeleteListings={confirmDeleteListings}
                    router={router}
                    errors={errors}
                    getListingFailureMessage={getListingFailureMessage}
                    getListingPrice={getListingPrice}
                  />
                );
              })
            ) : (
              <ListingsWorkspaceEmptyState activeTabLabel={LISTING_TABS.find((t) => t.value === activeTab)?.label} />
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
      <div className="mt-5 flex flex-col gap-3 rounded-[16px] border border-[#e5e7eb] bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-[#475467]">
          Showing page {listingPagination.page} of {listingPagination.total_pages} · {listingPagination.total.toLocaleString()} listings in this catalog.
        </p>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" disabled={listingPagination.page <= 1} onClick={() => { setCatalogPage(Math.max(1, listingPagination.page - 1)); setSelectedIds([]); }}>
            Previous
          </Button>
          <span className="rounded-[8px] bg-[#f2f4f7] px-3 py-2 text-xs font-semibold text-[#344054]">
            {listingPagination.page} / {listingPagination.total_pages}
          </span>
          <Button size="sm" variant="outline" disabled={listingPagination.page >= listingPagination.total_pages} onClick={() => { setCatalogPage(Math.min(listingPagination.total_pages, listingPagination.page + 1)); setSelectedIds([]); }}>
            Next
          </Button>
        </div>
      </div>
      </SectionPanel>
      ) : null}

      {(workspaceMode === 'repair' || workspaceMode === 'all') ? (
      <ListingsCollapsibleSection
        id="listing-status"
        title="Workspace signals and repair tools"
        description="Pricing posture, readiness counts, eBay preflight summary, and the repair queue live together here instead of crowding the first screen."
        badge={`${listingMetrics.blockedCount} blocked`}
        defaultOpen
      >
        <PageSplit columnsClassName="xl:grid-cols-[minmax(0,1fr)_380px]">
      <PageMain>
        {listingError ? (
          <SectionPanel
            title="Catalog load needs attention"
            description="PosterPro did not receive a listings response, so it is intentionally not presenting the catalog as empty."
            tone="danger"
            action={<Button size="sm" variant="outline" onClick={() => reload()}>Retry catalog</Button>}
          >
            <p className="text-sm text-[var(--pp-text-muted)]">
              {listingError} Signed-in account: #{user?.id || 'unknown'}.
            </p>
          </SectionPanel>
        ) : null}
        {!listingError && listings.length === 0 ? (
          <SectionPanel
            title="No listings belong to this signed-in account"
            description="PosterPro is showing the complete catalog for the current account; it has not applied a hidden recovery or Vine filter."
            action={<Button size="sm" variant="outline" href="/login">Switch account</Button>}
          >
            <p className="text-sm text-[var(--pp-text-muted)]">
              Signed-in account: #{user?.id || 'unknown'}. If you expected recovered inventory or Amazon Vine drafts here, sign out and use the recovery operator account, then select Clear all filters.
            </p>
          </SectionPanel>
        ) : null}
            <ListingsStatusGrid workflowPreferences={workflowPreferences} listingMetrics={listingMetrics} formatMarketplace={formatMarketplace} />
          </PageMain>

          <PageAside>
            <ListingsPreflightPanel bulkPreflightSummary={bulkPreflightSummary} selectedRowsLength={selectedRows.length} />
            <ListingsRepairQueuePanel
              repairQueueImageStatusFilter={repairQueueImageStatusFilter}
              setRepairQueueImageStatusFilter={setRepairQueueImageStatusFilter}
              refreshRepairQueue={refreshRepairQueue}
              repairQueueLoading={repairQueueLoading}
              exportRepairQueueCsv={exportRepairQueueCsv}
              repairQueueReport={repairQueueReport}
              setSelectedListingId={setSelectedListingId}
              runSingleListingPreflight={(listingId) => runBulkMarketplacePreflight(['ebay'], { forceRefresh: true, targetIds: [listingId] })}
              applyRepairAction={applyRepairAction}
              uploadActualPhotosToListing={uploadActualPhotosToListing}
              approvePendingPhotos={approvePendingRepairQueuePhotos}
            />
          </PageAside>
        </PageSplit>
      </ListingsCollapsibleSection>
      ) : null}

      {(workspaceMode === 'launch' || workspaceMode === 'all') ? (
      <ListingsCollapsibleSection
        title="Launch drill and publish readiness"
        description="Candidate selection, dry-run launch QA, and the latest publish-ready reports are grouped here so they stay available without owning the first screen."
        badge={launchCandidatesReport?.candidates?.length ? `${launchCandidatesReport.candidates.length} launch candidates` : 'Launch QA'}
        defaultOpen
      >
        <div className="space-y-6">
          <ListingsLaunchCandidatesPanel
            launchCandidatesLoading={launchCandidatesLoading}
            refreshLaunchCandidates={refreshLaunchCandidates}
            runPreflightOnCandidates={runPreflightOnLaunchCandidates}
            runLaunchDrillForCandidates={runLaunchDrillForCandidates}
            openFirstCandidate={openFirstLaunchCandidate}
            exportLaunchCandidatesCsv={exportLaunchCandidatesCsv}
            ebayAccountReadiness={ebayAccountReadiness}
            launchCandidatesReport={launchCandidatesReport}
          />
          {bulkPreflightReport ? (
            <ListingsReportPanel
              title="Last bulk preflight report"
              description="Review the latest bulk run without opening every listing."
              metrics={[
                { label: 'Ready for eBay', value: bulkPreflightReport?.summary?.ready_for_ebay || 0, detail: 'Rows whose current preflight is ready for eBay queueing.' },
                { label: 'Ready for Facebook', value: bulkPreflightReport?.summary?.ready_for_facebook || 0, detail: 'Rows whose current preflight is ready for Facebook assisted handoff.' },
                { label: 'Blocked', value: bulkPreflightReport?.summary?.blocked || 0, detail: 'Rows blocked by at least one marketplace.' },
                { label: 'Preflight failed', value: bulkPreflightReport?.summary?.preflight_failed || 0, detail: 'Rows that returned a backend validation or provider failure.' },
              ]}
              items={(bulkPreflightReport?.items || []).slice(0, 6)}
              itemKeyPrefix="bulk-preflight"
              renderMarketStatus={(item, market, data) => (
                <span key={`${item.listing_id}-${market}`} className="pp-chip">
                  {market.toUpperCase()}: {String(data?.status || 'unknown').replaceAll('_', ' ')}
                </span>
              )}
              renderFooter={(item) => (
                item.top_blocker_code ? <p className="mt-2 text-xs text-[#b42318]">Top blocker: {item.top_blocker_code}</p> : null
              )}
            />
          ) : null}

          {bulkPublishReport ? (
            <ListingsReportPanel
              title="Last bulk publish-ready run"
              description={`Dry-run ${bulkPublishReport?.dry_run ? 'preview' : 'live queue'} for ready-only marketplace publishing.`}
              metrics={[
                { label: 'Queued', value: bulkPublishReport?.summary?.queued || 0, detail: 'Listings queued successfully in live mode.' },
                { label: 'Skipped blocked', value: bulkPublishReport?.summary?.skipped_blocked || 0, detail: 'Listings blocked by a marketplace preflight issue.' },
                { label: 'Warnings need confirm', value: bulkPublishReport?.summary?.skipped_warning_requires_confirmation || 0, detail: 'Warning-only listings that were not allowed into live mode.' },
                { label: 'Already queued', value: bulkPublishReport?.summary?.skipped_already_queued || 0, detail: 'Listings already had pending marketplace work.' },
              ]}
              items={(bulkPublishReport?.items || []).slice(0, 6)}
              itemKeyPrefix="bulk-publish"
              renderMarketStatus={(item, market, data) => (
                <span key={`${item.listing_id}-${market}`} className="pp-chip">
                  {market.toUpperCase()}: {String(data?.status || 'unknown').replaceAll('_', ' ')}
                </span>
              )}
              renderFooter={(item) => (
                <>
                  {item.blocked_marketplaces?.length ? (
                    <p className="mt-2 text-xs text-[#b42318]">Blocked: {item.blocked_marketplaces.join(', ')}</p>
                  ) : null}
                  {item.warning_marketplaces?.length ? (
                    <p className="mt-1 text-xs text-[#8a4b10]">Warnings: {item.warning_marketplaces.join(', ')}</p>
                  ) : null}
                </>
              )}
            />
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
        </div>
      </ListingsCollapsibleSection>
      ) : null}

      {(workspaceMode === 'queue' || workspaceMode === 'all') ? (
      <ListingsCollapsibleSection
        id="listing-publish-queue"
        title="Queue monitoring"
        description="Live worker progress and a direct handoff into the jobs console."
        badge={`${publishJobStats.queued} queued`}
        defaultOpen
      >
        <SectionPanel
          noPadding
          action={<Link href="/jobs" className="text-sm font-medium text-[#2563eb]">Open jobs console</Link>}
        >
          <div className="grid gap-3 p-5 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Queued / running" value={publishJobStats.queued} detail="Jobs still moving through the worker." />
            <MetricCard label="Completed" value={publishJobStats.completed} detail="Publish jobs that finished successfully." />
            <MetricCard label="Failed" value={publishJobStats.failed} detail="Jobs that need attention or retry." />
            <MetricCard label="Progress" value={`${publishJobStats.progress}%`} detail={`${publishJobStats.completed} of ${publishJobStats.total} publish jobs completed`} />
          </div>
        </SectionPanel>
      </ListingsCollapsibleSection>
      ) : null}
      </PageFrame>
      </ListingsWorkspaceErrorBoundary>
    </AppShell>
  );
}

ListingsPage.requireAuth = true;

// Listing actions are safety-critical and must never be served from a stale
// page document after a deployment. Next's hashed JS handles normal cache
// invalidation; this header also prevents a browser/proxy retaining an older
// HTML shell with obsolete bulk-action handlers.
export async function getServerSideProps({ res }) {
  res.setHeader('Cache-Control', 'no-store, max-age=0, must-revalidate');
  return { props: {} };
}
