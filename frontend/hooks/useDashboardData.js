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
  const [clusters, setClusters] = useState([]);
  const [listings, setListings] = useState([]);
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
  const [listingPagination, setListingPagination] = useState({ page: listingPage, page_size: listingPageSize, total: 0, total_pages: 1 });
  const [listingBucketCounts, setListingBucketCounts] = useState({});
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

    setListings(l || []);
    setListingPagination(
      Array.isArray(listingResult)
        ? { page: 1, page_size: l?.length || listingPageSize, total: l?.length || 0, total_pages: 1 }
        : {
          page: Number(listingResult?.page || listingPage),
          page_size: Number(listingResult?.page_size || listingPageSize),
          total: Number(listingResult?.total || 0),
          total_pages: Number(listingResult?.total_pages || 1),
          bucket_counts: listingResult?.bucket_counts || {},
        },
    );
    setListingBucketCounts(Array.isArray(listingResult) ? {} : (listingResult?.bucket_counts || {}));

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
      setAutonomousConfig(autoConfigValue);
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
    paginateListings,
    staticCacheKey,
    userId,
  ]);

  useEffect(() => {
    reload();
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
