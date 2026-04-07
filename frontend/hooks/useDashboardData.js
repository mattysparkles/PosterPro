import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  fetchAlerts,
  fetchAnalyticsOverview,
  fetchAutonomousConfig,
  fetchClusters,
  fetchEbayOfferDashboard,
  fetchListings,
  fetchMarketplaces,
  fetchPlatformConfig,
  fetchPrediction,
  fetchPricingRecommendation,
  fetchStorageUnitBatches,
  optimizeListing,
} from '../lib/api';

export default function useDashboardData() {
  const [clusters, setClusters] = useState([]);
  const [listings, setListings] = useState([]);
  const [marketplaces, setMarketplaces] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [recommendation, setRecommendation] = useState(null);
  const [prediction, setPrediction] = useState(null);
  const [optimization, setOptimization] = useState(null);
  const [autonomousConfig, setAutonomousConfig] = useState({ autonomous_mode: true, autonomous_dry_run: false });
  const [offerDashboard, setOfferDashboard] = useState({ active_offers: [], decision_log: [] });
  const [enabledPlatforms, setEnabledPlatforms] = useState(['ebay']);
  const [storageBatches, setStorageBatches] = useState([]);

  const reload = useCallback(async () => {
    const [c, l, m, a, al, autoConfig, offerData, platformConfig, batches] = await Promise.all([
      fetchClusters(),
      fetchListings(),
      fetchMarketplaces(),
      fetchAnalyticsOverview(),
      fetchAlerts(),
      fetchAutonomousConfig(),
      fetchEbayOfferDashboard().catch(() => ({ active_offers: [], decision_log: [] })),
      fetchPlatformConfig(1).catch(() => ({ enabled_platforms: ['ebay'] })),
      fetchStorageUnitBatches().catch(() => []),
    ]);
    setClusters(c);
    setListings(l);
    setMarketplaces(m.marketplaces || []);
    setAnalytics(a);
    setAlerts(al.alerts || []);
    setAutonomousConfig(autoConfig);
    setOfferDashboard(offerData);
    setEnabledPlatforms(platformConfig.enabled_platforms || ['ebay']);
    setStorageBatches(batches || []);

    if (l?.length) {
      const listingId = l[0].id;
      const [rec, pred, opt] = await Promise.all([fetchPricingRecommendation(listingId), fetchPrediction(listingId), optimizeListing(listingId)]);
      setRecommendation(rec);
      setPrediction(pred);
      setOptimization(opt);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const readyCount = useMemo(() => listings.filter((l) => l.status === 'ready').length, [listings]);
  const recentAutoPublished = useMemo(
    () =>
      listings
        .filter(
          (listing) =>
            listing.marketplace_data?.autonomous?.trigger === 'auto' &&
            !listing.marketplace_data?.autonomous?.dry_run &&
            (listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id)
        )
        .sort((a, b) => b.id - a.id)
        .slice(0, 8),
    [listings]
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
    readyCount,
    recentAutoPublished,
    setEnabledPlatforms,
    reload,
  };
}
