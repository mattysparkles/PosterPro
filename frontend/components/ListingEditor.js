import { useMemo, useState } from "react";
import { useRouter } from "next/router";
import { Camera, Sparkles, Trash2, WandSparkles } from "lucide-react";

import MarketplaceStatusPanel from "./MarketplaceStatusPanel";
import StatusPill from "./StatusPill";
import Button from "./ui/button";
import PhotoEditorModal from "./PhotoEditorModal";
import Input from "./ui/input";
import { toPublicImageUrl } from "../lib/api";

const PLATFORM_OPTIONS = [
  "ebay",
  "facebook",
  "etsy",
  "poshmark",
  "mercari",
  "depop",
  "whatnot",
];

function marketplacePreviewTitle(market) {
  if (market === "facebook") return "Facebook Marketplace";
  if (market === "ebay") return "eBay";
  return market;
}

function deriveListingImages(listing) {
  const seen = new Set();
  const rawImages = Array.isArray(listing?.listing_images) ? listing.listing_images : [];
  const fallbackImages = Array.isArray(listing?.image_urls) ? listing.image_urls : [];
  const normalized = [];
  rawImages.forEach((image, index) => {
    if (!image || !image.storage_path) return;
    const key = `${image.storage_path}|${image.source_url || ""}`;
    if (seen.has(key)) return;
    seen.add(key);
    normalized.push({
      ...image,
      display_order: Number.isFinite(Number(image.display_order)) ? Number(image.display_order) : index,
      role: image.role || (index === 0 ? "primary" : "alternate_angle"),
      operator_state: image.operator_state || "suggested",
      source_platform: image.source_platform || listing?.source_type || "upload",
    });
  });
  fallbackImages.forEach((path, index) => {
    const key = `${path}|`;
    if (!path || seen.has(key)) return;
    seen.add(key);
    normalized.push({
      storage_path: path,
      display_order: normalized.length + index,
      role: normalized.length ? "alternate_angle" : "primary",
      operator_state: "approved",
      source_platform: listing?.source_type || "upload",
      confidence: 1,
      is_reference: false,
    });
  });
  return normalized.sort((a, b) => (a.display_order || 0) - (b.display_order || 0));
}

function sourceTone(sourcePlatform) {
  switch (String(sourcePlatform || "").toLowerCase()) {
    case "amazon":
    case "amazon_vine":
      return "bg-[#fff7ed] text-[#b54708]";
    case "google_photos":
      return "bg-[#eef4ff] text-[#2563eb]";
    case "upload":
    case "storage_batch":
      return "bg-[#ecfdf3] text-[#027a48]";
    default:
      return "bg-[#f2f4f7] text-[#475467]";
  }
}

function provenanceTone(source) {
  switch (String(source || "").toLowerCase()) {
    case "approximate":
    case "default":
      return "bg-[#fff7ed] text-[#b54708]";
    case "derived":
      return "bg-[#eff6ff] text-[#175cd3]";
    case "existing":
      return "bg-[#ecfdf3] text-[#027a48]";
    default:
      return "bg-[#f2f4f7] text-[#475467]";
  }
}

function provenanceLabel(source) {
  switch (String(source || "").toLowerCase()) {
    case "approximate":
      return "Approximate";
    case "default":
      return "Fallback";
    case "derived":
      return "Derived";
    case "existing":
      return "Exact";
    default:
      return "Unknown";
  }
}

function MarketplacePreviewFrame({ market, listing, previewEntry, previewImages, statusMap, crosspostPreviewLoading }) {
  const title = listing.title || "Draft title pending";
  const price = listing.suggested_price || listing.listing_price || previewEntry?.price || 0;
  const priceLabel = Number.isFinite(Number(price)) ? Number(price).toFixed(0) : String(price || "0");
  const imageCount = previewImages.length;
  const marketplaceName = marketplacePreviewTitle(market);
  const notes = (previewEntry?.notes || []).slice(0, 4);
  const imageColumns = previewImages.slice(1, 5);
  const condition = listing.condition || previewEntry?.condition || "Condition pending";
  const category = listing.category_suggestion || listing.category_id || previewEntry?.category_hint || "Category pending";
  const shipping = previewEntry?.shipping_policy || previewEntry?.shipping || {};

  return (
    <div className="overflow-hidden rounded-[20px] border border-[#d0d5dd] bg-white shadow-[0_18px_50px_rgba(16,24,40,0.08)]">
      <div className={`border-b px-4 py-3 ${market === "ebay" ? "bg-[#f5f9ff]" : "bg-[#f0f9ff]"}`}>
        <div className="flex items-center gap-3">
          <div className={`h-3 w-3 rounded-full ${market === "ebay" ? "bg-[#2563eb]" : "bg-[#1877f2]"}`} />
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#667085]">{marketplaceName} preview</p>
            <p className="truncate text-sm font-semibold text-[#101828]">{title}</p>
          </div>
          <span className="ml-auto rounded-full border border-[#d0d5dd] bg-white px-2.5 py-1 text-[11px] font-semibold text-[#475467]">
            {crosspostPreviewLoading ? "Loading…" : statusMap[market] || "Draft"}
          </span>
        </div>
      </div>

      <div className={`px-4 py-4 ${market === "ebay" ? "bg-[#f8fbff]" : "bg-[#f7fbff]"}`}>
        <div className="rounded-[16px] border border-[#d0d5dd] bg-white shadow-sm">
          <div className={`flex items-center gap-2 border-b px-3 py-2 text-[11px] uppercase tracking-[0.14em] ${market === "ebay" ? "bg-[#f8fbff] text-[#2563eb]" : "bg-[#eef6ff] text-[#1877f2]"}`}>
            <span className={`h-2.5 w-2.5 rounded-full ${market === "ebay" ? "bg-[#2563eb]" : "bg-[#1877f2]"}`} />
            {marketplaceName} listing page
          </div>
          <div className="grid gap-0 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
            <div className="border-b lg:border-b-0 lg:border-r">
              <div className="bg-[#0b1f3a] px-4 py-3 text-white">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold">{market === "ebay" ? "eBay" : "Marketplace"}</p>
                  <div className="rounded-full bg-white/10 px-2 py-1 text-[11px] font-medium">
                    {market === "ebay" ? "Buy It Now" : "Marketplace listing"}
                  </div>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <div className="h-2.5 flex-1 rounded-full bg-white/10" />
                  <div className="h-2.5 w-16 rounded-full bg-white/20" />
                </div>
              </div>
              <div className="p-4">
                <div className="overflow-hidden rounded-[16px] border border-[#e5e7eb] bg-[#f9fafb]">
                  {previewImages.length ? (
                    <img
                      src={toPublicImageUrl(previewImages[0])}
                      alt={title}
                      className="h-72 w-full object-cover"
                    />
                  ) : (
                    <div className="flex h-72 items-center justify-center text-sm text-[#667085]">No listing image yet</div>
                  )}
                </div>
                {imageColumns.length ? (
                  <div className="mt-3 grid grid-cols-4 gap-2">
                    {imageColumns.map((imageUrl, index) => (
                      <div key={`${market}-${imageUrl}-${index}`} className="overflow-hidden rounded-[10px] border border-[#e5e7eb] bg-[#f9fafb]">
                        <img src={toPublicImageUrl(imageUrl)} alt={`${title} image ${index + 2}`} className="h-16 w-full object-cover" />
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>

            <div className="space-y-4 p-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">{market === "ebay" ? "Item details" : "Marketplace summary"}</p>
                <h3 className="mt-1 text-xl font-semibold text-[#101828]">{title}</h3>
                <p className="mt-1 text-sm text-[#667085]">Listing #{listing.id} · {imageCount} image{imageCount === 1 ? '' : 's'}</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <span className="pp-chip">${priceLabel}</span>
                  <span className="pp-chip">{condition}</span>
                  <span className="pp-chip">{category}</span>
                  {listing.quantity ? <span className="pp-chip">Qty {listing.quantity}</span> : null}
                </div>
              </div>

              <div className={`rounded-[16px] border px-4 py-3 ${market === "ebay" ? "border-[#dbeafe] bg-[#eff6ff]" : "border-[#dbeafe] bg-[#eff6ff]"}`}>
                <p className="text-sm font-semibold text-[#101828]">{market === "ebay" ? "About this item" : "Description"}</p>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-[#475467]">
                  {listing.description || previewEntry?.description || "Description not generated yet."}
                </p>
              </div>

              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-[14px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">Status</p>
                  <p className="mt-2 text-sm font-semibold text-[#101828]">
                    {crosspostPreviewLoading ? "Loading preview…" : statusMap[market] || "Draft"}
                  </p>
                </div>
                <div className="rounded-[14px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">Shipping / delivery</p>
                  <p className="mt-2 text-sm text-[#101828]">
                    {market === "facebook"
                      ? previewEntry?.delivery_method || "manual"
                      : shipping?.service || shipping?.domestic_service || "Standard shipping"}
                  </p>
                </div>
              </div>

              <div className="rounded-[14px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">
                  {market === "ebay" ? "Item specifics" : "Pickup / delivery"}
                </p>
                {market === "ebay" ? (
                  <div className="mt-3 grid gap-2">
                    {Object.entries(listing.item_specifics || {}).slice(0, 4).length ? (
                      Object.entries(listing.item_specifics || {}).slice(0, 4).map(([key, value]) => (
                        <div key={`${market}-${key}`} className="flex items-center justify-between gap-3 rounded-[10px] border border-[#eaecf0] bg-white px-3 py-2">
                          <span className="text-sm text-[#667085]">{key}</span>
                          <span className="text-sm font-semibold text-[#101828]">{String(value)}</span>
                        </div>
                      ))
                    ) : (
                      <p className="text-sm text-[#667085]">Item specifics will appear here once the draft is enriched.</p>
                    )}
                  </div>
                ) : (
                  <p className="mt-2 text-sm text-[#475467]">
                    {previewEntry?.meetup_notes || shipping?.facebook_meetup_notes || "Pickup and delivery details will appear here for Facebook Marketplace."}
                  </p>
                )}
              </div>

              {notes.length ? (
                <div className="rounded-[14px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">Preview notes</p>
                  <div className="mt-2 space-y-2">
                    {notes.map((note) => (
                      <p key={`${market}-${note}`} className="text-sm text-[#475467]">{note}</p>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ListingEditor({
  listing,
  pricingRecommendation,
  listingIntelligence,
  marketplacePreflights = {},
  marketplacePayloadPreviews = {},
  workflowPreferences,
  templates = [],
  onApplyTemplate,
  onSaveTemplate,
  onSave,
  onGenerate,
  onApprove,
  onApproveAndNext,
  onPublish,
  onDelete,
  onPhotoUpdated,
  onRefreshPricing,
  onApplyPricing,
  onRefreshMarketplacePreflight,
  onSyncEbayListing,
  onUploadPhotos,
  onApprovePhotos,
  onRejectPhotos,
  onSetPrimaryPhoto,
  publishState,
  statuses,
  crosspostPreview = [],
  crosspostPreviewLoading = false,
}) {
  const [openEditor, setOpenEditor] = useState(false);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [selectedPlatforms, setSelectedPlatforms] = useState(["ebay"]);
  const [activePreviewMarket, setActivePreviewMarket] = useState("ebay");
  const [showExcludedComps, setShowExcludedComps] = useState(false);
  const [manualComp, setManualComp] = useState({ title: "", price: "", source_marketplace: "manual", condition: "" });
  const [newItemSpecific, setNewItemSpecific] = useState({ key: "", value: "" });
  const router = useRouter();
  const requiresApproval = workflowPreferences?.review_before_publish ?? true;
  const intelligence = listingIntelligence?.intelligence || {};
  const draftMeta = listingIntelligence?.draft_meta || {};
  const pricingAnalysis = listingIntelligence?.pricing_analysis || pricingRecommendation || {};
  const readiness = listingIntelligence?.readiness || {};
  const listingImages = useMemo(() => deriveListingImages(listing), [listing]);
  const approvedActualImages = useMemo(() => listingImages.filter((image) => !image.is_reference && image.operator_state === "approved"), [listingImages]);
  const pendingActualImages = useMemo(() => listingImages.filter((image) => !image.is_reference && image.operator_state !== "approved" && image.operator_state !== "rejected"), [listingImages]);
  const referenceImages = useMemo(() => listingImages.filter((image) => image.is_reference && image.operator_state !== "rejected"), [listingImages]);
  const rejectedImages = useMemo(() => listingImages.filter((image) => image.operator_state === "rejected"), [listingImages]);
  const readinessSummary = readiness.review_summary || listing.readiness_summary || {};
  const conditionData = listing.condition_data || {};
  const shippingProfile = listing.shipping_profile || {};
  const qualitySummary = readiness.quality_summary || listing.quality_summary || {};
  const latestPublishAttempt = listing.latest_publish_attempt || null;
  const preflightMap = marketplacePreflights || {};
  const payloadPreviewMap = marketplacePayloadPreviews || {};
  const ebaySpecificsProvenance = useMemo(
    () => ({
      ...((latestPublishAttempt?.payload_snapshot && latestPublishAttempt.payload_snapshot.item_specifics_provenance) || {}),
      ...((listing?.marketplace_data && listing.marketplace_data.ebay_item_specifics_provenance) || {}),
      ...((payloadPreviewMap?.ebay && payloadPreviewMap.ebay.item_specifics_provenance) || {}),
    }),
    [latestPublishAttempt?.payload_snapshot, listing?.marketplace_data, payloadPreviewMap],
  );
  const approximateSpecificFields = useMemo(
    () => new Set([
      ...((latestPublishAttempt?.payload_snapshot && latestPublishAttempt.payload_snapshot.item_specifics_approximate) || []),
      ...((listing?.marketplace_data && listing.marketplace_data.ebay_item_specifics_approximate) || []),
      ...((payloadPreviewMap?.ebay && payloadPreviewMap.ebay.item_specifics_approximate) || []),
    ].filter(Boolean)),
    [latestPublishAttempt?.payload_snapshot, listing?.marketplace_data, payloadPreviewMap],
  );

  const statusMap = useMemo(() => {
    const map = {};
    (statuses || []).forEach((row) => {
      map[row.marketplace] = row.status;
    });
    return map;
  }, [statuses]);
  const previewMap = useMemo(() => {
    const map = {};
    (crosspostPreview || []).forEach((entry) => {
      if (!entry?.marketplace) return;
      map[entry.marketplace] = entry;
    });
    return map;
  }, [crosspostPreview]);
  const previewImages = useMemo(() => {
    return listingImages
      .filter((image) => image.operator_state !== "rejected")
      .map((image) => image.storage_path)
      .filter(Boolean);
  }, [listingImages]);
  const activePreviewEntry = previewMap[activePreviewMarket] || null;
  const visibleComps = showExcludedComps
    ? [...(pricingAnalysis?.included_comps || []), ...(pricingAnalysis?.excluded_comps || [])]
    : (pricingAnalysis?.included_comps || pricingAnalysis?.comparables || []);

  const uploadActualPhotos = async (files) => {
    const validFiles = Array.from(files || []);
    if (!validFiles.length || !onUploadPhotos) return;
    await onUploadPhotos(listing.id, validFiles);
  };

  const approvePhoto = async (storagePath) => {
    if (onApprovePhotos) {
      await onApprovePhotos(listing.id, [storagePath]);
      return;
    }
    await persistListingImages(listingImages.map((entry) => String(entry.storage_path) === String(storagePath) ? { ...entry, operator_state: "approved", operator_approved: true, operator_rejected: false, is_reference: false } : entry));
  };

  const rejectPhoto = async (storagePath) => {
    if (onRejectPhotos) {
      await onRejectPhotos(listing.id, [storagePath]);
      return;
    }
    await persistListingImages(listingImages.map((entry) => String(entry.storage_path) === String(storagePath) ? { ...entry, operator_state: "rejected", operator_approved: false, operator_rejected: true } : entry));
  };

  const setPrimaryPhoto = async (storagePath, index) => {
    if (onSetPrimaryPhoto) {
      await onSetPrimaryPhoto(listing.id, storagePath);
      return;
    }
    await persistListingImages([listingImages[index], ...listingImages.filter((_, entryIndex) => entryIndex !== index)]);
  };

  const renderImageCard = (image, index, groupLabel) => (
    <div key={`${image.storage_path}-${index}-${groupLabel}`} className="rounded-[14px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
      <div className="overflow-hidden rounded-[12px] border border-[#e5e7eb] bg-white">
        <img src={toPublicImageUrl(image.storage_path)} alt={`${listing.title || "Listing"} image ${index + 1}`} className="h-48 w-full object-cover" />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${sourceTone(image.source_platform)}`}>{image.source_platform || "upload"}</span>
        <span className="rounded-full bg-[#f2f4f7] px-2.5 py-1 text-[11px] font-semibold text-[#475467]">{image.role || "alternate_angle"}</span>
        <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${image.is_reference ? "bg-[#fff7ed] text-[#b54708]" : "bg-[#ecfdf3] text-[#027a48]"}`}>
          {image.is_reference ? "Reference image, not publishable" : image.operator_state === "approved" ? "Actual item photo, approved" : image.operator_state === "rejected" ? "Rejected" : "Actual item photo, pending review"}
        </span>
      </div>
      <p className="mt-2 text-xs text-[#667085]">
        Source: {image.source_platform || "upload"} · State: {image.operator_state || "suggested"} · Reference: {image.is_reference ? "yes" : "no"}
      </p>
      {image.warning ? <p className="mt-2 text-xs text-[#b54708]">{image.warning}</p> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {image.operator_state !== "approved" ? <Button size="sm" variant="outline" onClick={() => approvePhoto(image.storage_path)}>Approve</Button> : null}
        {image.operator_state !== "rejected" ? <Button size="sm" variant="outline" onClick={() => rejectPhoto(image.storage_path)}>Reject</Button> : null}
        <Button size="sm" variant="outline" onClick={() => setPrimaryPhoto(image.storage_path, listingImages.findIndex((entry) => entry.storage_path === image.storage_path))}>Set primary</Button>
      </div>
    </div>
  );

  const persistListingImages = async (nextImages) => {
    const normalized = nextImages.map((image, index) => ({
      ...image,
      display_order: index,
      role: index === 0 ? "primary" : (image.role === "primary" ? "alternate_angle" : (image.role || "alternate_angle")),
    }));
    await onSave(listing.id, {
      listing_images: normalized,
      image_urls: normalized.filter((image) => image.operator_state !== "rejected").map((image) => image.storage_path).filter(Boolean),
    });
  };

  const updateConditionField = async (field, value) => {
    await onSave(listing.id, {
      condition_data: {
        ...conditionData,
        [field]: value,
      },
    });
  };

  const updateShippingField = async (field, value) => {
    await onSave(listing.id, {
      shipping_profile: {
        ...shippingProfile,
        [field]: value,
      },
    });
  };

  const updateItemSpecificField = async (field, value) => {
    const nextSpecifics = { ...(listing.item_specifics || {}) };
    if (value === null || value === undefined || String(value).trim() === "") {
      delete nextSpecifics[field];
    } else {
      nextSpecifics[field] = value;
    }
    await onSave(listing.id, {
      item_specifics: nextSpecifics,
    });
  };

  const addItemSpecificField = async () => {
    const key = String(newItemSpecific.key || "").trim();
    const value = String(newItemSpecific.value || "").trim();
    if (!key || !value) return;
    await updateItemSpecificField(key, value);
    setNewItemSpecific({ key: "", value: "" });
  };

  const addManualComp = async () => {
    if (!manualComp.title || !manualComp.price) return;
    const existingManual = Array.isArray(listing?.marketplace_data?.pricing_manual_comps)
      ? listing.marketplace_data.pricing_manual_comps
      : [];
    await onSave(listing.id, {
      marketplace_data: {
        ...(listing.marketplace_data || {}),
        pricing_manual_comps: [
          ...existingManual,
          {
            ...manualComp,
            price: Number(manualComp.price),
            comp_type: "manual",
          },
        ],
      },
    });
    setManualComp({ title: "", price: "", source_marketplace: "manual", condition: "" });
    if (onRefreshPricing) {
      await onRefreshPricing(listing.id);
    }
  };

  const syncListingWithEbay = async () => {
    if (!onSyncEbayListing || !listing.ebay_listing_id) return;
    await onSyncEbayListing(listing.id);
  };

  const togglePlatform = (platform) => {
    setSelectedPlatforms((prev) =>
      prev.includes(platform)
        ? prev.filter((p) => p !== platform)
        : [...prev, platform],
    );
  };

  const scrollToSection = (sectionId) => {
    if (typeof document === "undefined") return;
    const node = document.getElementById(sectionId);
    if (node) {
      node.scrollIntoView({ behavior: "smooth", block: "start" });
      if (typeof node.focus === "function") {
        node.focus({ preventScroll: true });
      }
    }
  };

  const handlePreflightFix = async (issue, market) => {
    const code = String(issue?.code || "").toUpperCase();
    if (code.includes("TITLE")) {
      scrollToSection("listing-title-field");
      return;
    }
    if (code.includes("PRICE") || code.includes("PRICING")) {
      scrollToSection("listing-pricing-section");
      if (code.includes("WEAK")) {
        await onRefreshPricing?.(listing.id);
      }
      return;
    }
    if (code.includes("PHOTO") || code.includes("IMAGE")) {
      scrollToSection("listing-image-section");
      return;
    }
    if (code.includes("CONDITION")) {
      scrollToSection("listing-condition-section");
      return;
    }
    if (code.includes("SHIPPING") || code.includes("WEIGHT") || code.includes("DIMENSION")) {
      scrollToSection("listing-shipping-section");
      return;
    }
    if (code.includes("CATEGORY")) {
      scrollToSection("listing-category-section");
      return;
    }
    if (code.includes("ASPECT") || code.includes("SPECIFIC")) {
      scrollToSection("listing-item-specifics-section");
      return;
    }
    if (code.includes("POLICY")) {
      await router.push("/settings?tab=ebay");
    }
    if (market === "facebook" && code.includes("BRIDGE")) {
      await router.push("/settings?tab=marketplaces");
    }
  };

  const renderPreflightIssue = (issue, market, type = "blocker") => {
    const tone = type === "warning" ? "border-[#fef0c7] bg-[#fffaeb] text-[#8a4b10]" : "border-[#fecdca] bg-[#fff6ed] text-[#7a2e0b]";
    return (
      <div key={`${market}-${type}-${issue.code}`} className={`rounded-[10px] border p-3 text-sm ${tone}`}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-semibold">{issue.message}</p>
            {issue.fix_hint ? <p className="mt-1 text-xs opacity-90">{issue.fix_hint}</p> : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {issue.retryable ? <span className="rounded-full border border-current px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em]">Retryable</span> : null}
            {typeof handlePreflightFix === "function" ? (
              <Button size="sm" variant="outline" onClick={() => handlePreflightFix(issue, market)}>
                Fix
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    );
  };

  const renderPublishAttemptStatus = (attempt) => {
    const status = String(attempt?.marketplace_status || '').toLowerCase();
    if (['posted', 'published', 'completed'].includes(status)) return 'success';
    if (['queued', 'running', 'posting'].includes(status)) return 'info';
    if (status === 'failed') return 'danger';
    if (attempt?.dry_run || status.startsWith('dry_run')) return 'warning';
    if (status === 'skipped') return 'default';
    return 'default';
  };

  return (
    <div className="h-full" data-tour="view-inventory">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-[#101828]">Listing #{listing.id}</h3>
        <StatusPill status={listing.ebay_publish_status || listing.status} />
      </div>
      <p className="mb-4 text-sm text-[#667085]">
        Tighten the draft, set pricing, and publish when it is ready.
      </p>

      <div className="mb-4 grid gap-3 md:grid-cols-2">
        <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
          <p className="text-sm font-semibold text-[#101828]">Review gate</p>
          <p className="mt-2 text-sm text-[#667085]">
            {requiresApproval
              ? 'This workspace requires an approval step before publish.'
              : 'This workspace allows direct publish from draft or ready states.'}
          </p>
        </div>
        <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
          <p className="text-sm font-semibold text-[#101828]">Current preview mode</p>
          <p className="mt-2 text-sm text-[#667085]">
            {workflowPreferences?.listing_preview_mode === 'marketplace' ? 'Marketplace-style listing preview' : 'Editor-first draft layout'}
          </p>
        </div>
        <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
          <p className="text-sm font-semibold text-[#101828]">Listing quality</p>
          <p className="mt-2 text-sm text-[#667085]">
            {qualitySummary.score != null ? `${qualitySummary.score}/100 · ${String(qualitySummary.status || 'needs_review').replaceAll('_', ' ')}` : 'Quality scoring pending'}
          </p>
        </div>
      </div>

      {latestPublishAttempt ? (
        <div id="listing-publish-attempt" className="mb-4 rounded-[14px] border border-[#e5e7eb] bg-white p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-[#101828]">Latest publish attempt</p>
              <p className="mt-1 text-sm text-[#667085]">
                Shows the most recent queued, dry-run, or live attempt so operators can see what happened without opening raw job JSON.
              </p>
            </div>
            <StatusPill
              status={renderPublishAttemptStatus(latestPublishAttempt)}
              label={String(latestPublishAttempt.marketplace_status || (latestPublishAttempt.dry_run ? 'dry_run' : 'unknown')).replaceAll('_', ' ')}
            />
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Marketplace</p>
              <p className="mt-2 text-sm font-semibold text-[#101828]">{latestPublishAttempt.marketplace || 'unknown'}</p>
            </div>
            <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Marketplace listing id</p>
              <p className="mt-2 text-sm font-semibold text-[#101828]">{latestPublishAttempt.marketplace_listing_id || 'pending'}</p>
            </div>
            <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Payload hash</p>
              <p className="mt-2 break-all text-xs text-[#101828]">{latestPublishAttempt.payload_hash || 'not recorded'}</p>
            </div>
            <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Retry count</p>
              <p className="mt-2 text-sm font-semibold text-[#101828]">{latestPublishAttempt.retry_count || 0}</p>
            </div>
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Translated error</p>
              <p className="mt-2 text-sm text-[#101828]">
                {latestPublishAttempt.translated_error?.user_message || latestPublishAttempt.translated_error?.fix_hint || latestPublishAttempt.raw_error || 'No error recorded.'}
              </p>
              {latestPublishAttempt.translated_error?.fix_hint ? (
                <p className="mt-2 text-sm text-[#667085]">{latestPublishAttempt.translated_error.fix_hint}</p>
              ) : null}
            </div>
            <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Timing</p>
              <p className="mt-2 text-sm text-[#101828]">
                {latestPublishAttempt.started_at ? `Started ${new Date(latestPublishAttempt.started_at).toLocaleString()}` : 'Start time unavailable'}
              </p>
              <p className="mt-1 text-sm text-[#101828]">
                {latestPublishAttempt.finished_at ? `Finished ${new Date(latestPublishAttempt.finished_at).toLocaleString()}` : 'Still running or not finished'}
              </p>
            </div>
          </div>
          {latestPublishAttempt.retryable ? (
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => onPublish(listing.id, [latestPublishAttempt.marketplace])}>
                Retry latest attempt
              </Button>
            </div>
          ) : null}
          <details className="mt-3 rounded-[10px] border border-[#eaecf0] bg-white p-3">
            <summary className="cursor-pointer text-sm font-semibold text-[#101828]">Raw diagnostics</summary>
            <div className="mt-3 space-y-2 text-xs text-[#667085]">
              <p>Attempt ID: {latestPublishAttempt.id}</p>
              <p>Preflight status: {latestPublishAttempt.preflight_status || 'unknown'}</p>
              <p>Inventory SKU: {latestPublishAttempt.inventory_item_sku || 'unknown'}</p>
              <p>Offer ID: {latestPublishAttempt.offer_id || 'unknown'}</p>
              <pre className="overflow-auto rounded-[10px] bg-[#0b1120] p-3 text-[11px] text-[#cbd5e1]">
                {JSON.stringify(latestPublishAttempt, null, 2)}
              </pre>
            </div>
          </details>
        </div>
      ) : null}

      <div id="listing-image-section" className="mb-4 rounded-[14px] border border-[#e5e7eb] bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-[#101828]">Marketplace readiness</p>
            <p className="mt-1 text-sm text-[#667085]">Inspect exactly what each marketplace still needs before eBay publish or Facebook assisted handoff.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => onRefreshMarketplacePreflight?.(listing.id)}>Refresh preflight</Button>
            {listing.ebay_listing_id ? (
              <Button size="sm" variant="outline" onClick={syncListingWithEbay}>
                Push updates to eBay
              </Button>
            ) : null}
            <Button size="sm" variant="ghost" onClick={() => onRefreshPricing?.(listing.id)}>Refresh pricing</Button>
          </div>
        </div>
        <div className="mt-4 grid gap-3 xl:grid-cols-2">
          {['ebay', 'facebook'].map((market) => {
            const preflight = preflightMap[market] || null;
            const payloadPreview = payloadPreviewMap[market] || null;
            const blockers = preflight?.blockers || [];
            const warnings = preflight?.warnings || [];
            const category = preflight?.category_summary || {};
            const policy = preflight?.policy_summary || {};
            return (
              <div key={`preflight-${market}`} className="rounded-[14px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold capitalize text-[#101828]">{market}</p>
                    <p className="mt-1 text-xs text-[#667085]">
                      {preflight?.status || 'preflight pending'}
                      {category?.category_name ? ` · ${category.category_name}` : ''}
                    </p>
                  </div>
                  <StatusPill status={preflight?.status === 'blocked' ? 'danger' : preflight?.status === 'ready_with_warnings' ? 'warning' : preflight?.status === 'published' ? 'success' : 'info'} label={preflight?.status || 'pending'} />
                </div>
                <div className="mt-3 grid gap-2 md:grid-cols-2">
                  <div className="rounded-[10px] border border-[#eaecf0] bg-white p-3">
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Blockers</p>
                    <p className="mt-1 text-lg font-semibold text-[#101828]">{blockers.length}</p>
                  </div>
                  <div className="rounded-[10px] border border-[#eaecf0] bg-white p-3">
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Warnings</p>
                    <p className="mt-1 text-lg font-semibold text-[#101828]">{warnings.length}</p>
                  </div>
                </div>
                <div className="mt-3 grid gap-2">
                  {(preflight?.missing_fields || []).length ? (
                    <div className="rounded-[10px] border border-[#fecdca] bg-[#fff6ed] p-3">
                      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#b54708]">Missing fields</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {preflight.missing_fields.map((field) => <span key={`${market}-${field}`} className="pp-chip">{field}</span>)}
                      </div>
                    </div>
                  ) : null}
                  {(blockers || []).slice(0, 4).map((issue) => renderPreflightIssue(issue, market, "blocker"))}
                  {(!blockers || !blockers.length) && !(warnings || []).length ? (
                    <p className="text-sm text-[#667085]">No blockers detected for this marketplace.</p>
                  ) : null}
                  {(warnings || []).slice(0, 2).map((issue) => renderPreflightIssue(issue, market, "warning"))}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" onClick={() => onRefreshMarketplacePreflight?.(listing.id)}>Rerun preflight</Button>
                  <Button size="sm" variant="secondary" onClick={() => setActivePreviewMarket(market)}>Open preview</Button>
                </div>
                <div className="mt-3 grid gap-2 text-xs text-[#667085]">
                  {market === 'ebay' ? (
                    <>
                      <p>Payment policy: {policy.payment_policy_id || 'missing'}</p>
                      <p>Fulfillment policy: {policy.fulfillment_policy_id || 'missing'}</p>
                      <p>Return policy: {policy.return_policy_id || 'missing'}</p>
                      <p>Merchant location: {policy.merchant_location_key || 'missing'}</p>
                    </>
                  ) : (
                    <>
                      <p>Assisted workflow: {policy.assisted_publish ? 'yes' : 'manual'}</p>
                      <p>Bridge required: {policy.browser_bridge_required ? 'yes' : 'no'}</p>
                      <p>Final submit: {policy.final_submit_supported ? 'yes' : 'handoff only'}</p>
                    </>
                  )}
                </div>
                <details className="mt-3 rounded-[10px] border border-[#eaecf0] bg-white p-3">
                  <summary className="cursor-pointer text-sm font-semibold text-[#101828]">Advanced payload preview</summary>
                  <pre className="mt-3 overflow-auto rounded-[10px] bg-[#0b1120] p-3 text-xs text-[#cbd5e1]">{JSON.stringify(payloadPreview, null, 2)}</pre>
                </details>
              </div>
            );
          })}
        </div>
      </div>

      <div className="mb-4 rounded-[14px] border border-[#e5e7eb] bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-[#101828]">Image review</p>
            <p className="mt-1 text-sm text-[#667085]">Approve the actual item photos, keep source/reference images clearly labeled, and set the primary image before publish.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="pp-chip">{listingImages.length} attached</span>
            <span className="pp-chip">{Number(readinessSummary.actual_image_count || 0)} actual item</span>
            <span className="pp-chip">{Number(readinessSummary.reference_image_count || 0)} reference</span>
            <label className="inline-flex cursor-pointer items-center rounded-[10px] border border-[#d0d5dd] bg-white px-3 py-2 text-sm font-medium text-[#101828]">
              Upload actual photos
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                className="hidden"
                onChange={async (event) => {
                  const files = event.target.files;
                  await uploadActualPhotos(files);
                  event.target.value = "";
                }}
              />
            </label>
          </div>
        </div>
        {readinessSummary.manual_photo_needed ? (
          <div className="mt-3 rounded-[12px] border border-[#fecdca] bg-[#fff6ed] px-3 py-2 text-sm text-[#b54708]">
            Only source/reference photos are attached right now. Add or approve actual item photos before publishing.
          </div>
        ) : null}
        <div className="mt-4 space-y-4">
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Actual item photos, approved</p>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {approvedActualImages.map((image, index) => renderImageCard(image, index, "approved"))}
            </div>
          </div>
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Actual item photos, pending review</p>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {pendingActualImages.map((image, index) => renderImageCard(image, index, "pending"))}
            </div>
          </div>
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Reference/source images</p>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {referenceImages.map((image, index) => renderImageCard(image, index, "reference"))}
            </div>
          </div>
          {rejectedImages.length ? (
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Rejected images</p>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {rejectedImages.map((image, index) => renderImageCard(image, index, "rejected"))}
              </div>
            </div>
          ) : null}
          {!listingImages.length ? (
            <div className="rounded-[12px] border border-dashed border-[#d0d5dd] bg-[#fcfcfd] p-4 text-sm text-[#667085]">
              No images attached yet. Upload item photos or import source/reference images before publishing.
            </div>
          ) : null}
        </div>
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <div id="listing-condition-section" className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-[#101828]">Condition review</p>
              <p className="mt-1 text-sm text-[#667085]">Do not assume new condition. Keep imported/open-box items cautious until verified.</p>
            </div>
            <span className="pp-chip">{Math.round(Number(conditionData.condition_confidence || 0) * 100)}% confidence</span>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div>
              <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Condition label</label>
              <Input defaultValue={listing.condition || ""} placeholder="Needs review / Used / Open box" onBlur={(e) => onSave(listing.id, { condition: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Condition bucket</label>
              <select className="pp-input mt-1 h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]" value={conditionData.condition_bucket || "needs_review"} onChange={(e) => updateConditionField("condition_bucket", e.target.value)}>
                {["needs_review","open_box_or_used_unknown","open_box","used","new_in_box","parts_only","import_condition_unverified"].map((option) => (
                  <option key={option} value={option}>{option.replaceAll("_", " ")}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {[
              ["open_box", "Open box"],
              ["new_in_box", "New in box"],
              ["used", "Used"],
              ["parts_only", "Parts only"],
              ["missing_accessories", "Missing accessories"],
            ].map(([key, label]) => (
              <label key={key} className="flex items-center gap-2 rounded-[10px] border border-[#e5e7eb] bg-[#fcfcfd] px-3 py-2 text-sm text-[#475467]">
                <input type="checkbox" checked={Boolean(conditionData[key])} onChange={(e) => updateConditionField(key, e.target.checked)} />
                {label}
              </label>
            ))}
          </div>
          <div className="mt-3">
            <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Condition notes</label>
            <textarea
              defaultValue={conditionData.item_condition_notes || ""}
              className="mt-1 min-h-24 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828]"
              placeholder="Describe wear, testing status, packaging condition, and included accessories."
              onBlur={(e) => updateConditionField("item_condition_notes", e.target.value)}
            />
          </div>
        </div>

        <div id="listing-shipping-section" className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-[#101828]">Shipping readiness</p>
              <p className="mt-1 text-sm text-[#667085]">Track estimated versus verified package data before eBay publish.</p>
            </div>
            <span className="pp-chip">
              {shippingProfile.estimated
                ? "Estimated shipping"
                : shippingProfile.manual_measurement_needed
                  ? "Needs measurement"
                  : "Measurements present"}
            </span>
          </div>
          {shippingProfile.estimated ? (
            <div className="mt-3 rounded-[12px] border border-[#fecdca] bg-[#fff6ed] px-3 py-2 text-xs text-[#b54708]">
              Shipping values are estimated from Vine draft data and should be reviewed before final publish.
            </div>
          ) : null}
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div>
              <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Package weight</label>
              <Input type="number" defaultValue={shippingProfile.package_weight || ""} placeholder="lb" onBlur={(e) => updateShippingField("package_weight", e.target.value ? Number(e.target.value) : null)} />
            </div>
            <div>
              <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Shipping class</label>
              <select className="pp-input mt-1 h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]" value={shippingProfile.shipping_class_suggestion || "usps_ground_advantage"} onChange={(e) => updateShippingField("shipping_class_suggestion", e.target.value)}>
                {["usps_ground_advantage","standard_ground","priority_mail","ups_ground","local_pickup_only","manual_review"].map((option) => (
                  <option key={option} value={option}>{option === "usps_ground_advantage" ? "USPS Ground Advantage" : option.replaceAll("_", " ")}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            {["length","width","height"].map((key) => (
              <div key={key}>
                <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Package {key}</label>
                <Input
                  type="number"
                  defaultValue={(shippingProfile.package_dimensions || {})[key] || ""}
                  placeholder="in"
                  onBlur={(e) => updateShippingField("package_dimensions", {
                    ...(shippingProfile.package_dimensions || {}),
                    [key]: e.target.value ? Number(e.target.value) : null,
                  })}
                />
              </div>
            ))}
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {[
              ["fragile", "Fragile"],
              ["oversize", "Oversized"],
              ["battery", "Battery"],
              ["liquid", "Liquid"],
              ["hazmat", "Hazmat review"],
              ["local_pickup_recommended", "Local pickup recommended"],
            ].map(([key, label]) => (
              <label key={key} className="flex items-center gap-2 rounded-[10px] border border-[#e5e7eb] bg-[#fcfcfd] px-3 py-2 text-sm text-[#475467]">
                <input type="checkbox" checked={Boolean(shippingProfile[key])} onChange={(e) => updateShippingField(key, e.target.checked)} />
                {label}
              </label>
            ))}
          </div>
          <div className="mt-4 grid gap-2">
            {Object.entries(readinessSummary.shipping_checklist || {}).map(([key, value]) => (
              <div key={key} className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-[#fcfcfd] px-3 py-2 text-sm">
                <span className="text-[#475467]">{key.replaceAll("_", " ")}</span>
                <span className={value ? "text-[#027a48]" : "text-[#b42318]"}>{value ? "Yes" : "No"}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2 rounded-[12px] border border-[#e5e7eb] bg-[#f9fafb] p-3">
        <select
          className="h-10 rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
          value={selectedTemplateId}
          onChange={(e) => setSelectedTemplateId(e.target.value)}
        >
          <option value="">Use Template…</option>
          {templates.map((template) => (
            <option key={template.id} value={String(template.id)}>
              {template.name}
            </option>
          ))}
        </select>
        <Button
          size="sm"
          variant="outline"
          disabled={!selectedTemplateId}
          onClick={() =>
            onApplyTemplate(listing.id, Number(selectedTemplateId))
          }
        >
          Apply Template
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() =>
            onSaveTemplate({
              user_id: listing.user_id,
              name: `${listing.category_suggestion || listing.category_id || "General"} Defaults`,
              category_id: listing.category_id || null,
              is_category_default: true,
              fields: {
                title: listing.title,
                description: listing.description,
                condition: listing.condition,
                listing_price: listing.listing_price || listing.suggested_price,
              },
            })
          }
        >
          Save as Default
        </Button>
      </div>

      <div className="space-y-3">
        <Input
          id="listing-title-field"
          defaultValue={listing.title || ""}
          placeholder="Short clear title (ex: Vintage Canon camera with lens)"
          onBlur={(e) => onSave(listing.id, { title: e.target.value })}
          title="This is the headline buyers see first."
        />
        <textarea
          id="listing-description-field"
          defaultValue={listing.description || ""}
          placeholder="Describe condition, size, defects, accessories, and what is included."
          className="min-h-28 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
          onBlur={(e) => onSave(listing.id, { description: e.target.value })}
          title="Explain the item in plain words so anyone can understand quickly."
        />
        <Input
          id="listing-price-field"
          type="number"
          defaultValue={listing.suggested_price || ""}
          placeholder="Suggested price"
          onBlur={(e) =>
            onSave(listing.id, { suggested_price: Number(e.target.value) })
          }
          title="Set a clear asking price."
        />
      </div>

      <div id="listing-category-section" className="mt-4 rounded-[14px] border border-[#e5e7eb] bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-[#101828]">Category</p>
            <p className="mt-1 text-sm text-[#667085]">Choose the clearest category suggestion and keep the marketplace category in sync.</p>
          </div>
          <span className="pp-chip">{listing.category_suggestion || listing.category_id || 'Category pending'}</span>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <Input
            id="listing-category-field"
            defaultValue={listing.category_id || ""}
            placeholder="Marketplace category ID"
            onBlur={(e) => onSave(listing.id, { category_id: e.target.value })}
          />
          <Input
            defaultValue={listing.category_suggestion || ""}
            placeholder="Category suggestion / hint"
            onBlur={(e) => onSave(listing.id, { category_suggestion: e.target.value })}
          />
        </div>
      </div>

      <div className="mt-4 rounded-[14px] border border-[#e5e7eb] bg-white p-4">
        <div className="mb-4 grid gap-3 md:grid-cols-3">
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Draft quality</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">{draftMeta.draft_quality || intelligence.draft_quality || 'Pending'}</p>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Generation source</p>
            <p className="mt-2 text-lg font-semibold capitalize text-[#101828]">{draftMeta.generation_source || intelligence.generation_source || 'Fallback'}</p>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Readiness</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">{readiness.ready_for_publish ? 'Publishable' : 'Review required'}</p>
          </div>
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-sm font-semibold text-[#101828]">Missing information</p>
            <div className="mt-3 space-y-2">
              {(intelligence.missing_information || []).length ? (
                intelligence.missing_information.map((item) => (
                  <div key={item} className="rounded-[10px] border border-[#e5e7eb] bg-white px-3 py-2 text-sm text-[#667085]">
                    {item}
                  </div>
                ))
              ) : (
                <p className="text-sm text-[#667085]">No missing-information checklist has been generated yet.</p>
              )}
            </div>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-sm font-semibold text-[#101828]">Photo review notes</p>
            <div className="mt-3 space-y-2">
              {(intelligence.photo_notes || []).length ? (
                intelligence.photo_notes.map((item) => (
                  <div key={item} className="rounded-[10px] border border-[#e5e7eb] bg-white px-3 py-2 text-sm text-[#667085]">
                    {item}
                  </div>
                ))
              ) : (
                <p className="text-sm text-[#667085]">No photo review notes available.</p>
              )}
            </div>
          </div>
        </div>

        <div id="listing-item-specifics-section" className="mt-4 rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
          <p className="text-sm font-semibold text-[#101828]">Suggested item specifics</p>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[#667085]">
            <span>Edit the actual values here so the eBay preflight can clear required aspects.</span>
            {approximateSpecificFields.size ? <span className="pp-chip">{approximateSpecificFields.size} approximated</span> : null}
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {Object.entries(listing.item_specifics || {}).length ? (
              Object.entries(listing.item_specifics || {}).map(([key, value]) => (
                <div key={key} className="rounded-[10px] border border-[#e5e7eb] bg-white px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{key}</p>
                    <span className={`rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] ${provenanceTone(ebaySpecificsProvenance[key] || (approximateSpecificFields.has(key) ? "approximate" : ""))}`}>
                      {provenanceLabel(ebaySpecificsProvenance[key] || (approximateSpecificFields.has(key) ? "approximate" : ""))}
                    </span>
                    <Button size="sm" variant="ghost" onClick={() => updateItemSpecificField(key, "")}>Clear</Button>
                  </div>
                  <Input
                    className="mt-2"
                    defaultValue={String(value)}
                    placeholder={`Value for ${key}`}
                    onBlur={(e) => updateItemSpecificField(key, e.target.value)}
                  />
                </div>
              ))
            ) : (
              <p className="text-sm text-[#667085]">No structured specifics generated yet.</p>
            )}
          </div>
          <div className="mt-4 grid gap-2 md:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)_auto]">
            <Input
              value={newItemSpecific.key}
              placeholder="Specific name"
              onChange={(e) => setNewItemSpecific((current) => ({ ...current, key: e.target.value }))}
            />
            <Input
              value={newItemSpecific.value}
              placeholder="Specific value"
              onChange={(e) => setNewItemSpecific((current) => ({ ...current, value: e.target.value }))}
            />
            <Button variant="outline" onClick={addItemSpecificField}>Add item specific</Button>
          </div>
        </div>

        <div className="mt-4 rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
          <p className="text-sm font-semibold text-[#101828]">Sold-comps search prompts</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {(intelligence.research_queries || []).length ? (
              intelligence.research_queries.map((query) => (
                <span key={query} className="pp-chip">{query}</span>
              ))
            ) : (
              <p className="text-sm text-[#667085]">No search prompts generated yet.</p>
            )}
          </div>
        </div>
      </div>

      <div id="listing-pricing-section" className="mt-4 rounded-[14px] border border-[#e5e7eb] bg-white p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-[#101828]">Pricing reasoning</p>
            <p className="mt-1 text-sm text-[#667085]">Use this before approving or changing the asking price.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {pricingAnalysis?.recommended_price ? (
              <span className="rounded-full border border-[#dbe7ff] bg-[#eef4ff] px-3 py-1 text-xs font-semibold text-[#2563eb]">
                Recommend ${pricingAnalysis.recommended_price}
              </span>
            ) : null}
            {pricingAnalysis?.warning ? (
              <span className="rounded-full border border-[#fecdca] bg-[#fff6ed] px-3 py-1 text-xs font-semibold text-[#b54708]">
                {pricingAnalysis.warning}
              </span>
            ) : null}
            {pricingAnalysis?.stale ? (
              <span className="rounded-full border border-[#fecdca] bg-[#fff6ed] px-3 py-1 text-xs font-semibold text-[#b54708]">
                Pricing stale
              </span>
            ) : null}
          </div>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Current price</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">${pricingAnalysis?.current_price ?? listing.suggested_price ?? listing.listing_price ?? '0'}</p>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Sold comps baseline</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">${pricingAnalysis?.market_avg_sold ?? '0'}</p>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Confidence</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">{pricingAnalysis?.price_confidence ? `${Math.round(pricingAnalysis.price_confidence * 100)}%` : pricingAnalysis?.confidence ? `${Math.round(pricingAnalysis.confidence * 100)}%` : 'Pending'}</p>
          </div>
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Comparable sales used</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">
              {pricingAnalysis?.comp_count_used ?? ((pricingAnalysis?.historical_comparable_count || 0) + (pricingAnalysis?.external_comparable_count || 0))}
            </p>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">External market avg</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">${pricingAnalysis?.external_market_avg_sold ?? '0'}</p>
          </div>
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-4">
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Quick sale</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">${pricingAnalysis?.quick_sale_price ?? '0'}</p>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Floor</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">${pricingAnalysis?.floor_price ?? '0'}</p>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Stretch</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">${pricingAnalysis?.stretch_price ?? '0'}</p>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Marketplace priority</p>
            <p className="mt-2 text-lg font-semibold capitalize text-[#101828]">{pricingAnalysis?.recommended_marketplace_priority || 'ebay'}</p>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => onApplyPricing?.(listing.id, { strategy: 'recommended' })}>Apply list price</Button>
          <Button size="sm" variant="outline" onClick={() => onApplyPricing?.(listing.id, { strategy: 'quick_sale' })}>Apply quick-sale</Button>
          <Button size="sm" variant="outline" onClick={() => onApplyPricing?.(listing.id, { strategy: 'floor' })}>Apply floor</Button>
          <Button size="sm" variant="secondary" onClick={() => onRefreshPricing?.(listing.id)}>Rerun pricing</Button>
          <Button size="sm" variant="ghost" onClick={() => setShowExcludedComps((value) => !value)}>
            {showExcludedComps ? 'Hide excluded comps' : 'Show excluded comps'}
          </Button>
        </div>
        <div className="mt-3 rounded-[12px] border border-[#e5e7eb] bg-[#f8fafc] p-3">
          <p className="text-sm text-[#475467]">
            {pricingAnalysis?.pricing_explanation || pricingAnalysis?.reasoning || 'Pricing reasoning becomes richer once historical sold data and external comps are available for this item.'}
          </p>
        </div>
        {pricingAnalysis?.condition_adjustment_explanation ? (
          <p className="mt-3 text-sm text-[#667085]">{pricingAnalysis.condition_adjustment_explanation}</p>
        ) : null}
        {pricingAnalysis?.shipping_price_interaction_note ? (
          <p className="mt-1 text-sm text-[#667085]">{pricingAnalysis.shipping_price_interaction_note}</p>
        ) : null}
        {(pricingAnalysis?.comparable_titles || []).length ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {pricingAnalysis.comparable_titles.map((title) => (
              <span key={title} className="pp-chip">{title}</span>
            ))}
          </div>
        ) : null}
        <div className="mt-4 rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-semibold text-[#101828]">Comparable listings</p>
            <span className="text-xs text-[#667085]">{visibleComps.length} shown</span>
          </div>
          <div className="mt-3 space-y-2">
            {visibleComps.length ? visibleComps.map((comp, index) => (
              <div key={`${comp.source_marketplace}-${comp.title}-${index}`} className="rounded-[10px] border border-[#e5e7eb] bg-white px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium text-[#101828]">{comp.title}</p>
                    <p className="text-xs text-[#667085]">
                      {comp.source_marketplace} · {comp.comp_type} · {comp.condition || 'condition unknown'}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-semibold text-[#101828]">${comp.total_price ?? comp.price}</p>
                    <p className="text-xs text-[#667085]">{Math.round(Number(comp.relevance_score || comp.confidence || 0) * 100)}% relevance</p>
                  </div>
                </div>
                <p className="mt-2 text-xs text-[#667085]">
                  {comp.include ? (comp.reason_included || 'Included') : (comp.reason_excluded || 'Excluded')}
                </p>
              </div>
            )) : (
              <p className="text-sm text-[#667085]">No comparable listings available yet.</p>
            )}
          </div>
        </div>
        <div className="mt-4 rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
          <p className="text-sm font-semibold text-[#101828]">Manual comp entry</p>
          <div className="mt-3 grid gap-3 md:grid-cols-4">
            <Input value={manualComp.title} placeholder="Comp title" onChange={(e) => setManualComp((current) => ({ ...current, title: e.target.value }))} />
            <Input type="number" value={manualComp.price} placeholder="Price" onChange={(e) => setManualComp((current) => ({ ...current, price: e.target.value }))} />
            <Input value={manualComp.condition} placeholder="Condition" onChange={(e) => setManualComp((current) => ({ ...current, condition: e.target.value }))} />
            <Button size="sm" onClick={addManualComp}>Add manual comp</Button>
          </div>
        </div>
      </div>

      {workflowPreferences?.listing_preview_mode === 'marketplace' ? (
        <div className="mt-4 rounded-[14px] border border-[#e5e7eb] bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-[#101828]">Marketplace preview</p>
              <p className="text-xs text-[#667085]">These are styled like the live marketplace pages so you can review before approval/publish.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {PLATFORM_OPTIONS.filter((market) => market === 'ebay' || market === 'facebook').map((market) => {
                const selected = activePreviewMarket === market;
                const mode = previewMap[market]?.execution_mode || null;
                return (
                  <button
                    key={`preview-${market}`}
                    type="button"
                    onClick={() => setActivePreviewMarket(market)}
                    className={`rounded-full border px-3 py-1 text-xs font-semibold capitalize transition ${
                      selected ? 'border-[#2563eb] bg-[#eef4ff] text-[#2563eb]' : 'border-[#e5e7eb] bg-white text-[#475467]'
                    }`}
                  >
                    {market}
                    {mode ? ` · ${String(mode).replace('_', ' ')}` : ''}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-2">
            <MarketplacePreviewFrame
              market="ebay"
              listing={listing}
              previewEntry={previewMap.ebay || activePreviewEntry}
              previewImages={previewImages}
              statusMap={statusMap}
              crosspostPreviewLoading={crosspostPreviewLoading}
            />
            <MarketplacePreviewFrame
              market="facebook"
              listing={listing}
              previewEntry={previewMap.facebook || activePreviewEntry}
              previewImages={previewImages}
              statusMap={statusMap}
              crosspostPreviewLoading={crosspostPreviewLoading}
            />
          </div>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          variant="outline"
          onClick={() => setOpenEditor(true)}
          title="Open premium photo editor."
        >
          <Camera size={16} /> Edit photos
        </Button>
        <Button
          variant="secondary"
          onClick={() => onGenerate(listing.id)}
          title="Use AI to improve title and description."
        >
          <WandSparkles size={16} /> AI Enhance
        </Button>
        {listing.needs_review || listing.restricted_review_required || listing.status === 'draft' ? (
          <>
            <Button variant="outline" onClick={() => onApprove(listing.id)} title="Move this draft from review into the ready queue.">
              Approve Draft
            </Button>
            <Button variant="outline" onClick={() => (onApproveAndNext ? onApproveAndNext(listing.id) : onApprove(listing.id))} title="Approve this draft and move to the next listing in the current queue.">
              Approve & Next
            </Button>
          </>
        ) : null}
        {onDelete ? (
          <Button
            variant="danger"
            onClick={() => onDelete(listing.id)}
            title="Permanently delete this listing and its attached media."
          >
            <Trash2 size={16} /> Delete
          </Button>
        ) : null}
        <Button
          disabled={publishState.loading || !selectedPlatforms.length || (requiresApproval && listing.status !== 'ready')}
          onClick={() => onPublish(listing.id, selectedPlatforms)}
          data-tour="publish"
          title="Publish this listing to selected marketplaces."
        >
          <Sparkles size={16} />{" "}
          {publishState.loading ? "Publishing..." : "Publish Selected"}
        </Button>
      </div>

      <div className="mt-4">
        <p className="mb-2 text-sm font-semibold text-[#101828]">Choose marketplaces</p>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
          {PLATFORM_OPTIONS.map((market) => {
            const enabled = selectedPlatforms.includes(market);
            const status = statusMap[market] || "Not published";
            return (
              <button
                key={market}
                type="button"
                className={`rounded-[10px] border p-3 text-left transition ${
                  enabled ? 'border-[#2563eb] bg-[#eef4ff]' : 'border-[#e5e7eb] bg-white'
                }`}
                onClick={() => togglePlatform(market)}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold capitalize text-[#101828]">{market}</span>
                  <span className={`inline-block h-2.5 w-2.5 rounded-full ${enabled ? 'bg-[#2563eb]' : 'bg-[#d0d5dd]'}`} />
                </div>
                <span className="mt-2 inline-block rounded-full border border-[#e5e7eb] bg-[#f9fafb] px-2 py-0.5 text-xs font-medium text-[#667085]">
                  {status}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {publishState.error && (
        <p className="mt-3 text-sm text-[#b42318]">{publishState.error}</p>
      )}
      <MarketplaceStatusPanel statuses={statuses} />

      <PhotoEditorModal
        open={openEditor}
        listing={listing}
        onClose={() => setOpenEditor(false)}
        onApply={onPhotoUpdated}
      />
    </div>
  );
}
