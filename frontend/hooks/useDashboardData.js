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

  const reload = useCallback(async () => {
    if (!userId) return;
    const settled = await Promise.allSettled([
      fetchClusters(),
      fetchListings(),
      fetchMarketplaces(),
      fetchAnalyticsOverview(userId),
      fetchAlerts(userId),
      fetchAutonomousConfig(),
      fetchEbayOfferDashboard(userId),
      fetchPlatformConfig(userId),
      fetchStorageUnitBatches(),
      fetchListingTemplates(userId),
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
    const l = listingsResult.status === "fulfilled" ? listingsResult.value : [];
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
    setMarketplaces(m?.marketplaces || []);
    setAnalytics(a);
    setAlerts(al?.alerts || []);
    setAutonomousConfig(autoConfig);
    setOfferDashboard(offerData);
    setEnabledPlatforms(platformConfig?.enabled_platforms || ["ebay"]);
    setStorageBatches(batches || []);
    setListingTemplates(templates || []);

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
  }, [includeLeadInsights, userId]);

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
    readyCount,
    recentAutoPublished,
    setEnabledPlatforms,
    reload,
  };
}
