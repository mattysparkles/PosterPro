import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import { Bot, Save, Sparkles, Truck } from "lucide-react";

import AppShell from "../../components/layout/AppShell";
import Button from "../../components/ui/button";
import Input from "../../components/ui/input";
import Select from "../../components/ui/select";
import Textarea from "../../components/ui/textarea";
import Checkbox from "../../components/ui/checkbox";
import PageHeader from "../../components/ui/page-header";
import SectionPanel from "../../components/ui/section-panel";
import StatusPill from "../../components/ui/status-pill";
import { useAuth } from "../../contexts/AuthContext";
import useDashboardData from "../../hooks/useDashboardData";
import {
  createMarketplaceImportJob,
  createListing,
  fetchCrosspostJobs,
  fetchCrosspostPreview,
  fetchListing,
  fetchMarketplacePreflight,
  fetchSettingsPanels,
  searchEbayCategories,
  browseEbayCategories,
  fetchEbayCategoryAspects,
  queueCrosspostJob,
  generateListing,
  requestListingRevision,
  updateMarketplaceDescription,
  savePublishListingChanges,
  toggleAutonomousMode,
  updateListing,
  recordManualSale,
  resolveListingCategory,
  researchListingFitment,
} from "../../lib/api";

const CHANNEL_LABELS = {
  ebay: "eBay",
  facebook: "Facebook Marketplace",
  etsy: "Etsy",
  mercari: "Mercari",
  poshmark: "Poshmark",
  depop: "Depop",
  whatnot: "Whatnot",
  vinted: "Vinted",
  offerup: "OfferUp",
};

const PREVIEW_MARKETPLACES = ['ebay', 'facebook', 'mercari', 'vinted', 'offerup', 'poshmark', 'etsy', 'depop'];
const MARKETPLACE_TAB_LABELS = { ebay: 'eBay', facebook: 'Facebook', mercari: 'Mercari', vinted: 'Vinted', offerup: 'OfferUp', poshmark: 'Poshmark', etsy: 'Etsy', depop: 'Depop' };

const MARKETPLACE_PREVIEW_STYLES = {
  ebay: { brand: 'eBay', price: '#111827' }, facebook: { brand: 'Facebook Marketplace', price: '#1877f2' },
  mercari: { brand: 'Mercari', price: '#d92735' }, poshmark: { brand: 'Poshmark', price: '#7b1e3a' },
  etsy: { brand: 'Etsy', price: '#b45309' }, depop: { brand: 'Depop', price: '#111827' },
  whatnot: { brand: 'Whatnot', price: '#5b21b6' }, vinted: { brand: 'Vinted', price: '#007782' },
  offerup: { brand: 'OfferUp', price: '#087f5b' },
};

const MARKETPLACE_MARKS = {
  ebay: { text: 'eBay', className: 'marketplace-mark--ebay' }, facebook: { text: 'f', className: 'marketplace-mark--facebook' },
  mercari: { text: 'M', className: 'marketplace-mark--mercari' }, vinted: { text: 'V', className: 'marketplace-mark--vinted' },
  offerup: { text: 'O', className: 'marketplace-mark--offerup' }, poshmark: { text: 'P', className: 'marketplace-mark--poshmark' },
  etsy: { text: 'E', className: 'marketplace-mark--etsy' }, depop: { text: 'D', className: 'marketplace-mark--depop' },
  whatnot: { text: 'W', className: 'marketplace-mark--whatnot' },
};

function MarketplaceMark({ marketplace, small = false }) {
  const mark = MARKETPLACE_MARKS[marketplace] || { text: String(marketplace || '?').slice(0, 1).toUpperCase(), className: '' };
  const word = marketplace === 'ebay' ? 'eBay' : marketplace === 'facebook' ? 'f' : mark.text;
  return <svg aria-hidden="true" viewBox="0 0 48 48" className={`marketplace-mark ${mark.className} ${small ? 'marketplace-mark--small' : ''}`}><rect className="marketplace-mark-bg" x="1" y="1" width="46" height="46" rx="13" />{marketplace === 'ebay' ? <text x="24" y="30" textAnchor="middle" className="marketplace-mark-word"><tspan fill="#e53238">e</tspan><tspan fill="#0064d2">B</tspan><tspan fill="#f5af02">a</tspan><tspan fill="#86b817">y</tspan></text> : <text x="24" y="30" textAnchor="middle" className="marketplace-mark-word">{word}</text>}</svg>;
}

function MarketplaceVisualPreview({ entry, formatMoney }) {
  const payload = entry?.payload || {};
  const marketplace = String(entry?.marketplace || payload.marketplace || 'ebay').toLowerCase();
  const style = MARKETPLACE_PREVIEW_STYLES[marketplace] || MARKETPLACE_PREVIEW_STYLES.ebay;
  // Preview only what the destination formatter actually placed in its payload.
  // Falling back to canonical images would conceal a broken marketplace payload.
  const images = Array.from(new Set((Array.isArray(payload.image_urls) ? payload.image_urls : []).filter(Boolean))).slice(0, 12);
  const price = payload.price ?? payload.listing_price ?? payload.starting_bid;
  const shipping = payload.shipping_policy || payload.shipping || {};
  const shippingToken = typeof shipping === 'string' ? shipping : (shipping.service || shipping.service_name || shipping.domestic_service || shipping.shipping_service || shipping.mode || payload.delivery_method || 'Not specified');
  const shippingLabels = { usps_ground_advantage: 'USPS Ground Advantage', usps_priority: 'USPS Priority Mail', ups_ground: 'UPS Ground', fedex_ground: 'FedEx Ground', local_pickup: 'Local pickup', local_and_shipping: 'Local pickup or shipping', shipping_only: 'Shipping', local_only: 'Local pickup' };
  const shippingText = shippingLabels[String(shippingToken).toLowerCase()] || String(shippingToken).replaceAll('_', ' ');
  const specifics = payload.item_specifics || payload.itemSpecifics || payload.aspects || {};
  const description = String(payload.description || '').replace(/<br\s*\/?\s*>/gi, '\n').replace(/<\/(p|div|li|h[1-6])\s*>/gi, '\n').replace(/<[^>]+>/g, '').replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&').replace(/&quot;/gi, '"').trim();
  const gallery = <div className={`marketplace-listing-gallery marketplace-listing-gallery--${marketplace}`}>
    <div className="marketplace-listing-hero">{images[0] ? <img src={images[0]} alt={payload.title || 'Listing photo'} /> : <span>No listing photo</span>}</div>
    {images.length > 1 ? <div className="marketplace-listing-thumbnails">{images.slice(0, 8).map((url, index) => <img key={`${url}-${index}`} src={url} alt={`Photo ${index + 1}`} />)}</div> : null}
  </div>;
  const category = payload.category_name || payload.category_path || payload.category_hint || payload.category?.name || (String(payload.category_id || '').match(/^\d+$/) ? `Category ID ${payload.category_id}` : payload.category_id) || 'Category not selected';
  const specs = Object.entries(specifics).slice(0, 12);
  const details = <div className={`marketplace-listing-details marketplace-listing-details--${marketplace}`}>
    <p className="marketplace-listing-category">{category}</p>
    <h3>{payload.title || 'Untitled listing'}</h3>
    <p className="marketplace-listing-price" style={{ color: style.price }}>{formatMoney(price)}</p>
    <p className="marketplace-listing-condition">{payload.condition || 'Condition not specified'}{payload.quantity > 1 ? ` · ${payload.quantity} available` : ''}</p>
    <div className="marketplace-listing-shipping"><span>Shipping</span><strong>{shippingText}</strong></div>
    {marketplace === 'facebook' ? <div className="marketplace-preview-actions"><button type="button">Message seller</button><button type="button">Save</button><p>Seller details · Marketplace</p></div> : null}
    {marketplace === 'ebay' ? <button type="button" className="marketplace-preview-buy">Buy It Now</button> : null}
    {specs.length ? <dl className="marketplace-listing-specifics">{specs.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{Array.isArray(value) ? value.join(', ') : String(value ?? '—')}</dd></div>)}</dl> : null}
    {description ? <details className="marketplace-listing-description"><summary>Description</summary><div>{description}</div></details> : null}
  </div>;
  return <div className={`marketplace-listing-preview marketplace-listing-preview--${marketplace}`}>
    <div className="marketplace-preview-caption"><MarketplaceMark marketplace={marketplace} /><span>{style.brand} listing preview</span><span className="marketplace-preview-only">Preview only</span></div>
    <div className="marketplace-listing-layout">{gallery}{details}</div>
  </div>;
}

const SHIPPING_SCOPE_OPTIONS = [
  { value: "local_only", label: "Local only" },
  { value: "shipping_only", label: "Shipping only" },
  { value: "local_and_shipping", label: "Local + shipping" },
];

const FACEBOOK_RENEWAL_OPTIONS = [
  { value: "manual", label: "Manual renewals" },
  { value: "daily", label: "Daily renewal plan" },
  { value: "scheduled", label: "Scheduled renewal plan" },
];

const CONDITION_OPTIONS = ["New", "Used", "Open box", "Refurbished", "Pre-owned", "For parts or not working", "Parts only"];
const PUBLISH_MODE_OPTIONS = [
  { value: "direct_api", label: "Direct marketplace API" },
  { value: "browser_assisted", label: "Browser-assisted" },
  { value: "manual_or_provider", label: "Manual/provider handoff" },
  { value: "manual", label: "Manual" },
];
const FULFILLMENT_OPTIONS = [
  { value: "shipping", label: "Shipping" },
  { value: "local_or_shipping", label: "Local pickup or shipping" },
  { value: "local_pickup", label: "Local pickup" },
  { value: "live_sale", label: "Live sale" },
];
const SHIPPING_SERVICE_OPTIONS = [
  { value: "usps_ground_advantage", label: "USPS Ground Advantage" },
  { value: "usps_priority", label: "USPS Priority Mail" },
  { value: "ups_ground", label: "UPS Ground" },
  { value: "fedex_ground", label: "FedEx Ground" },
  { value: "manual", label: "Choose manually" },
];

function defaultMarketplaceData() {
  return {
    crosspost_mode: "approval_required",
    targets: ["ebay"],
    import_sources: [],
    source_marketplace: null,
    manual_entry: true,
    shipping: {
      mode: "calculated",
      domestic_service: "usps_ground_advantage",
      international_enabled: false,
      local_pickup_enabled: false,
      free_shipping: false,
      handling_time_days: 2,
      facebook_meetup_notes: "",
    },
    channels: {
      ebay: {
        enabled: true,
        publish_mode: "direct_api",
        status: "draft",
        fulfillment: "shipping",
        shipping_available: true,
      },
      facebook: {
        enabled: false,
        publish_mode: "manual_or_provider",
        status: "manual_setup",
        fulfillment: "local_or_shipping",
        shipping_available: false,
        renewal_mode: "manual",
      },
      etsy: { enabled: false, publish_mode: "manual_or_provider", status: "draft", fulfillment: "shipping", shipping_available: true },
      mercari: { enabled: false, publish_mode: "manual_or_provider", status: "draft", fulfillment: "shipping", shipping_available: true },
      poshmark: { enabled: false, publish_mode: "manual_or_provider", status: "draft", fulfillment: "shipping", shipping_available: true },
      depop: { enabled: false, publish_mode: "manual_or_provider", status: "draft", fulfillment: "shipping", shipping_available: true },
      whatnot: { enabled: false, publish_mode: "manual_or_provider", status: "draft", fulfillment: "live_sale", shipping_available: true },
      vinted: { enabled: false, publish_mode: "manual_or_provider", status: "draft", fulfillment: "shipping", shipping_available: true },
      offerup: { enabled: false, publish_mode: "manual_or_provider", status: "draft", fulfillment: "local_or_shipping", shipping_available: true },
    },
  };
}

function normalizeListingForm(listing) {
  const marketplaceData = {
    ...defaultMarketplaceData(),
    ...(listing?.marketplace_data || {}),
  };
  marketplaceData.shipping = {
    ...defaultMarketplaceData().shipping,
    ...(marketplaceData.shipping || {}),
  };
  marketplaceData.channels = {
    ...defaultMarketplaceData().channels,
    ...(marketplaceData.channels || {}),
  };

  return {
    title: listing?.title || "",
    description: listing?.description || "",
    category_id: listing?.category_id || "",
    category_suggestion: listing?.category_suggestion || "",
    condition: listing?.condition || "",
    quantity: String(listing?.quantity || 1),
    listing_price: listing?.listing_price ?? listing?.suggested_price ?? "",
    suggested_price: listing?.suggested_price ?? "",
    purchase_cost: listing?.purchase_cost ?? "",
    shipping_cost: listing?.shipping_cost ?? "",
    source_type: listing?.source_type || "manual",
    source_marketplace: marketplaceData.source_marketplace || "",
    tags: (listing?.tags || []).join(", "),
    image_urls: (listing?.image_urls || []).join("\n"),
    item_specifics_json: JSON.stringify(listing?.item_specifics || {}, null, 2),
    source_metadata_json: JSON.stringify(listing?.source_metadata || {}, null, 2),
    marketplace_data: marketplaceData,
    needs_review: listing?.needs_review ?? true,
  };
}

function parseJsonField(value, fieldName) {
  if (!value.trim()) return {};
  try {
    return JSON.parse(value);
  } catch (error) {
    throw new Error(`${fieldName} must be valid JSON.`);
  }
}

export default function ListingWorkspacePage() {
  const router = useRouter();
  const { listingId } = router.query;
  const isNew = listingId === "new";
  const { user } = useAuth();
  const { autonomousConfig, reload: reloadDashboard } = useDashboardData(user?.id);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [updateFeedback, setUpdateFeedback] = useState(null);
  const [generating, setGenerating] = useState(false);
  const [revisionFields, setRevisionFields] = useState([]);
  const [revisionNote, setRevisionNote] = useState("");
  const [revisionPriority, setRevisionPriority] = useState(0);
  const [queueingCrosspost, setQueueingCrosspost] = useState(false);
  const [importing, setImporting] = useState(false);
  const [listing, setListing] = useState(null);
  // Null means "all currently active remote destinations".  Once the
  // operator selects a subset, Save & publish changes carries that explicit
  // destination scope to the shared mutation/update service.
  const [saveTargetMarketplaces, setSaveTargetMarketplaces] = useState(null);
  const returnToListings = useMemo(() => {
    const candidate = typeof router.query.return_to === 'string' ? router.query.return_to : '';
    return candidate.startsWith('/listings') ? candidate : '/listings';
  }, [router.query.return_to]);
  const [crosspostPreview, setCrosspostPreview] = useState([]);
  const [crosspostJobs, setCrosspostJobs] = useState([]);
  const [workflow, setWorkflow] = useState({
    review_before_publish: true,
    auto_publish_after_approval: false,
    default_preview_marketplace: 'ebay',
  });
  const [previewMarketplace, setPreviewMarketplace] = useState('ebay');
  const [selectedImageIndex, setSelectedImageIndex] = useState(0);
  const [photoLightboxOpen, setPhotoLightboxOpen] = useState(false);
  const [variantMarketplace, setVariantMarketplace] = useState('ebay');
  const [variantDraft, setVariantDraft] = useState('');
  const [savingVariant, setSavingVariant] = useState(false);
  const [manualSaleAmount, setManualSaleAmount] = useState('');
  const [manualSaleQuantity, setManualSaleQuantity] = useState('1');
  const [recordingSale, setRecordingSale] = useState(false);
  const [form, setForm] = useState(() => normalizeListingForm(null));
  useEffect(() => {
    if (!photoLightboxOpen) return undefined;
    const closeOnEscape = (event) => { if (event.key === "Escape") setPhotoLightboxOpen(false); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [photoLightboxOpen]);
  const [categoryBrowseNodes, setCategoryBrowseNodes] = useState([]);
  const [categoryBrowseTrail, setCategoryBrowseTrail] = useState([]);
  const [categoryBrowseSource, setCategoryBrowseSource] = useState('');
  const [categoryQuery, setCategoryQuery] = useState("");
  const [categorySearchResults, setCategorySearchResults] = useState([]);
  const [categorySearching, setCategorySearching] = useState(false);
  const [categoryBrowseTree, setCategoryBrowseTree] = useState("0");
  const [categoryAspects, setCategoryAspects] = useState(null);
  const [categoryAspectLoading, setCategoryAspectLoading] = useState(false);
  const [categoryAspectValues, setCategoryAspectValues] = useState({});
  const [fitmentResearch, setFitmentResearch] = useState(null);
  const [researchingFitment, setResearchingFitment] = useState(false);
  const categoryChoices = useMemo(() => {
    const metadata = listing?.source_metadata || {};
    const candidates = Array.isArray(metadata.category_candidates) ? metadata.category_candidates : [];
    const rows = candidates.map((candidate) => ({ value: String(candidate.category_id || ""), label: `${candidate.category_name || candidate.category_id} — ${candidate.breadcrumb || candidate.category_tree || ""}` })).filter((row) => /^\d+$/.test(row.value));
    if (listing?.category_id && /^\d+$/.test(String(listing.category_id)) && !rows.some((row) => row.value === String(listing.category_id))) rows.unshift({ value: String(listing.category_id), label: `${listing.category_suggestion || "Current category"} — ${listing.category_id}` });
    return rows;
  }, [listing]);
  const loadCategoryChildren = async (parentId = "", label = "All Categories", trail = []) => {
    try {
      const result = await browseEbayCategories(parentId, { treeId: categoryBrowseTree });
      setCategoryBrowseNodes(result?.categories || []);
      setCategoryBrowseSource(result?.source === 'rate_limited' ? 'rate_limited' : result?.stale ? 'stale' : result?.source || 'local');
      setCategoryBrowseTrail(parentId ? [...trail, { id: String(parentId), label }] : []);
    } catch (error) {
      const rateLimited = /429|too many requests|request limit/i.test(error.message || "");
      setCategoryBrowseSource(rateLimited ? 'rate_limited' : 'error');
      toast.error(rateLimited ? "eBay is temporarily rate-limiting category browsing. PosterPro will use saved taxonomy data and avoid repeated requests." : "Could not load eBay categories. Try again in a moment.");
      setCategoryBrowseNodes([]);
    }
  };
  const runCategorySearch = async () => {
    if (!categoryQuery.trim()) return;
    setCategorySearching(true);
    try {
      const result = await searchEbayCategories(categoryQuery.trim(), { treeIds: ["0", "100"] });
      setCategorySearchResults((result?.results || []).slice(0, 5));
    } catch (error) {
      setCategorySearchResults([]);
      const rateLimited = /429|too many requests|request limit/i.test(error.message || "");
      setCategoryBrowseSource(rateLimited ? 'rate_limited' : categoryBrowseSource);
      toast.error(rateLimited ? "eBay is temporarily rate-limiting category search. Saved taxonomy data will be reused when available." : "Could not search eBay categories. Try again in a moment.");
    } finally {
      setCategorySearching(false);
    }
  };
  const loadCategoryAspects = async (result) => {
    if (!result?.category_id || result.leaf === false || result.publishable === false) return;
    setCategoryAspectLoading(true);
    try {
      const data = await fetchEbayCategoryAspects(result.category_id, {
        marketplace: result.marketplace || (String(result.taxonomy_tree_id) === "100" ? "EBAY_MOTORS_US" : "EBAY_US"),
        treeId: result.taxonomy_tree_id || "0",
        treeVersion: result.category_tree_version,
      });
      setCategoryAspects(data);
      const current = listing?.item_specifics || {};
      setCategoryAspectValues(Object.fromEntries(Object.entries(current).map(([key, value]) => [key, Array.isArray(value) ? value[0] : value])));
    } catch (error) {
      setCategoryAspects({ required: [], recommended: [], source: "unavailable", error: error.message });
    } finally { setCategoryAspectLoading(false); }
  };
  const persistCategory = async (result) => {
    const treeId = String(result.taxonomy_tree_id || result.tree_id || "0");
    const marketplace = result.marketplace || (treeId === "100" ? "EBAY_MOTORS_US" : "EBAY_US");
    const path = result.breadcrumb || result.full_path || result.category_path || result.category_name || "";
    const sourceMetadata = {
      ...(listing?.source_metadata || {}),
      category_validation: {
        status: "valid_local",
        marketplace,
        tree_id: treeId,
        tree_version: String(result.category_tree_version || result.tree_version || (treeId === "100" ? "83" : "134")),
        category_id: String(result.category_id),
        category_name: result.category_name,
        full_path: path,
        leaf: true,
        source: "LOCAL_NORMALIZED_TREE",
      },
      taxonomy_marketplace: marketplace,
      taxonomy_tree_id: treeId,
      taxonomy_tree_version: String(result.category_tree_version || result.tree_version || (treeId === "100" ? "83" : "134")),
      category_path: path,
    };
    const saved = await updateListing(listing.id, { category_id: String(result.category_id), category_suggestion: path, source_metadata: sourceMetadata });
    setListing(saved);
    setForm((current) => ({ ...current, category_id: String(saved?.category_id || result.category_id), category_suggestion: saved?.category_suggestion || path }));
    setCategorySearchResults([]); setCategoryQuery("");
    await loadCategoryAspects({ ...result, marketplace, taxonomy_tree_id: treeId });
    toast.success(`${marketplace === "EBAY_MOTORS_US" ? "eBay Motors" : "eBay"} category saved.`);
  };
  const selectSearchCategory = async (result) => {
    if (!result?.category_id || result.publishable === false || result.leaf_verified === false) return;
    try { await persistCategory(result); } catch (error) { toast.error(error.message || "Could not save category."); }
  };
  const selectBrowseCategory = async (node) => {
    if (!node?.category_id || node.has_children || node.leaf === false || node.publishable === false) return;
    try { await persistCategory({ ...node, breadcrumb: node.full_path || [...categoryBrowseTrail.map((entry) => entry.label), node.category_name].filter(Boolean).join(" > "), marketplace: node.marketplace, taxonomy_tree_id: categoryBrowseTree, category_tree_version: node.category_tree_version }); } catch (error) { toast.error(error.message || "Could not save category."); }
  };
  const updateCategoryAspect = async (name, value) => {
    const next = { ...categoryAspectValues, [name]: value };
    setCategoryAspectValues(next);
    if (!listing?.id) return;
    try {
      const saved = await updateListing(listing.id, { item_specifics: { ...(listing.item_specifics || {}), [name]: value } });
      setListing(saved);
      setForm((current) => ({ ...current, item_specifics_json: JSON.stringify(saved?.item_specifics || {}, null, 2) }));
    } catch (error) { toast.error(error.message || "Could not save item specific."); }
  };
  const [importForm, setImportForm] = useState({
    source_marketplace: "facebook",
    import_mode: "manual",
    source_listing_reference: "",
    payload_json: '{\n  "title": "",\n  "description": "",\n  "price": 0,\n  "image_urls": []\n}',
  });

  useEffect(() => {
    if (!router.isReady || !user?.id || !listingId) return;
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const panelsPromise = fetchSettingsPanels().catch(() => null);
        const listingPromise = isNew ? Promise.resolve(null) : fetchListing(listingId);
        const [panels, fetchedListing] = await Promise.all([panelsPromise, listingPromise]);
        if (cancelled) return;
        setWorkflow({
          review_before_publish: panels?.workflow?.review_before_publish ?? true,
          auto_publish_after_approval: panels?.workflow?.auto_publish_after_approval ?? false,
          default_preview_marketplace: panels?.workflow?.default_preview_marketplace || 'ebay',
        });
        setPreviewMarketplace(String(panels?.workflow?.default_preview_marketplace || 'ebay').toLowerCase());
        setListing(fetchedListing);
        setForm(normalizeListingForm(fetchedListing));
        setVariantDraft(String(fetchedListing?.marketplace_descriptions?.[variantMarketplace] || fetchedListing?.canonical_description || fetchedListing?.description || ''));
        setSelectedImageIndex(0);
        if (fetchedListing?.id) {
          const [preview, jobs] = await Promise.all([
            fetchCrosspostPreview(fetchedListing.id, PREVIEW_MARKETPLACES).catch(() => []),
            fetchCrosspostJobs(fetchedListing.id).catch(() => []),
          ]);
          if (cancelled) return;
          setCrosspostPreview(preview || []);
          setCrosspostJobs(jobs || []);
        } else {
          setCrosspostPreview([]);
          setCrosspostJobs([]);
        }
      } catch (error) {
        if (!cancelled) {
          toast.error(error.message);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [isNew, listingId, router.isReady, user?.id]);

  useEffect(() => {
    if (!listing) return;
    setVariantDraft(String(listing.marketplace_descriptions?.[variantMarketplace] || listing.canonical_description || listing.description || ''));
  }, [listing, variantMarketplace]);

  useEffect(() => {
    const metadata = listing?.source_metadata || {};
    if (!listing?.category_id || !metadata.taxonomy_tree_id) return;
    loadCategoryAspects({
      category_id: listing.category_id,
      marketplace: metadata.taxonomy_marketplace || (String(metadata.taxonomy_tree_id) === "100" ? "EBAY_MOTORS_US" : "EBAY_US"),
      taxonomy_tree_id: metadata.taxonomy_tree_id,
      category_tree_version: metadata.taxonomy_tree_version,
      leaf: true,
      publishable: true,
    });
  }, [listing?.id, listing?.category_id, listing?.source_metadata?.taxonomy_tree_id]);

  const saveMarketplaceVariant = async () => {
    if (!listing?.id || !variantDraft.trim()) return;
    setSavingVariant(true);
    try {
      const saved = await updateMarketplaceDescription(listing.id, variantMarketplace, variantDraft.trim());
      setListing(saved);
      toast.success(`${CHANNEL_LABELS[variantMarketplace] || variantMarketplace} description saved.`);
    } catch (error) {
      toast.error(error.message || 'Could not save marketplace description.');
    } finally {
      setSavingVariant(false);
    }
  };

  const copyMercariManualPacket = async () => {
    const specifics = listing?.item_specifics && typeof listing.item_specifics === 'object' ? listing.item_specifics : {};
    const packet = [
      'POSTERPRO MANUAL MERCARI DRAFT — NOT PUBLISHED',
      `Title: ${form.title || '(missing — identify item before listing)'}`,
      `Price: ${form.listing_price || '(missing — enter an evidence-supported or operator-approved price)'}`,
      `Condition: ${form.condition || '(missing — inspect and select accurately)'}`,
      `Description: ${variantMarketplace === 'mercari' ? variantDraft : form.description || '(missing)'}`,
      `Brand: ${specifics.Brand || specifics.brand || '(not recorded)'}`,
      `Manufacturer part/model: ${specifics.MPN || specifics['Manufacturer Part Number'] || specifics.Model || '(not recorded)'}`,
      'Category: Select and verify manually in Mercari; PosterPro has not validated a Mercari taxonomy choice.',
      'Photos: Select reviewed publishable product photos manually. Exclude Slate artwork, QR labels, and internal reference images.',
      'Shipping: Choose a supported Mercari option manually using confirmed packed measurements. Do not guess weight or dimensions.',
      'Before publishing: verify physical item identity, condition, category, required specifics, price evidence, and stock availability.',
    ].join('\n\n');
    try {
      await navigator.clipboard.writeText(packet);
      toast.success('Manual Mercari draft packet copied. Nothing was submitted.');
    } catch (_error) {
      toast.error('Clipboard access is unavailable. Review and copy the draft fields manually; nothing was submitted.');
    }
  };

  const recordSale = async () => {
    if (!listing?.id || !Number(manualSaleAmount)) return;
    setRecordingSale(true);
    try {
      const result = await recordManualSale({ listing_id: listing.id, amount: Number(manualSaleAmount), quantity: Math.max(1, Number(manualSaleQuantity || 1)), channel: 'manual' });
      const refreshed = await fetchListing(listing.id);
      setListing(refreshed);
      setForm(normalizeListingForm(refreshed));
      setManualSaleAmount('');
      toast.success(`Sale recorded. ${result?.fanout?.length ? 'Inventory reconciliation queued.' : 'Inventory updated.'}`);
    } catch (error) {
      toast.error(error.message || 'Could not record sale.');
    } finally {
      setRecordingSale(false);
    }
  };

  useEffect(() => {
    if (!crosspostPreview.length) return;
    if (crosspostPreview.some((entry) => String(entry.marketplace).toLowerCase() === previewMarketplace)) return;
    setPreviewMarketplace(String(crosspostPreview[0]?.marketplace || 'ebay').toLowerCase());
  }, [crosspostPreview, previewMarketplace]);

  const previewMode = router.isReady && (router.query.mode === 'preview' || router.query.preview === '1');

  useEffect(() => {
    if (!previewMode) return;
    const previewSection = document.getElementById('listing-execution-preview');
    previewSection?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [previewMode]);

  const title = useMemo(() => {
    if (isNew) return "New Item";
    return listing?.title || `Listing #${listing?.id || listingId}`;
  }, [isNew, listing, listingId]);

  const itemSpecifics = useMemo(() => {
    try {
      const parsed = JSON.parse(form.item_specifics_json || "{}");
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }, [form.item_specifics_json]);
  const readSpecific = (...keys) => {
    for (const key of keys) {
      const value = itemSpecifics[key];
      if (value !== null && value !== undefined && String(value).trim()) return String(value);
    }
    return "";
  };
  const updateSpecific = (key, value) => {
    setForm((current) => {
      let specifics = {};
      try {
        const parsed = JSON.parse(current.item_specifics_json || "{}");
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) specifics = parsed;
      } catch { /* Preserve the visible field; saving still reports malformed advanced JSON. */ }
      return { ...current, item_specifics_json: JSON.stringify({ ...specifics, [key]: value }, null, 2) };
    });
  };

  const editorImageUrls = useMemo(
    () => form.image_urls.split("\n").map((value) => value.trim()).filter(Boolean).slice(0, 24),
    [form.image_urls],
  );

  useEffect(() => {
    if (selectedImageIndex >= editorImageUrls.length) setSelectedImageIndex(0);
  }, [editorImageUrls.length, selectedImageIndex]);

  const setChannelField = (channel, key, value) => {
    setForm((current) => ({
      ...current,
      marketplace_data: {
        ...current.marketplace_data,
        channels: {
          ...current.marketplace_data.channels,
          [channel]: {
            ...current.marketplace_data.channels[channel],
            [key]: value,
          },
        },
      },
    }));
  };

  const toggleChannel = (channel) => {
    setForm((current) => {
      const enabled = !current.marketplace_data.channels[channel]?.enabled;
      const targets = enabled
        ? Array.from(new Set([...(current.marketplace_data.targets || []), channel]))
        : (current.marketplace_data.targets || []).filter((item) => item !== channel);
      return {
        ...current,
        marketplace_data: {
          ...current.marketplace_data,
          targets,
          channels: {
            ...current.marketplace_data.channels,
            [channel]: {
              ...current.marketplace_data.channels[channel],
              enabled,
            },
          },
        },
      };
    });
  };

  const formatMoney = (value) => {
    if (value === null || value === undefined || value === "") return "—";
    const num = Number(value);
    if (!Number.isFinite(num)) return String(value);
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(num);
  };

  const buildPayload = (nextStatus) => {
    const itemSpecifics = parseJsonField(form.item_specifics_json, "Item specifics");
    const sourceMetadata = parseJsonField(form.source_metadata_json, "Source metadata");
    const sourceAsin = String(sourceMetadata.asin || sourceMetadata.ASIN || "").trim();
    if (sourceAsin && String(itemSpecifics.Model || "").trim().toUpperCase() === sourceAsin.toUpperCase()) delete itemSpecifics.Model;
    const quantity = Math.max(1, Number(form.quantity || 1));

    return {
      status: nextStatus || listing?.status || "draft",
      title: form.title.trim() || null,
      description: form.description.trim() || null,
      category_id: form.category_id.trim() || null,
      category_suggestion: form.category_suggestion.trim() || null,
      condition: form.condition.trim() || null,
      quantity,
      listing_price: form.listing_price === "" ? null : Number(form.listing_price),
      suggested_price: form.suggested_price === "" ? null : Number(form.suggested_price),
      purchase_cost: form.purchase_cost === "" ? null : Number(form.purchase_cost),
      shipping_cost: form.shipping_cost === "" ? null : Number(form.shipping_cost),
      source_type: form.source_type.trim() || "manual",
      source_metadata: sourceMetadata,
      image_urls: form.image_urls
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean),
      tags: form.tags
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      item_specifics: itemSpecifics,
      marketplace_data: {
        ...form.marketplace_data,
        source_marketplace: form.source_marketplace.trim() || null,
        manual_entry: form.source_type === "manual",
      },
      needs_review: workflow.review_before_publish ? true : form.needs_review,
    };
  };

  const saveListing = async (nextStatus) => {
    setSaving(true);
    try {
      const payload = buildPayload(nextStatus);
      const saved = isNew ? await createListing(payload) : await updateListing(listing.id, payload);
      setListing(saved);
      setForm(normalizeListingForm(saved));
      if (saved?.id) {
        // These panels are supplementary diagnostics, not part of the save
        // contract. Waiting for two extra network requests made a successful
        // draft save feel stalled, especially on a high-latency connection.
        void Promise.all([
          fetchCrosspostPreview(saved.id, PREVIEW_MARKETPLACES).catch(() => []),
          fetchCrosspostJobs(saved.id).catch(() => []),
        ]).then(([preview, jobs]) => {
          setCrosspostPreview(preview || []);
          setCrosspostJobs(jobs || []);
        });
      }
      // Saving a draft must not wait for a full dashboard/catalog refresh.
      // The saved response already contains the authoritative listing state;
      // dashboard metrics refresh on the next navigation/poll instead.
      if (isNew) {
        await router.replace(`/listings/${saved.id}?return_to=${encodeURIComponent(returnToListings)}`);
      }
      toast.success(nextStatus === "ready" ? "Listing saved and moved to ready." : "Listing saved.");
      return saved;
    } catch (error) {
      toast.error(error.message);
      return null;
    } finally {
      setSaving(false);
    }
  };

  const autosaveListingField = async (field, value) => {
    setForm((current) => ({ ...current, [field]: value }));
    if (isNew || !listing?.id) return;
    try {
      const saved = await updateListing(listing.id, { [field]: value || null });
      setListing(saved);
      setForm((current) => ({ ...current, [field]: saved?.[field] ?? value }));
      toast.success(`${field === "condition" ? "Condition" : "Category"} saved.`);
    } catch (error) {
      toast.error(error.message || `Could not save ${field}.`);
    }
  };

  const refreshAllDestinationPreflights = async () => {
    if (!listing?.id) return;
    const results = await Promise.all(PREVIEW_MARKETPLACES.map(async (marketplace) => {
      try {
        return [marketplace, await fetchMarketplacePreflight(listing.id, marketplace)];
      } catch (error) {
        return [marketplace, {
          marketplace,
          status: "failed",
          execution_status: "not checked",
          preparation_status: "unavailable",
          preparation_blockers: [{ code: "PREFLIGHT_UNAVAILABLE", message: error?.message || "Destination check could not be completed." }],
          blockers: [],
          connection_blockers: [],
        }];
      }
    }));
    setListing((current) => {
      if (!current) return current;
      const marketplaceData = current.marketplace_data || {};
      const previous = marketplaceData.marketplace_preflight || {};
      return {
        ...current,
        marketplace_data: {
          ...marketplaceData,
          marketplace_preflight: { ...previous, by_marketplace: { ...(previous.by_marketplace || {}), ...Object.fromEntries(results) } },
        },
      };
    });
    const unavailable = results.filter(([, result]) => result.preparation_status === "unavailable").length;
    toast.success(unavailable ? `Destination checks completed; ${unavailable} could not be checked.` : "Destination checks refreshed for all marketplaces.");
  };

  const runGenerate = async () => {
    const currentListing = listing || (await saveListing("draft"));
    if (!currentListing?.id) return;
    setGenerating(true);
    try {
      await generateListing(currentListing.id);
      const refreshed = await fetchListing(currentListing.id);
      setListing(refreshed);
      setForm(normalizeListingForm(refreshed));
      await reloadDashboard();
      toast.success("AI draft refreshed.");
    } catch (error) {
      toast.error(error.message);
    } finally {
      setGenerating(false);
    }
  };

  const requestRevision = async () => {
    if (!listing?.id || !revisionFields.length) {
      toast.error("Select at least one field to correct.");
      return;
    }
    try {
      const revised = await requestListingRevision(listing.id, revisionFields, revisionNote, revisionPriority);
      setListing(revised);
      setForm(normalizeListingForm(revised));
      setRevisionFields([]);
      setRevisionNote("");
      setRevisionPriority(0);
      await reloadDashboard();
      toast.success("Returned to Drafts with your correction request.");
    } catch (error) {
      toast.error(error.message || "Revision request failed.");
    }
  };

  const queueCrosspost = async () => {
    const currentListing = listing || (await saveListing("draft"));
    if (!currentListing?.id) return;
    setQueueingCrosspost(true);
    try {
      const targets = (form.marketplace_data.targets || []).filter(Boolean);
      const job = await queueCrosspostJob(currentListing.id, {
        marketplaces: targets,
        requested_mode: form.marketplace_data.crosspost_mode || "approval_required",
      });
      const [preview, jobs] = await Promise.all([
        fetchCrosspostPreview(currentListing.id, PREVIEW_MARKETPLACES).catch(() => []),
        fetchCrosspostJobs(currentListing.id).catch(() => []),
      ]);
      setCrosspostPreview(preview || []);
      setCrosspostJobs(jobs || []);
      toast.success(`Cross-post job #${job.id} queued.`);
    } catch (error) {
      toast.error(error.message);
    } finally {
      setQueueingCrosspost(false);
    }
  };

  const saveAndPublishChanges = async () => {
    if (!listing?.id) return;
    setSaving(true);
    setUpdateFeedback({ status: "queued", message: "Saved locally. Updating active marketplaces…" });
    try {
      const activeTargets = Array.from(new Set([
        ...(Array.isArray(listing?.marketplace_statuses) ? listing.marketplace_statuses.filter((row) => row?.marketplace_listing_id && ['published', 'updated', 'active'].includes(String(row.status || '').toLowerCase())).map((row) => String(row.marketplace || '').toLowerCase()) : []),
        ...(listing?.ebay_listing_id ? ['ebay'] : []),
      ]));
      const target_marketplaces = saveTargetMarketplaces === null ? activeTargets : saveTargetMarketplaces;
      const result = await savePublishListingChanges(listing.id, { ...buildPayload(listing.status), target_marketplaces });
      const saved = result?.listing || result;
      setListing(saved);
      setForm(normalizeListingForm(saved));
      const jobs = result?.jobs || [];
      setUpdateFeedback({ status: jobs.length ? "running" : "saved", message: jobs.length ? `Saved locally. Updating ${jobs.length} active marketplace${jobs.length === 1 ? "" : "s"}…` : "Saved locally. No active marketplace update was required." });
      toast.success(jobs.length ? `${jobs.length} marketplace update job(s) queued.` : "Changes saved locally.");
      // Keep the editor honest about remote state. Do not leave the operator
      // guessing after a successful request: follow the durable jobs to a
      // terminal result and surface the exact destination error if one fails.
      if (jobs.length) {
        let latestJobs = [];
        for (let attempt = 0; attempt < 12; attempt += 1) {
          latestJobs = await fetchCrosspostJobs(listing.id).catch(() => []);
          const pending = latestJobs.some((job) => ["queued", "running", "pending"].includes(String(job.status || "").toLowerCase()));
          if (!pending) break;
          await new Promise((resolve) => setTimeout(resolve, 1200));
        }
        setCrosspostJobs(latestJobs || []);
        const failed = (latestJobs || []).filter((job) => ["failed", "error"].includes(String(job.status || "").toLowerCase()));
        if (failed.length) {
          const reason = failed.map((job) => `${(job.target_marketplaces || []).join(", ") || "Marketplace"}: ${job.last_error || "update failed"}`).join("; ");
          setUpdateFeedback({ status: "failed", message: `Saved locally, but remote update failed — ${reason}` });
          toast.error(`Remote update failed: ${reason}`);
        } else {
          setUpdateFeedback({ status: "updated", message: "Marketplace updates confirmed ✓" });
          toast.success("Marketplace updates confirmed.");
        }
        const refreshed = await fetchListing(listing.id).catch(() => null);
        if (refreshed) {
          setListing(refreshed);
          setForm(normalizeListingForm(refreshed));
        }
      }
      await reloadDashboard();
    } catch (error) {
      toast.error(error.message || "Could not publish changes.");
    } finally {
      setSaving(false);
    }
  };

  const importMarketplacePayload = async () => {
    setImporting(true);
    try {
      const payload = parseJsonField(importForm.payload_json, "Import payload");
      const job = await createMarketplaceImportJob({
        source_marketplace: importForm.source_marketplace,
        source_listing_reference: importForm.source_listing_reference.trim() || undefined,
        import_mode: importForm.import_mode,
        payload,
      });
      await reloadDashboard();
      if (job.created_listing_id) {
        toast.success(`Imported into draft listing #${job.created_listing_id}.`);
        await router.push(`/listings/${job.created_listing_id}`);
        return;
      }
      toast.success(`Import job #${job.id} queued.`);
    } catch (error) {
      toast.error(error.message);
    } finally {
      setImporting(false);
    }
  };

  const runFitmentResearch = async () => {
    if (!listing?.id) return;
    setResearchingFitment(true);
    try {
      const result = await researchListingFitment(listing.id);
      setFitmentResearch(result);
      const researchOnly = result?.status === "compatibility_records_found";
      toast[researchOnly ? "success" : "error"](researchOnly ? "Sourced eBay Motors compatibility evidence is available for review." : result?.blocker || "No verified fitment evidence was returned.");
    } catch (error) {
      toast.error(error.message || "Fitment research could not be completed.");
    } finally {
      setResearchingFitment(false);
    }
  };

  if (loading) {
    return (
      <AppShell
        active="/listings"
        title="Listing Workspace"
        autonomousConfig={autonomousConfig}
        onToggleAutonomous={async () => {
          await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
          await reloadDashboard();
        }}
      >
        <div className="rounded-[16px] border border-[#e5e7eb] bg-white p-6 text-sm text-[#667085]">Loading listing workspace...</div>
      </AppShell>
    );
  }

  const marketplaceNames = [
    ['ebay', 'eBay'], ['facebook', 'Facebook'], ['mercari', 'Mercari'],
    ['poshmark', 'Poshmark'], ['vinted', 'Vinted'], ['etsy', 'Etsy'], ['offerup', 'OfferUp'],
  ];
  const durableMarketplaceRows = Array.isArray(listing?.marketplace_statuses) ? listing.marketplace_statuses : [];
  const activeMarketplaceTargets = new Set([
    ...durableMarketplaceRows.filter((row) => row?.marketplace_listing_id && ['published', 'updated', 'active'].includes(String(row.status || '').toLowerCase())).map((row) => String(row.marketplace || '').toLowerCase()),
    ...(listing?.ebay_listing_id ? ['ebay'] : []),
  ]);
  const saveTargets = saveTargetMarketplaces === null ? Array.from(activeMarketplaceTargets) : saveTargetMarketplaces;
  const fitmentEvidence = fitmentResearch || listing?.marketplace_data?.vehicle_fitment_research || null;
  const isAutomotiveCandidate = /automotive|vehicle|4wd|car part|truck part|motorcycle|fitment|actuator|o-ring|valve stem/i.test(`${form.title || ""} ${form.category_suggestion || ""} ${form.item_specifics?.Type || ""}`);

  return (
    <AppShell
      active="/listings"
      title="Listing Workspace"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reloadDashboard();
      }}
      contentWidth="default"
      contentClassName="listing-workspace-content"
    >
      <PageHeader
        compact
        className="listing-editor-page-header"
        title={title}
        description="Review product details and destination readiness. Marketplace access only affects that destination."
        actions={
          <div className="listing-editor-actions flex flex-wrap items-center gap-2">
            <Button type="button" variant="tertiary" onClick={() => router.push(returnToListings)}>← Listings</Button>
            <Button type="button" variant="outline" onClick={() => saveListing()} disabled={saving}>
              <Save size={16} /> {saving ? "Saving…" : "Save changes"}
            </Button>
            <Button type="button" variant="secondary" onClick={() => saveListing("ready")} disabled={saving}>
              {saving ? "Saving…" : "Approve / Mark ready"}
            </Button>
            <Button type="button" onClick={queueCrosspost} disabled={queueingCrosspost}>
              {queueingCrosspost ? "Queueing…" : "Publish selected"}
            </Button>
            <details className="listing-editor-more relative">
              <summary className="flex h-10 cursor-pointer list-none items-center rounded-lg border border-[#d0d5dd] bg-white px-3 text-sm font-semibold text-[#344054] hover:bg-[#f8fafc] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#2563eb]">More actions <span aria-hidden="true" className="ml-2">⌄</span></summary>
              <div className="absolute right-0 z-20 mt-2 w-[min(24rem,90vw)] rounded-xl border border-[#d0d5dd] bg-white p-3 shadow-xl">
                <div className="flex flex-wrap gap-2">
                  <Button type="button" size="sm" variant="outline" onClick={runGenerate} disabled={generating}><Bot size={15} />{generating ? "Generating…" : "Generate listing draft"}</Button>
                  <Button type="button" size="sm" variant="outline" onClick={() => saveListing("draft")} disabled={saving}>Save as draft</Button>
                  {!isNew ? <Button type="button" size="sm" variant="outline" onClick={saveAndPublishChanges} disabled={saving}><Sparkles size={15} />Save & update live listings</Button> : null}
                </div>
                {!isNew && activeMarketplaceTargets.size ? <div className="mt-3 border-t border-[#eaecf0] pt-3">
                  <p className="text-xs font-semibold text-[#344054]">Send edits to already-live destinations</p>
                  <div className="mt-2 flex flex-wrap gap-x-3 gap-y-2">
                    {marketplaceNames.filter(([market]) => activeMarketplaceTargets.has(market)).map(([market, label]) => <label key={market} className="inline-flex min-h-8 items-center gap-2 text-sm text-[#344054]"><input type="checkbox" checked={saveTargets.includes(market)} onChange={() => setSaveTargetMarketplaces((current) => { const base = current === null ? Array.from(activeMarketplaceTargets) : current; return base.includes(market) ? base.filter((value) => value !== market) : [...base, market]; })} className="h-4 w-4 accent-[#2563eb]" />{label}</label>)}
                  </div>
                </div> : null}
              </div>
            </details>
          </div>
        }
      />

      <section className="listing-editor-identity mb-5 grid gap-5 rounded-[18px] border border-[#d9e2ef] bg-white p-4 lg:grid-cols-[minmax(280px,0.72fr)_minmax(0,1.28fr)] lg:items-center">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-[#667085]">Product photos</p>
          <div className="listing-editor-main-photo mt-2 overflow-hidden rounded-[12px] border border-[#dbe4f0] bg-[#f5f7fa]">
            {editorImageUrls[selectedImageIndex] ? <button type="button" className="h-full w-full cursor-zoom-in" onClick={() => setPhotoLightboxOpen(true)} aria-label="Open full-size product photo"><img src={editorImageUrls[selectedImageIndex]} alt={`Product photo ${selectedImageIndex + 1}`} className="h-full w-full object-contain" /></button> : <div className="flex h-full items-center justify-center p-6 text-center text-sm font-semibold text-[#667085]">Add product photos before publishing</div>}
          </div>
          {editorImageUrls.length > 1 ? <div className="mt-3 flex gap-2 overflow-x-auto pb-1" aria-label="Product photo thumbnails">{editorImageUrls.map((url, index) => <Button key={`${url}-${index}`} type="button" variant="tertiary" size="icon-sm" onClick={() => setSelectedImageIndex(index)} className={`shrink-0 rounded-lg border-2 !p-0.5 transition ${selectedImageIndex === index ? "border-[#2563eb] ring-2 ring-[#2563eb]/20" : "border-[#dbe4f0] hover:border-[#94a3b8]"}`} aria-label={`View product photo ${index + 1}`}><img src={url} alt={`Product photo ${index + 1}`} style={{ width: 56, height: 56, minWidth: 56, objectFit: "cover" }} className="rounded-md" /></Button>)}</div> : null}
          {editorImageUrls.length ? <div className="mt-2 flex flex-wrap items-center gap-2"><span className="mr-auto text-xs text-[#667085]">{editorImageUrls.length} product photo{editorImageUrls.length === 1 ? "" : "s"} · {selectedImageIndex === 0 ? "first image is primary" : `photo ${selectedImageIndex + 1} selected`}</span><Button type="button" size="sm" variant="outline" disabled={!selectedImageIndex} onClick={() => { const urls = form.image_urls.split("\n").map((value) => value.trim()).filter(Boolean); const [chosen] = urls.splice(selectedImageIndex, 1); setForm((current) => ({ ...current, image_urls: [chosen, ...urls].join("\n") })); setSelectedImageIndex(0); }}>Set primary</Button><Button type="button" size="sm" variant="outline" disabled={selectedImageIndex === 0} onClick={() => { const urls = form.image_urls.split("\n").map((value) => value.trim()).filter(Boolean); [urls[selectedImageIndex - 1], urls[selectedImageIndex]] = [urls[selectedImageIndex], urls[selectedImageIndex - 1]]; setForm((current) => ({ ...current, image_urls: urls.join("\n") })); setSelectedImageIndex(selectedImageIndex - 1); }}>Move earlier</Button><Button type="button" size="sm" variant="outline" disabled={selectedImageIndex >= editorImageUrls.length - 1} onClick={() => { const urls = form.image_urls.split("\n").map((value) => value.trim()).filter(Boolean); [urls[selectedImageIndex + 1], urls[selectedImageIndex]] = [urls[selectedImageIndex], urls[selectedImageIndex + 1]]; setForm((current) => ({ ...current, image_urls: urls.join("\n") })); setSelectedImageIndex(selectedImageIndex + 1); }}>Move later</Button><Button type="button" size="sm" variant="outline" onClick={() => { const urls = form.image_urls.split("\n").map((value) => value.trim()).filter(Boolean).filter((_, index) => index !== selectedImageIndex); setForm((current) => ({ ...current, image_urls: urls.join("\n") })); setSelectedImageIndex(Math.max(0, Math.min(selectedImageIndex, urls.length - 1))); }}>Remove photo</Button></div> : null}
        </div>
        <div className="min-w-0">
          <div className="mt-3 flex flex-wrap gap-2"><StatusPill status="info" label={String(listing?.source_type || listing?.source_metadata?.source_type || "manual").replaceAll("_", " ")} />{listing?.canonical_readiness?.queue ? <StatusPill status={listing.canonical_readiness.queue === "NEEDS_REVIEW" ? "warning" : listing.canonical_readiness.queue === "PUBLISHED" ? "success" : "info"} label={String(listing.canonical_readiness.queue).replaceAll("_", " ")} /> : <StatusPill status={listing?.status || "draft"} label={listing?.status || "draft"} />}</div>
          <div className="mt-4 grid gap-3 sm:grid-cols-3"><div><p className="text-xs font-bold uppercase tracking-wide text-[#667085]">Brand</p><p className="mt-1 font-semibold text-[#101828]">{listing?.brand || listing?.item_specifics?.Brand || listing?.source_metadata?.amazon_product_facts?.brand || "Pending"}</p></div><div><p className="text-xs font-bold uppercase tracking-wide text-[#667085]">Condition</p><p className="mt-1 font-semibold text-[#101828]">{form.condition || "Pending"}</p></div><div><p className="text-xs font-bold uppercase tracking-wide text-[#667085]">Price</p><p className="mt-1 font-semibold text-[#101828]">{form.listing_price ? `$${Number(form.listing_price).toFixed(2)}` : "Pending"}</p></div></div>
        </div>
      </section>

      {photoLightboxOpen && editorImageUrls[selectedImageIndex] ? <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/90 p-4" role="dialog" aria-modal="true" aria-label="Full-size product photo" onClick={() => setPhotoLightboxOpen(false)}><button type="button" className="absolute right-4 top-4 rounded-lg bg-white px-4 py-2 text-sm font-semibold text-slate-900" onClick={() => setPhotoLightboxOpen(false)}>Close</button><img src={editorImageUrls[selectedImageIndex]} alt={`Full-size product photo ${selectedImageIndex + 1}`} className="max-h-[90vh] max-w-[94vw] object-contain" onClick={(event) => event.stopPropagation()} /></div> : null}

      {listing?.canonical_readiness ? (
        <section className="mb-5 rounded-[16px] border border-[#d9e2ef] bg-white p-4" aria-label="Canonical listing readiness">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-[#101828]">PosterPro readiness</p>
              <p className="mt-1 text-sm text-[#667085]">One shared result powers the queue, review, and publish decisions.</p>
            </div>
            <StatusPill
              status={listing.canonical_readiness.queue === 'PUBLISHED' ? 'success' : listing.canonical_readiness.queue === 'NEEDS_ATTENTION' ? 'danger' : listing.canonical_readiness.queue === 'PROCESSING' ? 'warning' : 'info'}
              label={String(listing.canonical_readiness.queue || 'PROCESSING').replaceAll('_', ' ')}
            />
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-4">
            {[
              ['Machine work', listing.canonical_readiness.processing_complete ? 'Complete' : 'In progress'],
              ['Review', listing.canonical_readiness.review_required ? 'Required' : 'Not required'],
              ['Publish', listing.canonical_readiness.publishable ? 'Ready' : 'Blocked'],
              ['Remote state', listing.canonical_readiness.remote_state || 'LOCAL'],
            ].map(([label, value]) => <div key={label} className="rounded-[10px] border border-[#eaecf0] bg-[#fcfcfd] p-3"><p className="text-xs font-semibold text-[#667085]">{label}</p><p className="mt-1 text-sm font-semibold text-[#101828]">{value}</p></div>)}
          </div>
          {listing.canonical_readiness.shipping_setup_required ? (
            <div className="mt-3 rounded-[10px] border border-amber-300 bg-amber-50 p-3" role="status">
              <p className="text-sm font-semibold text-amber-950">Shipping setup required</p>
              <p className="mt-1 text-sm text-amber-900">Add verified packed weight and dimensions before publishing. PosterPro will not estimate parcel measurements; publication remains blocked until shipping is configured.</p>
            </div>
          ) : null}
          {listing.canonical_readiness.description_quality ? (
            <div className="mt-3 rounded-[10px] border border-[#eaecf0] bg-[#f8fafc] p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Description quality</p>
                <span className="text-sm font-semibold text-[#101828]">{String(listing.canonical_readiness.description_quality.quality || 'unknown').replaceAll('_', ' ')}</span>
              </div>
              <p className="mt-1 text-xs text-[#667085]">
                {listing.canonical_readiness.description_quality.word_count || 0} words · {listing.canonical_readiness.description_quality.source_facts_covered || 0}/{listing.canonical_readiness.description_quality.source_fact_count || 0} verified source facts represented
              </p>
            </div>
          ) : null}
          {listing?.source_type === 'amazon_vine' && listing?.source_metadata?.amazon_enrichment?.stages ? (() => {
            const stages = listing.source_metadata.amazon_enrichment.stages;
            const text = stages.text_extraction || {};
            const facts = stages.fact_normalization || {};
            const images = stages.image_retrieval || {};
            const description = stages.description_generation || {};
            return <div className="mt-3 rounded-[10px] border border-[#eaecf0] bg-[#f8fafc] p-3" aria-label="Amazon Vine enrichment status">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Amazon Vine enrichment</p>
              <p className="mt-1 text-xs text-[#667085]">Images, source text, normalized facts, and sales copy are tracked separately.</p>
              <div className="mt-2 grid gap-2 sm:grid-cols-4">
                {[
                  ['Images', images.state === 'complete' ? `${images.image_count || 0} imported` : 'Unavailable', images.state === 'complete'],
                  ['Amazon text', text.state === 'complete' ? `${text.fact_count || 0} facts extracted` : 'Unavailable', text.state === 'complete'],
                  ['Normalized facts', facts.state === 'complete' ? `${facts.verified_fact_count || 0} verified` : 'Waiting', facts.state === 'complete'],
                  ['Listing copy', description.state === 'complete' ? `${description.word_count || 0} words` : 'Blocked', description.state === 'complete'],
                ].map(([label, value, good]) => <div key={label} className="rounded-lg border border-[#eaecf0] bg-white p-2"><p className="text-xs font-semibold text-[#667085]">{label}</p><p className={`mt-0.5 text-xs font-semibold ${good ? 'text-emerald-700' : 'text-amber-700'}`}>{value}</p></div>)}
              </div>
              {text.state !== 'complete' && text.reason ? <p className="mt-2 text-xs text-amber-800">{text.reason}</p> : null}
            </div>;
          })() : null}
          {listing.canonical_readiness.blocking_reasons?.length ? (() => {
            const isProcessing = listing.canonical_readiness.queue === 'PROCESSING';
            return <div className={`mt-3 rounded-[10px] border p-3 ${isProcessing ? 'border-blue-200 bg-blue-50' : 'border-amber-200 bg-amber-50'}`}>
              <p className={`text-xs font-semibold uppercase tracking-[0.08em] ${isProcessing ? 'text-blue-900' : 'text-amber-900'}`}>{isProcessing ? 'Current checks' : 'Action needed'}</p>
              <ul className={`mt-1 list-disc pl-5 text-sm ${isProcessing ? 'text-blue-900' : 'text-amber-900'}`}>{listing.canonical_readiness.blocking_reasons.slice(0, 5).map((reason) => <li key={reason}>{reason}</li>)}</ul>
            </div>;
          })() : null}
        </section>
      ) : null}

      <details className="listing-compact-disclosure mb-4">
        <summary>Marketplace status and remote listing links</summary>
      <SectionPanel>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {marketplaceNames.map(([marketplace, label]) => {
            const row = durableMarketplaceRows.find((item) => String(item.marketplace || '').toLowerCase() === marketplace);
            const rawStatus = String(row?.status || (marketplace === 'ebay' ? listing?.ebay_publish_status : '') || '').toUpperCase();
            const displayStatus = rawStatus === 'PUBLISHED' || rawStatus === 'UPDATED' ? 'LIVE'
              : rawStatus === 'PENDING' ? 'QUEUED'
                : rawStatus === 'FAILED' ? 'FAILED'
                  : rawStatus || (activeMarketplaceTargets.has(marketplace) ? 'TARGETED' : 'NOT LISTED');
            const response = row?.raw_response && typeof row.raw_response === 'object' ? row.raw_response : {};
            const externalUrl = response.external_url || response.url || response.listing_urls?.[0] || response.bridge_completion?.result?.listing_urls?.[0];
            return (
              <article key={marketplace} className="rounded-lg border border-slate-200 bg-white p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-slate-900">{label}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${displayStatus === 'LIVE' ? 'bg-emerald-100 text-emerald-900' : displayStatus === 'FAILED' ? 'bg-red-100 text-red-900' : displayStatus === 'QUEUED' || displayStatus === 'TARGETED' ? 'bg-amber-100 text-amber-900' : 'bg-slate-100 text-slate-700'}`}>{displayStatus}</span>
                </div>
                {row?.marketplace_listing_id && <p className="mt-1 break-all text-[11px] text-slate-600">External ID: {row.marketplace_listing_id}</p>}
                {externalUrl && <a className="mt-1 inline-block text-xs font-medium text-blue-700 underline" href={externalUrl} target="_blank" rel="noreferrer">Open marketplace listing</a>}
              </article>
            );
          })}
        </div>
      </SectionPanel>
      </details>

      {(listing?.readiness_summary?.blockers?.length || listing?.marketplace_preflight_summary) ? (
        <details className="listing-compact-disclosure mb-4">
          <summary>Listing and destination issues</summary>
          <section className="mt-2 rounded-[12px] border border-amber-200 bg-amber-50 p-3">
          <Button type="button" size="sm" variant="outline" className="mt-2" onClick={refreshAllDestinationPreflights}>Refresh all destination checks</Button>
          {listing?.readiness_summary?.blockers?.length ? <ul className="mt-2 list-disc pl-5 text-sm text-amber-900">{listing.readiness_summary.blockers.map((reason) => <li key={reason}>{reason}</li>)}</ul> : null}
          {Object.entries(listing?.marketplace_preflight_summary?.by_marketplace || {}).map(([market, summary]) => ((summary?.blockers || []).length ? <div key={market} className="mt-3"><p className="text-xs font-bold uppercase tracking-wide text-amber-800">{market} preflight</p><ul className="mt-1 list-disc pl-5 text-sm text-amber-900">{summary.blockers.map((issue, index) => <li key={`${market}-${index}`}>{issue.message || issue.code || String(issue)}</li>)}</ul></div> : null))}
          {!listing?.readiness_summary?.blockers?.length && !Object.values(listing?.marketplace_preflight_summary?.by_marketplace || {}).some((summary) => (summary?.blockers || []).length) ? <p className="mt-2 text-sm text-amber-900">Preflight has not produced a current blocker report. Run eBay preflight before retrying publication.</p> : null}
          </section>
        </details>
      ) : null}

      <details className="listing-editor-correction mb-5 rounded-[16px] border border-[#d9e2ef] bg-white">
        <summary className="cursor-pointer px-5 py-3 text-sm font-semibold text-[#344054]">Need a correction? Send this listing back to Drafts</summary>
        <div className="border-t border-[#e5e7eb] p-5">
          {listing?.source_metadata?.correction_status ? <div className="mb-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-semibold text-blue-800">{String(listing.source_metadata.correction_status).replaceAll('_', ' ')}{listing.source_metadata.correction_job_id ? <span className="ml-2 text-xs font-normal">Job #{listing.source_metadata.correction_job_id}</span> : null}</div> : null}
          <p className="mb-3 text-sm text-[#667085]">Choose only the fields that need work. PosterPro records the request for the next AI revision.</p>
          <div className="flex flex-wrap gap-3">
            {['title', 'description', 'category', 'price', 'condition', 'photos', 'item specifics', 'shipping'].map((field) => <Checkbox key={field} checked={revisionFields.includes(field)} onChange={() => setRevisionFields((current) => current.includes(field) ? current.filter((item) => item !== field) : [...current, field])} label={`Fix ${field}`} />)}
          </div>
          <Textarea value={revisionNote} onChange={(event) => setRevisionNote(event.target.value)} className="mt-3 min-h-20" placeholder="Optional: describe what is wrong or point the AI to a label/photo." />
          <label className="mt-3 block text-sm font-semibold">Priority <Input type="number" min="0" step="1" value={revisionPriority} onChange={(event) => setRevisionPriority(Math.max(0, Number(event.target.value) || 0))} className="ml-2 inline-flex w-20" /></label><p className="mt-1 text-xs text-[#667085]">0 = immediate / highest priority; newer priority-0 requests run first.</p>
          <div className="mt-3"><Button variant="outline" onClick={requestRevision}>Send to Drafts &amp; request AI correction</Button></div>
        </div>
      </details>

      <div className="listing-editor-main-grid grid min-w-0 gap-4">
        <div className="space-y-5 xl:contents">
          <SectionPanel title="Product identity" description="The shared product facts used to prepare each marketplace version.">
            {!isNew && isAutomotiveCandidate ? <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50/60 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><h4 className="text-sm font-semibold text-[#101828]">Automotive fitment research</h4><p className="text-xs text-[#667085]">Matches exact Brand+MPN in eBay Catalog, then reads eBay Motors compatibility. No vehicle fitment is inferred from a title or visual similarity.</p></div>
                <Button type="button" size="sm" variant="outline" onClick={runFitmentResearch} disabled={researchingFitment}>{researchingFitment ? "Checking catalog…" : "Research fitment"}</Button>
              </div>
              {fitmentEvidence ? <div className="mt-3 space-y-2 border-t border-amber-200 pt-3 text-sm">
                <p className="font-medium text-[#344054]">Research status: {String(fitmentEvidence.status || "unknown").replaceAll("_", " ")}</p>
                {fitmentEvidence.product ? <p className="text-[#475467]">Exact catalog product: {fitmentEvidence.product.title} · Brand {fitmentEvidence.product.brand} · MPN {(fitmentEvidence.product.mpn || []).join(", ")} · ePID {fitmentEvidence.product.epid}{fitmentEvidence.product.product_url ? <> · <a className="underline" href={fitmentEvidence.product.product_url} target="_blank" rel="noreferrer">catalog record</a></> : null}</p> : null}
                {fitmentEvidence.compatibilities?.length ? <div><p className="font-semibold text-[#344054]">eBay Motors catalog applications ({fitmentEvidence.compatibilities.length})</p><ul className="mt-1 list-disc pl-5 text-[#475467]">{fitmentEvidence.compatibilities.slice(0, 30).map((row, index) => <li key={`${fitmentEvidence.product?.epid || "fitment"}-${index}`}>{(row.properties || []).map((property) => `${property.name}: ${property.value}`).join(" · ")}</li>)}</ul></div> : null}
                {fitmentEvidence.manufacturer_sources?.length ? <div className="rounded-lg bg-white p-3"><p className="font-semibold text-[#344054]">Manufacturer-reported applications (research evidence, not listing claims)</p>{fitmentEvidence.manufacturer_sources.map((source, sourceIndex) => <div key={`${source.source_url || source.source_name}-${sourceIndex}`} className="mt-2 text-[#475467]"><p><a className="underline" href={source.source_url} target="_blank" rel="noreferrer">{source.source_name || "Manufacturer source"}: {source.source_title || source.source_url}</a> · captured {source.captured_at || "date unavailable"}</p>{source.match ? <p className="text-xs">Identity correlation: {source.match.brand || "brand unavailable"} / {source.match.mpn || "MPN unavailable"}{source.match.matched_catalog_epid ? ` · ePID ${source.match.matched_catalog_epid}` : ""}</p> : null}{source.applications?.length ? <ul className="mt-1 list-disc pl-5">{source.applications.map((application, applicationIndex) => <li key={`${application.make}-${application.model}-${applicationIndex}`}>{application.make} {application.model}{application.from_year ? ` ${application.from_year}` : ""}{application.to_year && application.to_year !== application.from_year ? `–${application.to_year}` : ""}{application.notes ? ` · ${application.notes}` : ""}</li>)}</ul> : null}<p className="mt-1 text-xs text-amber-900">{source.review_note || "Manufacturer-reported application only; verify the exact physical unit, year/trim/engine and exclusions before using this as customer-facing fitment."}</p></div>)}</div> : null}
                {fitmentEvidence.blocker ? <p role="status" className="text-amber-900">{fitmentEvidence.blocker}</p> : null}
                {fitmentEvidence.candidates?.length && !fitmentEvidence.product ? <p className="text-xs text-[#667085]">Unconfirmed catalog candidates: {fitmentEvidence.candidates.slice(0, 5).map((candidate) => `${candidate.title} (${candidate.brand || "brand unknown"}, ${Array.isArray(candidate.mpn) ? candidate.mpn.join("/") : candidate.mpn || "MPN unknown"})`).join("; ")}</p> : null}
                <p className="text-xs text-[#667085]">Provider: eBay Catalog and Motors Metadata · researched {fitmentEvidence.researched_at || "time unavailable"} · evidence only; listing content and readiness are unchanged.</p>
              </div> : null}
            </div> : null}
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2 md:col-span-2">
                <label className="text-sm font-medium text-[#101828]">Title</label>
                <Input value={form.title} onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))} placeholder="Vintage camera kit with lens and strap" />
              </div>
              <div className="space-y-2 md:col-span-2">
                <div className="flex flex-wrap items-baseline justify-between gap-2"><label className="text-sm font-semibold text-[#101828]">Master product description</label><span className="text-xs text-[#667085]">Canonical copy · destination versions may inherit or override this</span></div>
                <Textarea
                  value={form.description}
                  onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
                  className="listing-master-description min-h-92 max-w-5xl"
                  placeholder="Write complete, accurate, buyer-focused product information."
                />
              </div>
              {!isNew ? <div className="md:col-span-2 rounded-xl border border-blue-100 bg-blue-50/50 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div><h4 className="text-sm font-semibold text-[#101828]">Marketplace version</h4><p className="text-xs text-[#667085]">This destination can inherit the master copy or use its own edited version.</p><p className="mt-1 text-xs font-semibold text-blue-800">{(listing.marketplace_descriptions || {})[variantMarketplace] ? 'Marketplace override' : 'Inherits from Master'}</p></div>
                  <Select className="h-9 rounded-xl border-blue-200 px-2" value={variantMarketplace} onChange={(event) => setVariantMarketplace(event.target.value)} aria-label="Marketplace description destination">
                    {Object.keys(CHANNEL_LABELS).filter((key) => ['ebay','facebook','mercari','poshmark','vinted','etsy','offerup'].includes(key)).map((key) => <option key={key} value={key}>{CHANNEL_LABELS[key]}</option>)}
                  </Select>
                </div>
                <Textarea value={variantDraft} onChange={(event) => setVariantDraft(event.target.value)} className="mt-3 min-h-56 border-blue-200" maxLength={variantMarketplace === 'mercari' ? 1000 : undefined} />
                {(listing.description_regeneration_requests || []).some((request) => request.marketplace === variantMarketplace && request.status === 'QUEUED') ? <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900">A fresh {CHANNEL_LABELS[variantMarketplace] || variantMarketplace} description has been queued. Your current copy is preserved until the generated revision is ready.</p> : null}
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-[#667085]">{variantMarketplace === 'mercari' ? `${variantDraft.length}/1000 characters` : `${variantDraft.length} characters`} · saved as operator-edited</span><div className="flex gap-2">{variantMarketplace === 'mercari' ? <Button type="button" size="sm" variant="outline" onClick={copyMercariManualPacket}>Copy manual listing packet</Button> : null}<Button type="button" size="sm" onClick={saveMarketplaceVariant} disabled={savingVariant}>{savingVariant ? 'Saving…' : 'Save destination copy'}</Button></div></div>
              </div> : null}
              <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-3"><label className="text-sm font-medium text-[#101828]">eBay category</label><Button type="button" size="sm" variant="outline" onClick={async () => { try { const result = await resolveListingCategory(listing.id); if (result?.resolved) { toast.success(`Category resolved: ${result.category_path}`); const saved = await fetchListing(listing.id); setListing(saved); setForm((current) => ({ ...current, category_id: String(saved?.category_id || result.category_id), category_suggestion: saved?.category_suggestion || result.category_path })); } else toast.error(result?.reason || "No confident taxonomy match yet."); } catch (error) { const rateLimited = /429|too many requests|request limit/i.test(error.message || ""); toast.error(rateLimited ? "eBay is temporarily limiting taxonomy requests. PosterPro will reuse saved category data and retry later." : "Could not resolve an eBay category. Try again in a moment."); } }}>Suggest best match</Button></div>
                <div className="rounded-xl border border-[#d9e2ef] bg-[#f8fbff] px-3 py-2 text-sm text-[#344054]">
                  <p className="font-semibold">{listing?.catalog_category?.full_path || listing?.source_metadata?.category_path || form.category_suggestion || "Category not resolved"}</p>
                  <p className="mt-1 text-xs text-[#667085]">{form.category_id ? `${listing?.catalog_category?.status === "VERIFIED" ? "Verified" : "Revalidation required"} ${listing?.catalog_category?.marketplace || listing?.source_metadata?.taxonomy_marketplace || "EBAY_US"} · tree ${listing?.catalog_category?.taxonomy_tree_id || listing?.source_metadata?.taxonomy_tree_id || "0"} · ID ${form.category_id}` : "eBay category has not been verified yet."}</p>
                </div>
                <div className="flex gap-2"><Input value={categoryQuery} onChange={(event) => setCategoryQuery(event.target.value)} placeholder="Search categories, e.g. RV covers" onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); runCategorySearch(); } }} /><Button type="button" variant="outline" onClick={runCategorySearch} disabled={categorySearching}>{categorySearching ? "Searching…" : "Search"}</Button></div>
                {categorySearchResults.length ? <div className="space-y-2">{categorySearchResults.slice(0, 8).map((result) => <div key={`${result.marketplace}-${result.category_id}`} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[#eaecf0] bg-white px-3 py-2"><div className="min-w-0"><p className="text-sm font-semibold text-[#344054]">{result.marketplace === "EBAY_MOTORS_US" ? "eBay Motors" : "eBay"} · {result.category_name}</p><p className="text-xs text-[#667085]">{result.breadcrumb || result.category_path || "Verified local taxonomy"} · tree {result.category_tree_id || result.taxonomy_tree_id || "0"} · ID {result.category_id} · {result.leaf ? "Leaf" : "Browse only"}</p></div><Button type="button" size="sm" variant="outline" onClick={() => selectSearchCategory(result)} disabled={result.publishable === false || result.leaf_verified === false || result.leaf === false}>Select leaf</Button></div>)}</div> : null}
                {categoryChoices.length ? <details className="rounded-lg border border-[#eaecf0] bg-[#fafbfc] p-3"><summary className="cursor-pointer text-sm font-semibold text-[#344054]">Suggested categories ({Math.min(categoryChoices.length, 5)})</summary><div className="mt-2 space-y-1">{categoryChoices.slice(0, 5).map((option) => <button type="button" key={option.value} onClick={() => autosaveListingField("category_id", option.value)} className={`block w-full rounded-lg border px-3 py-2 text-left text-xs transition ${String(form.category_id) === option.value ? "border-blue-500 bg-blue-50 text-blue-900" : "border-[#e5e7eb] bg-white text-[#475467] hover:border-blue-300"}`}>{option.label}</button>)}</div></details> : null}
                <details className="ebay-category-browser rounded-lg border border-[#eaecf0] bg-white p-3" onToggle={(event) => { if (event.currentTarget.open && !categoryBrowseNodes.length) loadCategoryChildren(); }}><summary>Browse synchronized eBay categories</summary><div className="mt-2 flex flex-wrap gap-2"><Button type="button" size="sm" variant={categoryBrowseTree === "0" ? "default" : "outline"} onClick={() => { setCategoryBrowseTree("0"); setCategoryBrowseNodes([]); loadCategoryChildren("", "eBay", []); }}>eBay · tree 0</Button><Button type="button" size="sm" variant={categoryBrowseTree === "100" ? "default" : "outline"} onClick={() => { setCategoryBrowseTree("100"); setCategoryBrowseNodes([]); loadCategoryChildren("", "eBay Motors", []); }}>eBay Motors · tree 100</Button></div>{categoryBrowseSource === 'stale' ? <p className="mt-2 text-xs text-amber-800">Showing saved local taxonomy.</p> : null}{categoryBrowseNodes.length ? <div className="mt-3 max-h-64 space-y-1 overflow-y-auto rounded-lg border border-[#e5e7eb] bg-slate-50 p-2"><div className="mb-1 flex flex-wrap items-center gap-1 text-xs text-slate-600"><Button type="button" size="sm" variant="outline" onClick={() => loadCategoryChildren()}>Root</Button>{categoryBrowseTrail.map((entry, index) => <Button key={entry.id} type="button" size="sm" variant="outline" onClick={() => loadCategoryChildren(entry.id, entry.label, categoryBrowseTrail.slice(0, index))}>{entry.label}</Button>)}</div>{categoryBrowseNodes.map((node) => <div key={node.category_id} className="flex items-center justify-between gap-2 rounded border bg-white px-2 py-1 text-xs"><span><strong>{node.category_name}</strong><span className="ml-2 text-slate-500">{node.leaf ? "Leaf" : "Parent"}</span></span>{node.has_children ? <Button type="button" size="sm" variant="outline" onClick={() => loadCategoryChildren(node.category_id, node.category_name, categoryBrowseTrail)}>Browse</Button> : node.leaf && node.publishable ? <Button type="button" size="sm" onClick={() => selectBrowseCategory(node)}>Select leaf</Button> : <span className="text-amber-700">Browse only</span>}</div>)}</div> : null}</details>
                {categoryAspectLoading ? <p className="mt-2 text-xs text-slate-500">Loading required item specifics…</p> : null}
                {categoryAspects ? <div className="mt-3 rounded-xl border border-blue-200 bg-blue-50/40 p-3"><p className="text-xs font-bold uppercase tracking-wide text-blue-900">Required item specifics</p>{categoryAspects.required?.length ? <div className="mt-2 grid gap-3 sm:grid-cols-2">{categoryAspects.required.map((aspect) => <label key={aspect.name} className="space-y-1 text-xs font-semibold text-slate-800" data-aspect-name={aspect.name}>{aspect.name} *{aspect.allowed_values?.length ? <select value={categoryAspectValues[aspect.name] || ""} onChange={(event) => updateCategoryAspect(aspect.name, event.target.value)} className="h-10 w-full rounded-lg border border-slate-300 bg-white px-2 text-sm"><option value="">Select {aspect.name}</option>{aspect.allowed_values.map((value) => <option key={value} value={value}>{value}</option>)}</select> : <Input value={categoryAspectValues[aspect.name] || ""} onChange={(event) => updateCategoryAspect(aspect.name, event.target.value)} placeholder={`Enter ${aspect.name}`} />}</label>)}</div> : <p className="mt-2 text-sm text-slate-600">No required aspects are cached for this leaf yet.</p>}<p className="mt-4 text-xs font-bold uppercase tracking-wide text-slate-700">Recommended aspects</p><p className="mt-1 text-xs text-slate-600">{categoryAspects.recommended?.map((item) => item.name).filter(Boolean).join(", ") || "None available"}</p>{categoryAspects.error ? <p className="mt-2 text-xs text-amber-800">{categoryAspects.error}</p> : null}</div> : null}
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Condition</label>
                <Select className="h-10 w-full rounded-xl" value={form.condition} onChange={(event) => autosaveListingField("condition", event.target.value)}><option value="">Select condition</option>{CONDITION_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}</Select>
              </div>
              <div className="grid gap-3 rounded-xl border border-[#eaecf0] bg-[#fafbfc] p-4 sm:col-span-2 sm:grid-cols-2 xl:grid-cols-3">
                <p className="sm:col-span-2 xl:col-span-3 text-xs font-semibold uppercase tracking-[0.1em] text-[#667085]">Product identifiers and attributes</p>
                {[
                  ["Brand", readSpecific("Brand", "Brand Name") || listing?.brand || listing?.source_metadata?.amazon_product_facts?.brand || "", "Brand"],
                  ["Model", String(readSpecific("Model") || "").toUpperCase() === String(listing?.source_metadata?.asin || "").toUpperCase() ? "" : readSpecific("Model"), "Model"],
                  ["MPN", readSpecific("MPN", "Manufacturer Part Number"), "MPN"],
                  ["UPC", readSpecific("UPC", "EAN", "ISBN"), "UPC / EAN"],
                  ["Color", readSpecific("Color"), "Color"],
                  ["Material", readSpecific("Material", "Material Type"), "Material"],
                  ["Size", readSpecific("Size"), "Size / fit"],
                ].map(([key, value, label]) => <label key={key} className="block min-w-0 space-y-1 text-xs font-medium text-[#475467]">{label}<Input value={value} onChange={(event) => updateSpecific(key, event.target.value)} className="h-9 max-w-full" /></label>)}
              </div>
            </div>
          </SectionPanel>

          <SectionPanel title="Pricing, Inventory, and Shipping" description="Shared commercial fields used by direct publish paths and manual cross-post workflows.">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Listing price</label>
                <Input type="number" value={form.listing_price} onChange={(event) => setForm((current) => ({ ...current, listing_price: event.target.value }))} />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Suggested price</label>
                <Input type="number" value={form.suggested_price} onChange={(event) => setForm((current) => ({ ...current, suggested_price: event.target.value }))} />
              </div>
              {form.source_type === "amazon_vine" ? <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Vine estimated tax value (ETV)</label>
                <Input type="text" readOnly value={listing?.source_metadata?.estimated_tax_value == null ? "Not provided" : `$${Number(listing.source_metadata.estimated_tax_value).toFixed(2)}`} aria-label="Vine estimated tax value" />
                <p className="text-xs leading-5 text-[#667085]">Amazon’s reported tax value; it is not the amount paid to acquire the item.</p>
              </div> : null}
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Acquisition cost</label>
                <Input type="number" value={form.purchase_cost} onChange={(event) => setForm((current) => ({ ...current, purchase_cost: event.target.value }))} />
                {form.source_type === "amazon_vine" && listing?.source_metadata?.estimated_tax_value != null && Number(form.purchase_cost) === Number(listing.source_metadata.estimated_tax_value) ? <p className="text-xs leading-5 text-amber-800">This legacy acquisition-cost value matches the Vine ETV. It has not been changed; confirm it only if you actually paid this amount.</p> : null}
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Shipping cost</label>
                <Input type="number" value={form.shipping_cost} onChange={(event) => setForm((current) => ({ ...current, shipping_cost: event.target.value }))} />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Quantity</label>
                <Input type="number" min="1" value={form.quantity} onChange={(event) => setForm((current) => ({ ...current, quantity: event.target.value }))} />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Shipping mode</label>
                <Select
                  value={form.marketplace_data.shipping.mode}
                  onChange={(event) => setForm((current) => ({
                    ...current,
                    marketplace_data: {
                      ...current.marketplace_data,
                      shipping: { ...current.marketplace_data.shipping, mode: event.target.value },
                    },
                  }))}
                  className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                >
                  <option value="calculated">Calculated</option>
                  <option value="flat">Flat rate</option>
                  <option value="local_pickup">Local pickup</option>
                  <option value="manual">Manual</option>
                </Select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Shipping service</label>
                <Select
                  value={form.marketplace_data.shipping.domestic_service}
                  onChange={(event) => setForm((current) => ({
                    ...current,
                    marketplace_data: {
                      ...current.marketplace_data,
                      shipping: { ...current.marketplace_data.shipping, domestic_service: event.target.value },
                    },
                  }))}
                >
                  {SHIPPING_SERVICE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </Select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Handling days</label>
                <Input
                  type="number"
                  min="0"
                  value={form.marketplace_data.shipping.handling_time_days}
                  onChange={(event) => setForm((current) => ({
                    ...current,
                    marketplace_data: {
                      ...current.marketplace_data,
                      shipping: { ...current.marketplace_data.shipping, handling_time_days: Number(event.target.value || 0) },
                    },
                  }))}
                />
              </div>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              {[
                ["Free shipping", "free_shipping"],
                ["International enabled", "international_enabled"],
                ["Local pickup enabled", "local_pickup_enabled"],
              ].map(([label, key]) => (
                <label key={key} className="flex items-center justify-between rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] px-4 py-3 text-sm text-[#101828]">
                  {label}
                  <input
                    type="checkbox"
                    checked={!!form.marketplace_data.shipping[key]}
                    onChange={(event) => setForm((current) => ({
                      ...current,
                      marketplace_data: {
                        ...current.marketplace_data,
                        shipping: { ...current.marketplace_data.shipping, [key]: event.target.checked },
                      },
                    }))}
                  />
                </label>
              ))}
            </div>
          </SectionPanel>

          {!isNew && !listing?.sold_at ? <SectionPanel title="Record a sale" description="Sold this item in person or through another channel? Record it once and PosterPro will update inventory and reconcile active destinations.">
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_140px_auto] sm:items-end">
              <label className="space-y-1 text-sm font-medium text-[#101828]">Sale amount<Input type="number" min="0.01" step="0.01" value={manualSaleAmount} onChange={(event) => setManualSaleAmount(event.target.value)} placeholder="0.00" /></label>
              <label className="space-y-1 text-sm font-medium text-[#101828]">Units<Input type="number" min="1" step="1" value={manualSaleQuantity} onChange={(event) => setManualSaleQuantity(event.target.value)} /></label>
              <Button type="button" disabled={recordingSale || !Number(manualSaleAmount)} onClick={recordSale}>{recordingSale ? 'Recording…' : 'Record sale'}</Button>
            </div>
          </SectionPanel> : null}

          <SectionPanel title="Marketplace destinations" description="Choose where to publish. Listing preparation is independent; an unconnected destination will not block another.">
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#eaecf0] bg-[#fafbfc] px-4 py-3"><p className="text-sm text-[#475467]">Destination checks validate listing data separately from account access.</p><Button type="button" size="sm" variant="outline" onClick={refreshAllDestinationPreflights}>Refresh all destination checks</Button></div>
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                {Object.entries(CHANNEL_LABELS).map(([channel, label]) => {
                  const channelState = form.marketplace_data.channels[channel] || {};
                  const destinationPreflight = listing?.marketplace_data?.marketplace_preflight?.by_marketplace?.[channel] || listing?.canonical_readiness?.marketplace_readiness?.[channel] || {};
                  const destinationStatus = String(destinationPreflight.preparation_status || destinationPreflight.status || "not checked").replaceAll("_", " ");
                  const rawDestinationBlocker = destinationPreflight.preparation_blockers?.[0]?.message || destinationPreflight.blockers?.[0]?.message || destinationPreflight.blockers?.[0] || destinationPreflight.reason || "";
                  const destinationBlocker = typeof rawDestinationBlocker === "string" ? rawDestinationBlocker : rawDestinationBlocker ? JSON.stringify(rawDestinationBlocker) : "";
                  const executionBlocker = destinationPreflight.connection_blockers?.[0]?.message || "";
                  return (
                    <details key={channel} className="marketplace-destination-card min-w-0 rounded-[12px] border border-[#e5e7eb] bg-white p-3">
                      <summary className="flex cursor-pointer list-none items-start justify-between gap-2 [&::-webkit-details-marker]:hidden">
                        <div className="flex min-w-0 items-center gap-2">
                          <MarketplaceMark marketplace={channel} small />
                          <div>
                          <p className="text-sm font-semibold text-[#101828]">{label}</p>
                          <p className="mt-1 text-xs capitalize text-[#667085]">{destinationStatus}{channelState.enabled ? ' · selected' : ''}</p>
                          </div>
                        </div>
                        <span className="text-xs text-[#667085]">Details⌄</span>
                      </summary>
                      {destinationBlocker ? <p className="mt-2 text-xs leading-4 text-amber-700">{destinationBlocker}</p> : null}
                      {executionBlocker ? <p className="mt-1 text-xs leading-4 text-blue-800">Publish access: {executionBlocker}</p> : null}
                      <div className="mt-3 border-t border-[#eaecf0] pt-3">
                        <Checkbox aria-label={`Select ${label} as a publish destination`} checked={!!channelState.enabled} onChange={() => toggleChannel(channel)} label={`Publish to ${label}`} />
                      </div>
                      <div className="mt-3 space-y-3">
                        <div className="space-y-2">
                          <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Publish mode</label>
                          <Select value={channelState.publish_mode || "manual_or_provider"} onChange={(event) => setChannelField(channel, "publish_mode", event.target.value)} aria-label={`${label} publishing method`}>
                            {PUBLISH_MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                          </Select>
                        </div>
                        <div className="space-y-2">
                          <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Fulfillment</label>
                          <Select value={channelState.fulfillment || "shipping"} onChange={(event) => setChannelField(channel, "fulfillment", event.target.value)} aria-label={`${label} fulfillment`}>
                            {FULFILLMENT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                          </Select>
                        </div>
                        {channel === "facebook" ? (
                          <div className="space-y-2">
                            <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Renewal mode</label>
                            <Select
                              value={channelState.renewal_mode || "manual"}
                              onChange={(event) => setChannelField(channel, "renewal_mode", event.target.value)}
                              className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                            >
                              {FACEBOOK_RENEWAL_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </Select>
                          </div>
                        ) : null}
                      </div>
                    </details>
                  );
                })}
              </div>
              <div className="facebook-destination-rules grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(280px,0.85fr)]">
                <div className="facebook-shipping-panel rounded-[16px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
                  <p className="text-sm font-semibold text-[#101828]">Facebook shipping and meetup rules</p>
                  <div className="facebook-shipping-fields mt-3 grid gap-3">
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-[#101828]">Shipping scope</label>
                      <Select
                        value={form.marketplace_data.channels.facebook.shipping_scope || "local_only"}
                        onChange={(event) => setChannelField("facebook", "shipping_scope", event.target.value)}
                        className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                      >
                        {SHIPPING_SCOPE_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </Select>
                    </div>
                  </div>
                  <div className="facebook-meetup-notes mt-3 space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Meetup or shipping notes</label>
                    <Textarea
                      value={form.marketplace_data.shipping.facebook_meetup_notes}
                      onChange={(event) => setForm((current) => ({
                        ...current,
                        marketplace_data: {
                          ...current.marketplace_data,
                          shipping: { ...current.marketplace_data.shipping, facebook_meetup_notes: event.target.value },
                        },
                      }))}
                      className="min-h-28"
                      placeholder="Pickup radius, meetup rules, accepted shipping methods, packaging notes."
                    />
                  </div>
                </div>
                <div className="facebook-publishing-panel rounded-[16px] border border-[#f5d9a7] bg-[#fff9ef] p-4">
                  <div className="flex items-start gap-3">
                    <Truck className="mt-0.5 text-[#b54708]" size={18} />
                    <div>
                      <p className="text-sm font-semibold text-[#101828]">Facebook publishing status</p>
                      <p className="mt-1 text-sm text-[#475467]">{String(listing?.marketplace_data?.marketplace_preflight?.by_marketplace?.facebook?.connection_blockers?.[0]?.message || "No current Facebook publish-access blocker is recorded. Listing-data readiness is shown separately above.")}</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Button variant="outline" size="sm" onClick={() => router.push("/settings?tab=marketplaces&marketplace=facebook")}>
                          Facebook connection settings
                        </Button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </SectionPanel>

          <SectionPanel title="Product photos" description="Photos shown here are used in marketplace payload previews; source and diagnostics stay tucked away.">
            <div className="space-y-4">
              <details className="max-w-4xl rounded-xl border border-[#eaecf0] bg-white p-3">
                <summary className="cursor-pointer text-sm font-semibold text-[#344054]">Advanced · edit image source paths</summary>
                <div className="mt-3 space-y-2"><label className="text-xs font-medium text-[#667085]">One URL or stored media path per line</label><Textarea value={form.image_urls} onChange={(event) => setForm((current) => ({ ...current, image_urls: event.target.value }))} className="min-h-28 w-full" placeholder="One URL or file path per line" /></div>
              </details>
              <details className="rounded-xl border border-[#e5e7eb] bg-[#f8fafc] p-4">
                <summary className="cursor-pointer text-sm font-semibold text-[#344054]">Advanced source and diagnostic data</summary>
                <div className="mt-4 grid gap-4 xl:grid-cols-2">
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Item specifics JSON</label>
                    <Textarea
                      value={form.item_specifics_json}
                      onChange={(event) => setForm((current) => ({ ...current, item_specifics_json: event.target.value }))}
                      className="min-h-48 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 font-mono text-xs text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Source metadata JSON</label>
                    <Textarea
                      value={form.source_metadata_json}
                      onChange={(event) => setForm((current) => ({ ...current, source_metadata_json: event.target.value }))}
                      className="min-h-48 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 font-mono text-xs text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                    />
                  </div>
                </div>
              </details>
            </div>
          </SectionPanel>

          {isNew ? (
            <SectionPanel title="Import Existing Marketplace Listing" description="Paste a source listing payload and normalize it into a PosterPro draft.">
              <div className="grid gap-4 md:grid-cols-3">
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">Source marketplace</label>
                  <Select
                    value={importForm.source_marketplace}
                    onChange={(event) => setImportForm((current) => ({ ...current, source_marketplace: event.target.value }))}
                    className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  >
                    {Object.entries(CHANNEL_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">Import mode</label>
                  <Select
                    value={importForm.import_mode}
                    onChange={(event) => setImportForm((current) => ({ ...current, import_mode: event.target.value }))}
                    className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  >
                    <option value="manual">Manual</option>
                    <option value="provider_assist">Provider assist</option>
                    <option value="browser_assist">Browser assist</option>
                    <option value="csv_assist">CSV assist</option>
                  </Select>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">Source reference</label>
                  <Input
                    value={importForm.source_listing_reference}
                    onChange={(event) => setImportForm((current) => ({ ...current, source_listing_reference: event.target.value }))}
                    placeholder="FBM-12345 or external URL"
                  />
                </div>
              </div>
              <div className="mt-4 space-y-2">
                <label className="text-sm font-medium text-[#101828]">Payload JSON</label>
                <Textarea
                  value={importForm.payload_json}
                  onChange={(event) => setImportForm((current) => ({ ...current, payload_json: event.target.value }))}
                  className="min-h-52 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 font-mono text-xs text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                />
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button onClick={importMarketplacePayload} disabled={importing}>
                  {importing ? "Importing..." : "Create normalized draft"}
                </Button>
              </div>
            </SectionPanel>
          ) : null}
        </div>

        <div className="space-y-5">
          <SectionPanel id="listing-execution-preview" className="w-full min-w-0" title="Marketplace preview" description="Buyer-facing preview rendered from the destination payload PosterPro prepares. Publishing status and job history are shown separately below.">
            <div className="space-y-3">
              {crosspostPreview.length ? (
                <>
                  <div className="marketplace-preview-tablist flex flex-nowrap gap-2" role="tablist" aria-label="Marketplace preview selector">
                    {PREVIEW_MARKETPLACES.map((marketplace) => {
                      const entry = crosspostPreview.find((candidate) => String(candidate.marketplace).toLowerCase() === marketplace);
                      const selected = marketplace === previewMarketplace;
                      const preflight = listing?.marketplace_data?.marketplace_preflight?.by_marketplace?.[marketplace] || listing?.marketplace_preflight_summary?.by_marketplace?.[marketplace] || {};
                      const prep = preflight.preparation_status || (entry ? 'payload ready' : 'unavailable');
                      const shortStatus = prep === 'ready' ? 'Ready' : prep === 'ready_with_warnings' ? 'Warning' : prep === 'blocked' ? 'Blocked' : prep === 'unavailable' ? 'Unavailable' : 'Preview';
                      return <Button key={marketplace} type="button" variant="tertiary" role="tab" aria-selected={selected} onClick={() => setPreviewMarketplace(marketplace)} className={`marketplace-preview-tab !min-h-0 rounded-xl border px-3 py-2 text-left text-sm transition ${selected ? 'is-active' : ''}`}><span className="marketplace-preview-tab-name"><MarketplaceMark marketplace={marketplace} small /><span className="font-semibold">{MARKETPLACE_TAB_LABELS[marketplace] || marketplace}</span></span><span className="marketplace-preview-tab-status">{shortStatus}</span></Button>;
                    })}
                  </div>
                  {crosspostPreview.filter((entry) => String(entry.marketplace).toLowerCase() === previewMarketplace).map((entry) => (
                  <div key={entry.marketplace} className="space-y-3">
                    <MarketplaceVisualPreview entry={entry} formatMoney={formatMoney} />
                    <details className="rounded-xl border border-[#e5e7eb] bg-white p-3">
                      <summary className="cursor-pointer text-sm font-medium text-[#344054]">Advanced · inspect destination payload</summary>
                      <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-[#344054]">{JSON.stringify(entry.payload, null, 2)}</pre>
                    </details>
                  </div>
                  ))}
                  {!crosspostPreview.some((entry) => String(entry.marketplace).toLowerCase() === previewMarketplace) ? (
                    <div className="rounded-[12px] border border-[#fedf89] bg-[#fffaeb] p-4 text-sm text-[#93370d]">
                      {CHANNEL_LABELS[previewMarketplace] || previewMarketplace} has no destination payload. PosterPro will not fabricate a preview; inspect this destination’s data and capability status before publishing.
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-sm text-[#667085]">
                  Save the listing to generate destination payload previews, whether or not those marketplaces are selected or connected.
                </div>
              )}
            </div>
          </SectionPanel>

          <SectionPanel className="xl:col-span-2" title="Publish activity" description="Queued and completed marketplace operations for this listing.">
            <div className="space-y-3">
              {crosspostJobs.length ? (
                crosspostJobs.map((job) => (
                  <div key={job.id} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-semibold text-[#101828]">Job #{job.id}</p>
                      <StatusPill status={job.status} label={job.status} />
                    </div>
                    <p className="mt-2 text-sm text-[#475467]">Targets: {(job.target_marketplaces || []).join(", ") || "None"}</p>
                    {job.last_error ? <p className="mt-2 text-sm text-[#b42318]">{job.last_error}</p> : null}
                  </div>
                ))
              ) : (
                <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-sm text-[#667085]">
                  No cross-post jobs have been queued for this listing yet.
                </div>
              )}
            </div>
          </SectionPanel>
        </div>
      </div>
    </AppShell>
  );
}

ListingWorkspacePage.requireAuth = true;
