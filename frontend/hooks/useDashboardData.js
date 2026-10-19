import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  fetchAlerts,
  fetchAnalyticsOverview,
  fetchAutonomousConfig,
  fetchClusters,
  fetchEbayOfferDashboard,
  fetchListings,
  fetchListingTemplates,
  fetchMarketplaces,
  fetchPlatformConfig,
  fetchPrediction,
  fetchPricingRecommendation,
  fetchStorageUnitBatches,
  optimizeListing,
} from "../lib/api";
import { readListingPageCache, writeListingPageCache } from "../lib/listingPageCache.mjs";

export default function useDashboardData(userId, options = {}) {
  const asArray = (value) => (Array.isArray(value) ? value : []);
  const includeLeadInsights = Boolean(options.includeLeadInsights);
  const includeClusters = options.includeClusters !== false;
  const includeListings = options.includeListings !== false;
  const includeMarketplaces = options.includeMarketplaces !== false;
  const includeAnalytics = options.includeAnalytics !== false;
  const includeAlerts = options.includeAlerts !== false;
  const includeAutonomousConfig = options.includeAutonomousConfig !== false;
  const includeOfferDashboard = options.includeOfferDashboard !== false;
  const includePlatformConfig = options.includePlatformConfig !== false;
  const includeStorageBatches = options.includeStorageBatches !== false;
  const includeListingTemplates = options.includeListingTemplates !== false;
  const paginateListings = options.paginateListings === true;
  const listingPage = Math.max(1, Number(options.listingPage || 1));
  const listingPageSize = Math.min(250, Math.max(1, Number(options.listingPageSize || 25)));
  const listingSourceType = String(options.listingSourceType || '');
  const listingMarketplace = String(options.listingMarketplace || '');
  const listingReadiness = String(options.listingReadiness || '');
  const listingSearch = String(options.listingSearch || '');
  const listingQueue = String(options.listingQueue || '');
  const listingSortBy = String(options.listingSortBy || '');
  const listingSortDir = String(options.listingSortDir || '');
  const listingSummaryOnly = options.listingSummaryOnly !== false;
  const listingPageCacheKey = JSON.stringify({
    userId, paginateListings, listingPage, listingPageSize, listingSourceType,
    listingMarketplace, listingReadiness, listingSearch, listingQueue,
    listingSortBy, listingSortDir, listingSummaryOnly,
  });
  const initialListingPage = readListingPageCache(listingPageCacheKey);
  const [clusters, setClusters] = useState([]);
  const [listings, setListings] = useState(() => initialListingPage?.items || []);
  const [marketplaces, setMarketplaces] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [recommendation, setRecommendation] = useState(null);
  const [prediction, setPrediction] = useState(null);
  const [optimization, setOptimization] = useState(null);
  const [autonomousConfig, setAutonomousConfig] = useState({
    autonomous_mode: true,
    autonomous_dry_run: false,
  });
  const [offerDashboard, setOfferDashboard] = useState({
    active_offers: [],
    decision_log: [],
  });
  const [enabledPlatforms, setEnabledPlatforms] = useState(["ebay"]);
  const [storageBatches, setStorageBatches] = useState([]);
  const [listingTemplates, setListingTemplates] = useState([]);
  const [listingError, setListingError] = useState(null);
  const [listingPagination, setListingPagination] = useState(() => initialListingPage?.pagination || { page: listingPage, page_size: listingPageSize, total: 0, total_pages: 1, aggregate_value: 0, average_value: null, priced_count: 0, price_source_counts: null, missing_price_count: 0, shipping_min_total: null, shipping_max_total: null, listing_fee_total: null, sold_item_fee_total: null, expected_sold_fee_min_total: null, expected_sold_fee_max_total: null, net_before_shipping_total: null, net_after_shipping_min_total: null, net_after_shipping_max_total: null });
  const [listingBucketCounts, setListingBucketCounts] = useState(() => initialListingPage?.bucketCounts || {});
  const staticCacheKey = useMemo(
    () => JSON.stringify({
      userId,
      includeClusters,
      includeMarketplaces,
      includeAnalytics,
      includeAlerts,
      includeAutonomousConfig,
      includeOfferDashboard,
      includePlatformConfig,
      includeStorageBatches,
      includeListingTemplates,
    }),
    [
      userId,
      includeClusters,
      includeMarketplaces,
      includeAnalytics,
      includeAlerts,
      includeAutonomousConfig,
      includeOfferDashboard,
      includePlatformConfig,
      includeStorageBatches,
      includeListingTemplates,
    ],
  );
  const staticCacheRef = useRef({ key: null, loaded: false });
  const listingRequestRef = useRef(0);

  useEffect(() => {
    const cached = readListingPageCache(listingPageCacheKey);
    if (!cached) return;
    setListings(cached.items || []);
    setListingPagination(cached.pagination || { page: listingPage, page_size: listingPageSize, total: 0, total_pages: 1 });
    setListingBucketCounts(cached.bucketCounts || {});
    setListingError(null);
  }, [listingPage, listingPageCacheKey, listingPageSize]);

  const reload = useCallback(async () => {
    if (!userId) return;
    const requestId = ++listingRequestRef.current;
    const shouldReloadStatic = staticCacheRef.current.key !== staticCacheKey || !staticCacheRef.current.loaded;
    const staticRequests = shouldReloadStatic ? [
      includeClusters ? fetchClusters() : Promise.resolve([]),
      includeMarketplaces ? fetchMarketplaces() : Promise.resolve({ marketplaces: [] }),
      includeAnalytics ? fetchAnalyticsOverview(userId) : Promise.resolve(null),
      includeAlerts ? fetchAlerts(userId) : Promise.resolve({ alerts: [] }),
      includeAutonomousConfig ? fetchAutonomousConfig() : Promise.resolve({
        autonomous_mode: true,
        autonomous_dry_run: false,
      }),
      includeOfferDashboard ? fetchEbayOfferDashboard(userId) : Promise.resolve({
        active_offers: [],
        decision_log: [],
      }),
      includePlatformConfig ? fetchPlatformConfig(userId) : Promise.resolve({ enabled_platforms: ["ebay"] }),
      includeStorageBatches ? fetchStorageUnitBatches() : Promise.resolve([]),
      includeListingTemplates ? fetchListingTemplates(userId) : Promise.resolve([]),
    ] : [];

    const settled = await Promise.allSettled([
      includeListings ? (
        paginateListings
        ? fetchListings({
            page: listingPage,
            pageSize: listingPageSize,
            sourceType: listingSourceType,
            marketplace: listingMarketplace,
            readiness: listingReadiness,
            search: listingSearch,
            queue: listingQueue,
            sortBy: listingSortBy,
            sortDir: listingSortDir,
            summaryOnly: listingSummaryOnly,
          })
          : fetchListings({ sortBy: listingSortBy, sortDir: listingSortDir, summaryOnly: listingSummaryOnly })
      ) : Promise.resolve([]),
      ...staticRequests,
    ]);

    const listingsResult = settled[0];
    // A slower response for an older page/filter must never overwrite the
    // catalog selected by the operator (e.g. 25 rows arriving after 100).
    if (requestId !== listingRequestRef.current) return;
    const listingResult = listingsResult.status === "fulfilled" ? listingsResult.value : [];
    const l = Array.isArray(listingResult) ? listingResult : asArray(listingResult?.items);

    const nextPagination = Array.isArray(listingResult)
      ? { page: 1, page_size: l?.length || listingPageSize, total: l?.length || 0, total_pages: 1, aggregate_value: 0, average_value: 0, missing_price_count: 0, shipping_min_total: 0, shipping_max_total: 0, listing_fee_total: 0, sold_item_fee_total: 0, net_before_shipping_total: 0, net_after_shipping_min_total: 0, net_after_shipping_max_total: 0 }
      : {
        page: Number(listingResult?.page || listingPage),
        page_size: Number(listingResult?.page_size || listingPageSize),
        total: Number(listingResult?.total || 0),
        total_pages: Number(listingResult?.total_pages || 1),
        bucket_counts: listingResult?.bucket_counts || {},
        aggregate_value: listingResult?.aggregate_value == null ? 0 : Number(listingResult.aggregate_value),
        average_value: listingResult?.average_value == null ? null : Number(listingResult.average_value),
        priced_count: Number(listingResult?.priced_count ?? listingResult?.total ?? 0),
        price_source_counts: listingResult?.price_source_counts ?? null,
        missing_price_count: Number(listingResult?.missing_price_count || 0),
        shipping_min_total: listingResult?.shipping_min_total == null ? null : Number(listingResult.shipping_min_total),
        shipping_max_total: listingResult?.shipping_max_total == null ? null : Number(listingResult.shipping_max_total),
        listing_fee_total: listingResult?.listing_fee_total == null ? null : Number(listingResult.listing_fee_total),
        sold_item_fee_total: listingResult?.sold_item_fee_total == null ? null : Number(listingResult.sold_item_fee_total),
        expected_sold_fee_min_total: listingResult?.expected_sold_fee_min_total == null ? null : Number(listingResult.expected_sold_fee_min_total),
        expected_sold_fee_max_total: listingResult?.expected_sold_fee_max_total == null ? null : Number(listingResult.expected_sold_fee_max_total),
        net_before_shipping_total: listingResult?.net_before_shipping_total == null ? null : Number(listingResult.net_before_shipping_total),
        net_after_shipping_min_total: listingResult?.net_after_shipping_min_total == null ? null : Number(listingResult.net_after_shipping_min_total),
        net_after_shipping_max_total: listingResult?.net_after_shipping_max_total == null ? null : Number(listingResult.net_after_shipping_max_total),
      };
    const nextBucketCounts = Array.isArray(listingResult) ? {} : (listingResult?.bucket_counts || {});
    writeListingPageCache(listingPageCacheKey, { items: l || [], pagination: nextPagination, bucketCounts: nextBucketCounts });

    setListings(l || []);
    setListingPagination(nextPagination);
    setListingBucketCounts(nextBucketCounts);

    if (shouldReloadStatic) {
      const [
        clustersResult,
        marketplacesResult,
        analyticsResult,
        alertsResult,
        autoConfigResult,
        offerDataResult,
        platformConfigResult,
        batchesResult,
        templatesResult,
      ] = settled.slice(1);
      setClusters(clustersResult.status === "fulfilled" ? asArray(clustersResult.value) : []);
      const marketplacesValue = marketplacesResult.status === "fulfilled" ? marketplacesResult.value : { marketplaces: [] };
      const analyticsValue = analyticsResult.status === "fulfilled" ? analyticsResult.value : null;
      const alertsValue = alertsResult.status === "fulfilled" ? alertsResult.value : { alerts: [] };
      const autoConfigValue = autoConfigResult.status === "fulfilled" ? autoConfigResult.value : {
        autonomous_mode: true,
        autonomous_dry_run: false,
      };
      const offerDataValue = offerDataResult.status === "fulfilled" ? offerDataResult.value : {
        active_offers: [],
        decision_log: [],
      };
      const platformConfigValue = platformConfigResult.status === "fulfilled" ? platformConfigResult.value : { enabled_platforms: ["ebay"] };
      const batchesValue = batchesResult.status === "fulfilled" ? batchesResult.value : [];
      const templatesValue = templatesResult.status === "fulfilled" ? templatesResult.value : [];
      setMarketplaces(asArray(marketplacesValue?.marketplaces));
      setAnalytics(analyticsValue);
      setAlerts(asArray(alertsValue?.alerts));
      setAutonomousConfig({ ...(autoConfigValue || {}), loaded: autoConfigResult.status === "fulfilled" });
      setOfferDashboard(offerDataValue);
      setEnabledPlatforms(platformConfigValue?.enabled_platforms || ["ebay"]);
      setStorageBatches(asArray(batchesValue));
      setListingTemplates(asArray(templatesValue));
      staticCacheRef.current = {
        key: staticCacheKey,
        loaded: true,
      };
    }
    // Do not make a failed authenticated catalog request look like an empty
    // catalog.  The Listings page has thousands of records for the recovery
    // operator, and an expired session/network timeout must be actionable.
    setListingError(
      includeListings && listingsResult.status === "rejected"
        ? (listingsResult.reason?.message || "Unable to load the listings catalog.")
        : null,
    );

    if (includeLeadInsights && l?.length) {
      const listingId = l[0].id;
      const [rec, pred, opt] = await Promise.all([
        fetchPricingRecommendation(listingId),
        fetchPrediction(listingId),
        optimizeListing(listingId),
      ]);
      setRecommendation(rec);
      setPrediction(pred);
      setOptimization(opt);
    }
  }, [
    includeAlerts,
    includeAnalytics,
    includeAutonomousConfig,
    includeClusters,
    includeLeadInsights,
    includeListingTemplates,
    includeListings,
    includeMarketplaces,
    includeOfferDashboard,
    includePlatformConfig,
    includeStorageBatches,
    listingPage,
    listingPageSize,
    listingSearch,
    listingQueue,
    listingSortBy,
    listingSortDir,
    listingSourceType,
    listingSummaryOnly,
    listingPageCacheKey,
    paginateListings,
    staticCacheKey,
    userId,
  ]);

  useEffect(() => {
    // Coalesce rapid filter toggles into one catalog request. The Listings
    // page also exposes an explicit Reload Catalog action for operators who
    // want to fetch only after finishing a multi-select change.
    const timer = window.setTimeout(() => reload(), 450);
    return () => window.clearTimeout(timer);
  }, [reload]);

  const readyCount = useMemo(
    () => listings.filter((l) => l.status === "ready").length,
    [listings],
  );
  const recentAutoPublished = useMemo(
    () =>
      listings
        .filter(
          (listing) =>
            listing.marketplace_data?.autonomous?.trigger === "auto" &&
            !listing.marketplace_data?.autonomous?.dry_run &&
            (listing.ebay_publish_status === "POSTED" ||
              listing.ebay_listing_id),
        )
        .sort((a, b) => b.id - a.id)
        .slice(0, 8),
    [listings],
  );

  return {
    clusters,
    listings,
    marketplaces,
    analytics,
    alerts,
    recommendation,
    prediction,
    optimization,
    autonomousConfig,
    offerDashboard,
    enabledPlatforms,
    storageBatches,
    listingTemplates,
    listingError,
    listingPagination,
    listingBucketCounts,
    readyCount,
    recentAutoPublished,
    setEnabledPlatforms,
    reload,
  };
}
