import { useCallback, useEffect, useMemo, useState } from "react";

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
  const listingSearch = String(options.listingSearch || '');
  const listingQueue = String(options.listingQueue || '');
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

  const reload = useCallback(async () => {
    if (!userId) return;
    const settled = await Promise.allSettled([
      includeClusters ? fetchClusters() : Promise.resolve([]),
      includeListings ? (paginateListings ? fetchListings({ page: listingPage, pageSize: listingPageSize, sourceType: listingSourceType, search: listingSearch, queue: listingQueue }) : fetchListings()) : Promise.resolve([]),
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
    ]);

    const [
      clustersResult,
      listingsResult,
      marketplacesResult,
      analyticsResult,
      alertsResult,
      autoConfigResult,
      offerDataResult,
      platformConfigResult,
      batchesResult,
      templatesResult,
    ] = settled;

    const c = clustersResult.status === "fulfilled" ? clustersResult.value : [];
    const listingResult = listingsResult.status === "fulfilled" ? listingsResult.value : [];
    const l = Array.isArray(listingResult) ? listingResult : (listingResult?.items || []);
    const m = marketplacesResult.status === "fulfilled" ? marketplacesResult.value : { marketplaces: [] };
    const a = analyticsResult.status === "fulfilled" ? analyticsResult.value : null;
    const al = alertsResult.status === "fulfilled" ? alertsResult.value : { alerts: [] };
    const autoConfig = autoConfigResult.status === "fulfilled" ? autoConfigResult.value : {
      autonomous_mode: true,
      autonomous_dry_run: false,
    };
    const offerData = offerDataResult.status === "fulfilled" ? offerDataResult.value : {
      active_offers: [],
      decision_log: [],
    };
    const platformConfig = platformConfigResult.status === "fulfilled" ? platformConfigResult.value : { enabled_platforms: ["ebay"] };
    const batches = batchesResult.status === "fulfilled" ? batchesResult.value : [];
    const templates = templatesResult.status === "fulfilled" ? templatesResult.value : [];

    setClusters(c || []);
    setListings(l || []);
    setListingPagination(
      Array.isArray(listingResult)
        ? { page: 1, page_size: l?.length || listingPageSize, total: l?.length || 0, total_pages: 1 }
        : {
          page: Number(listingResult?.page || listingPage),
          page_size: Number(listingResult?.page_size || listingPageSize),
          total: Number(listingResult?.total || 0),
          total_pages: Number(listingResult?.total_pages || 1),
        },
    );
    setMarketplaces(m?.marketplaces || []);
    setAnalytics(a);
    setAlerts(al?.alerts || []);
    setAutonomousConfig(autoConfig);
    setOfferDashboard(offerData);
    setEnabledPlatforms(platformConfig?.enabled_platforms || ["ebay"]);
    setStorageBatches(batches || []);
    setListingTemplates(templates || []);
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
    listingSourceType,
    paginateListings,
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
    readyCount,
    recentAutoPublished,
    setEnabledPlatforms,
    reload,
  };
}
