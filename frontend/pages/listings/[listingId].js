import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import { Bot, ExternalLink, Save, Sparkles, Truck } from "lucide-react";

import AppShell from "../../components/layout/AppShell";
import Button from "../../components/ui/button";
import Input from "../../components/ui/input";
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
  fetchSettingsPanels,
  queueCrosspostJob,
  generateListing,
  requestListingRevision,
  toggleAutonomousMode,
  updateListing,
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
};

const MARKETPLACE_PREVIEW_STYLES = {
  ebay: { brand: 'eBay', accent: 'bg-[#3665f3]', shell: 'border-[#d9e2ff]', price: 'text-[#111827]' },
  facebook: { brand: 'Facebook Marketplace', accent: 'bg-[#0866ff]', shell: 'border-[#cfe0ff]', price: 'text-[#0f5132]' },
  mercari: { brand: 'Mercari', accent: 'bg-[#ff0211]', shell: 'border-[#ffd8dc]', price: 'text-[#b42318]' },
  poshmark: { brand: 'Poshmark', accent: 'bg-[#7b1e3a]', shell: 'border-[#efd7e0]', price: 'text-[#7b1e3a]' },
  etsy: { brand: 'Etsy', accent: 'bg-[#f1641e]', shell: 'border-[#ffe0cf]', price: 'text-[#9c2d00]' },
  depop: { brand: 'Depop', accent: 'bg-[#111827]', shell: 'border-[#d0d5dd]', price: 'text-[#111827]' },
  whatnot: { brand: 'Whatnot', accent: 'bg-[#6d28d9]', shell: 'border-[#e5dcff]', price: 'text-[#5b21b6]' },
  vinted: { brand: 'Vinted', accent: 'bg-[#007782]', shell: 'border-[#c6eef0]', price: 'text-[#00626c]' },
};

function MarketplaceVisualPreview({ entry, imageUrls, formatMoney }) {
  const payload = entry?.payload || {};
  const marketplace = String(entry?.marketplace || payload.marketplace || 'ebay').toLowerCase();
  const style = MARKETPLACE_PREVIEW_STYLES[marketplace] || MARKETPLACE_PREVIEW_STYLES.ebay;
  const images = Array.from(new Set([...(payload.image_urls || []), ...(imageUrls || [])].filter(Boolean))).slice(0, 12);
  const price = payload.price ?? payload.listing_price ?? payload.starting_bid;
  const shipping = payload.shipping_policy || payload.shipping || {};
  return (
    <div className={`overflow-hidden rounded-[18px] border bg-white shadow-[0_14px_28px_rgba(16,24,40,0.08)] ${style.shell}`}>
      <div className={`${style.accent} flex items-center justify-between px-4 py-3 text-white`}>
        <span className="text-base font-bold tracking-[-0.03em]">{style.brand}</span>
        <span className="rounded-full bg-white/18 px-2.5 py-1 text-[11px] font-semibold">Preview only · not published</span>
      </div>
      <div className="grid gap-4 p-4 md:grid-cols-[minmax(0,1.05fr)_minmax(220px,0.95fr)]">
        <div>
          <div className="aspect-square overflow-hidden rounded-[12px] bg-[#f2f4f7]">
            {images[0] ? <img src={images[0]} alt={payload.title || 'Listing preview'} className="h-full w-full object-contain" /> : <div className="flex h-full items-center justify-center text-sm text-[#667085]">No selected product image</div>}
          </div>
          {images.length > 1 ? (
            <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
              {images.map((url, index) => <img key={`${url}-${index}`} src={url} alt={`Listing image ${index + 1}`} className="h-14 w-14 shrink-0 rounded-[8px] border border-[#e5e7eb] object-cover" />)}
            </div>
          ) : null}
        </div>
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#667085]">{payload.category_hint || payload.category_id || 'Marketplace category pending review'}</p>
          <h3 className="mt-2 text-xl font-semibold leading-7 tracking-[-0.025em] text-[#101828]">{payload.title || 'Untitled listing'}</h3>
          <p className={`mt-4 text-2xl font-bold ${style.price}`}>{formatMoney(price)}</p>
          <div className="mt-4 space-y-2 text-sm text-[#475467]">
            <p><span className="font-semibold text-[#344054]">Condition:</span> {payload.condition || 'Review needed'}</p>
            <p><span className="font-semibold text-[#344054]">Quantity:</span> {payload.quantity ?? 1}</p>
            <p><span className="font-semibold text-[#344054]">Shipping:</span> {shipping.service || payload.delivery_method || 'See listing details'}</p>
          </div>
          {payload.description ? <p className="mt-4 line-clamp-5 text-sm leading-6 text-[#475467]">{payload.description}</p> : null}
        </div>
      </div>
    </div>
  );
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
  const [generating, setGenerating] = useState(false);
  const [revisionFields, setRevisionFields] = useState([]);
  const [revisionNote, setRevisionNote] = useState("");
  const [queueingCrosspost, setQueueingCrosspost] = useState(false);
  const [importing, setImporting] = useState(false);
  const [listing, setListing] = useState(null);
  const [crosspostPreview, setCrosspostPreview] = useState([]);
  const [crosspostJobs, setCrosspostJobs] = useState([]);
  const [workflow, setWorkflow] = useState({
    review_before_publish: true,
    auto_publish_after_approval: false,
    default_preview_marketplace: 'ebay',
  });
  const [previewMarketplace, setPreviewMarketplace] = useState('ebay');
  const [form, setForm] = useState(() => normalizeListingForm(null));
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
        if (fetchedListing?.id) {
          const targets = (fetchedListing.marketplace_data?.targets || []).filter(Boolean);
          const [preview, jobs] = await Promise.all([
            fetchCrosspostPreview(fetchedListing.id, targets).catch(() => []),
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

  const renderPreviewDetails = (entry) => {
    const payload = entry?.payload || {};
    const marketplace = String(entry?.marketplace || payload.marketplace || "").toLowerCase();
    const imageCount = Array.isArray(payload.image_urls) ? payload.image_urls.length : 0;
    if (marketplace === "ebay") {
      const policy = payload.shipping_policy || {};
      return [
        ["Title", payload.title || "—"],
        ["Price", formatMoney(payload.price)],
        ["Quantity", payload.quantity ?? "—"],
        ["Condition", payload.condition || "—"],
        ["Category", payload.category_id || "—"],
        ["Shipping", `${policy.service || "—"} · ${policy.free_shipping ? "Free shipping" : "Buyer pays"} · ${policy.handling_time_days ?? "—"} day handling`],
        ["Images", `${imageCount} attached`],
      ];
    }
    if (marketplace === "facebook") {
      return [
        ["Title", payload.title || "—"],
        ["Price", formatMoney(payload.price)],
        ["Condition", payload.condition || "—"],
        ["Availability", payload.availability || "—"],
        ["Delivery", payload.delivery_method || "—"],
        ["Category hint", payload.category_hint || "—"],
        ["Images", `${imageCount} attached`],
      ];
    }
    if (marketplace === "mercari") {
      return [
        ["Title", payload.title || "—"],
        ["Price", formatMoney(payload.price)],
        ["Condition", payload.condition || "—"],
        ["Brand", payload.brand || "—"],
        ["Category hint", payload.category_hint || "—"],
        ["Images", `${imageCount} attached`],
      ];
    }
    if (marketplace === "poshmark") {
      return [
        ["Title", payload.title || "—"],
        ["List price", formatMoney(payload.listing_price)],
        ["Brand", payload.brand || "—"],
        ["Size", payload.size || "—"],
        ["Condition", payload.condition || "—"],
        ["Category hint", payload.category_hint || "—"],
        ["Images", `${imageCount} attached`],
      ];
    }
    if (marketplace === "whatnot") {
      return [
        ["Title", payload.title || "—"],
        ["Starting bid", formatMoney(payload.starting_bid)],
        ["Quantity", payload.quantity ?? "—"],
        ["Condition", payload.condition || "—"],
        ["Category hint", payload.category_hint || "—"],
        ["Images", `${imageCount} attached`],
      ];
    }
    return [
      ["Title", payload.title || payload.headline || "—"],
      ["Price", formatMoney(payload.price || payload.listing_price || payload.starting_bid)],
      ["Condition", payload.condition || "—"],
      ["Images", `${imageCount} attached`],
    ];
  };

  const buildPayload = (nextStatus) => {
    const itemSpecifics = parseJsonField(form.item_specifics_json, "Item specifics");
    const sourceMetadata = parseJsonField(form.source_metadata_json, "Source metadata");
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
        const targets = (saved.marketplace_data?.targets || []).filter(Boolean);
        const [preview, jobs] = await Promise.all([
          fetchCrosspostPreview(saved.id, targets).catch(() => []),
          fetchCrosspostJobs(saved.id).catch(() => []),
        ]);
        setCrosspostPreview(preview || []);
        setCrosspostJobs(jobs || []);
      }
      await reloadDashboard();
      if (isNew) {
        await router.replace(`/listings/${saved.id}`);
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
      const revised = await requestListingRevision(listing.id, revisionFields, revisionNote);
      setListing(revised);
      setForm(normalizeListingForm(revised));
      setRevisionFields([]);
      setRevisionNote("");
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
        fetchCrosspostPreview(currentListing.id, targets).catch(() => []),
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

  return (
    <AppShell
      active="/listings"
      title="Listing Workspace"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reloadDashboard();
      }}
      contentWidth="wide"
    >
      <PageHeader
        title={title}
        description="One source-of-truth item record for manual entry, imported marketplace listings, AI-generated drafts, and cross-post planning."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => router.push("/listings")}>Back to listings</Button>
            <Button variant="secondary" onClick={runGenerate} disabled={generating}>
              <Bot size={16} />
              {generating ? "Generating..." : "AI fill draft"}
            </Button>
            <Button variant="outline" onClick={() => saveListing("draft")} disabled={saving}>
              <Save size={16} />
              {saving ? "Saving..." : "Save draft"}
            </Button>
            <Button variant="outline" onClick={queueCrosspost} disabled={queueingCrosspost}>
              {queueingCrosspost ? "Queueing..." : "Queue cross-post"}
            </Button>
            <Button onClick={() => saveListing("ready")} disabled={saving}>
              <Sparkles size={16} />
              Mark ready
            </Button>
          </div>
        }
      />

      <SectionPanel title="Send back to Drafts for correction" description="Choose the parts that need work. PosterPro records your request, moves the listing to Drafts, and uses your notes on the next AI revision.">
        <div className="flex flex-wrap gap-3">
          {['title', 'description', 'category', 'price', 'condition', 'photos', 'item specifics', 'shipping'].map((field) => <label key={field} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={revisionFields.includes(field)} onChange={() => setRevisionFields((current) => current.includes(field) ? current.filter((item) => item !== field) : [...current, field])} /> Fix {field}</label>)}
        </div>
        <textarea value={revisionNote} onChange={(event) => setRevisionNote(event.target.value)} className="mt-3 min-h-20 w-full rounded-[10px] border border-[#e5e7eb] p-3 text-sm" placeholder="Optional: describe what is wrong or point the AI to a label/photo." />
        <div className="mt-3"><Button variant="outline" onClick={requestRevision}>Send to Drafts &amp; request AI correction</Button></div>
      </SectionPanel>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(340px,0.75fr)]">
        <div className="space-y-5">
          <SectionPanel title="Core Listing" description="This record can be created manually or populated by imports and AI.">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2 md:col-span-2">
                <label className="text-sm font-medium text-[#101828]">Title</label>
                <Input value={form.title} onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))} placeholder="Vintage camera kit with lens and strap" />
              </div>
              <div className="space-y-2 md:col-span-2">
                <label className="text-sm font-medium text-[#101828]">Description</label>
                <textarea
                  value={form.description}
                  onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
                  className="min-h-36 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  placeholder="Describe condition, accessories, flaws, dimensions, and what is included."
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Category ID</label>
                <Input value={form.category_id} onChange={(event) => setForm((current) => ({ ...current, category_id: event.target.value }))} placeholder="Collectibles > Cameras" />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Category suggestion</label>
                <Input value={form.category_suggestion} onChange={(event) => setForm((current) => ({ ...current, category_suggestion: event.target.value }))} placeholder="Used by AI or imports" />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Condition</label>
                <Input value={form.condition} onChange={(event) => setForm((current) => ({ ...current, condition: event.target.value }))} placeholder="Used - Good" />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Tags</label>
                <Input value={form.tags} onChange={(event) => setForm((current) => ({ ...current, tags: event.target.value }))} placeholder="vintage, collectible, tested" />
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
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Purchase cost</label>
                <Input type="number" value={form.purchase_cost} onChange={(event) => setForm((current) => ({ ...current, purchase_cost: event.target.value }))} />
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
                <select
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
                </select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Domestic service</label>
                <Input
                  value={form.marketplace_data.shipping.domestic_service}
                  onChange={(event) => setForm((current) => ({
                    ...current,
                    marketplace_data: {
                      ...current.marketplace_data,
                      shipping: { ...current.marketplace_data.shipping, domestic_service: event.target.value },
                    },
                  }))}
                  placeholder="usps_ground_advantage"
                />
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

          <SectionPanel title="Cross-Post Plan" description="Choose target channels, posting mode, and marketplace-specific rules from the same item record.">
            <div className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {Object.entries(CHANNEL_LABELS).map(([channel, label]) => {
                  const channelState = form.marketplace_data.channels[channel] || {};
                  return (
                    <div key={channel} className="rounded-[16px] border border-[#e5e7eb] bg-white p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[#101828]">{label}</p>
                          <p className="mt-1 text-xs text-[#667085]">{channel === "facebook" ? "Manual/provider-first workflow" : "Connected or planned publish target"}</p>
                        </div>
                        <input type="checkbox" checked={!!channelState.enabled} onChange={() => toggleChannel(channel)} />
                      </div>
                      <div className="mt-3 space-y-3">
                        <div className="space-y-2">
                          <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Publish mode</label>
                          <Input value={channelState.publish_mode || ""} onChange={(event) => setChannelField(channel, "publish_mode", event.target.value)} />
                        </div>
                        <div className="space-y-2">
                          <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Fulfillment</label>
                          <Input value={channelState.fulfillment || ""} onChange={(event) => setChannelField(channel, "fulfillment", event.target.value)} />
                        </div>
                        {channel === "facebook" ? (
                          <div className="space-y-2">
                            <label className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Renewal mode</label>
                            <select
                              value={channelState.renewal_mode || "manual"}
                              onChange={(event) => setChannelField(channel, "renewal_mode", event.target.value)}
                              className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                            >
                              {FACEBOOK_RENEWAL_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          </div>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.9fr)]">
                <div className="rounded-[16px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
                  <p className="text-sm font-semibold text-[#101828]">Facebook shipping and meetup rules</p>
                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-[#101828]">Shipping scope</label>
                      <select
                        value={form.marketplace_data.channels.facebook.shipping_scope || "local_only"}
                        onChange={(event) => setChannelField("facebook", "shipping_scope", event.target.value)}
                        className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                      >
                        {SHIPPING_SCOPE_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-[#101828]">Source marketplace</label>
                      <Input
                        value={form.source_marketplace}
                        onChange={(event) => setForm((current) => ({ ...current, source_marketplace: event.target.value }))}
                        placeholder="facebook, ebay, mercari, manual"
                      />
                    </div>
                  </div>
                  <div className="mt-3 space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Meetup or shipping notes</label>
                    <textarea
                      value={form.marketplace_data.shipping.facebook_meetup_notes}
                      onChange={(event) => setForm((current) => ({
                        ...current,
                        marketplace_data: {
                          ...current.marketplace_data,
                          shipping: { ...current.marketplace_data.shipping, facebook_meetup_notes: event.target.value },
                        },
                      }))}
                      className="min-h-28 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                      placeholder="Pickup radius, meetup rules, accepted shipping methods, packaging notes."
                    />
                  </div>
                </div>
                <div className="rounded-[16px] border border-[#f5d9a7] bg-[#fff9ef] p-4">
                  <div className="flex items-start gap-3">
                    <Truck className="mt-0.5 text-[#b54708]" size={18} />
                    <div>
                      <p className="text-sm font-semibold text-[#101828]">Reality check for Facebook automation</p>
                      <p className="mt-1 text-sm text-[#475467]">
                        PosterPro now stores the full Facebook operating plan in-product, but direct Facebook listing import, renewals, and live posting still depend on a supported provider or manual/browser-assisted workflow. This workspace is the source of truth for that process.
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Button variant="outline" size="sm" onClick={() => router.push("/settings?tab=marketplaces&marketplace=facebook")}>
                          Open Facebook channel setup
                        </Button>
                        <Button variant="secondary" size="sm" onClick={() => router.push("/settings?tab=workflow")}>
                          Review publish policy
                        </Button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </SectionPanel>

          <SectionPanel title="Media and Structured Data" description="Manual entry, import jobs, and AI generation all feed these fields.">
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Image URLs or stored media paths</label>
                <textarea
                  value={form.image_urls}
                  onChange={(event) => setForm((current) => ({ ...current, image_urls: event.target.value }))}
                  className="min-h-28 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  placeholder="One URL or file path per line"
                />
              </div>
              <div className="grid gap-4 xl:grid-cols-2">
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">Item specifics JSON</label>
                  <textarea
                    value={form.item_specifics_json}
                    onChange={(event) => setForm((current) => ({ ...current, item_specifics_json: event.target.value }))}
                    className="min-h-48 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 font-mono text-xs text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">Source metadata JSON</label>
                  <textarea
                    value={form.source_metadata_json}
                    onChange={(event) => setForm((current) => ({ ...current, source_metadata_json: event.target.value }))}
                    className="min-h-48 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 font-mono text-xs text-[#101828] outline-none transition focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  />
                </div>
              </div>
            </div>
          </SectionPanel>

          {isNew ? (
            <SectionPanel title="Import Existing Marketplace Listing" description="Paste a source listing payload and normalize it into a PosterPro draft.">
              <div className="grid gap-4 md:grid-cols-3">
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">Source marketplace</label>
                  <select
                    value={importForm.source_marketplace}
                    onChange={(event) => setImportForm((current) => ({ ...current, source_marketplace: event.target.value }))}
                    className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  >
                    {Object.entries(CHANNEL_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#101828]">Import mode</label>
                  <select
                    value={importForm.import_mode}
                    onChange={(event) => setImportForm((current) => ({ ...current, import_mode: event.target.value }))}
                    className="pp-input h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
                  >
                    <option value="manual">Manual</option>
                    <option value="provider_assist">Provider assist</option>
                    <option value="browser_assist">Browser assist</option>
                    <option value="csv_assist">CSV assist</option>
                  </select>
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
                <textarea
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
          <SectionPanel title="Workspace Status" description="Use this as the operator-side summary before approval or cross-posting.">
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill status={listing?.status || "draft"} label={listing?.status || (isNew ? "new draft" : "draft")} />
                <StatusPill status={workflow.review_before_publish ? "info" : "warning"} label={workflow.review_before_publish ? "Approval required" : "Direct publish allowed"} />
              </div>
              <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-4 text-sm text-[#475467]">
                Save here first, then let AI enrich the record, then approve for publish. The same page works for manual products, imported marketplace listings, and photo-ingested drafts.
              </div>
            </div>
          </SectionPanel>

          <SectionPanel title="Import and Cross-Post Guidance" description="Operational guidance for the current marketplace stack.">
            <div className="space-y-3 text-sm text-[#475467]">
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                eBay is the current direct publishing path. Facebook, Mercari, Poshmark, and the other channels are now modeled here with import, publish, shipping, and renewal settings so a self-hosting operator has one control surface, even where direct APIs are still partial or unavailable.
              </div>
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                For cross-posting, this listing stores the shared source data first. Channel-specific adapters can then either publish directly, open a draft-first workflow, or hand off to a supported provider without duplicating item data.
              </div>
              <button type="button" onClick={() => router.push("/settings?tab=marketplaces")} className="inline-flex items-center gap-2 text-sm font-medium text-[#2563eb]">
                Open marketplace connection settings
                <ExternalLink size={14} />
              </button>
            </div>
          </SectionPanel>

          <SectionPanel id="listing-execution-preview" title="Execution Preview" description="See how PosterPro will treat each selected target before it publishes or hands off.">
            <div className="space-y-3">
              {crosspostPreview.length ? (
                <>
                  <div className="flex flex-wrap gap-2" role="tablist" aria-label="Marketplace preview selector">
                    {crosspostPreview.map((entry) => {
                      const marketplace = String(entry.marketplace || 'ebay').toLowerCase();
                      const selected = marketplace === previewMarketplace;
                      return <button key={marketplace} type="button" onClick={() => setPreviewMarketplace(marketplace)} className={`rounded-full border px-3 py-2 text-sm font-semibold transition ${selected ? 'border-[#2563eb] bg-[#eef4ff] text-[#1d4ed8]' : 'border-[#e5e7eb] bg-white text-[#475467]'}`}>{CHANNEL_LABELS[marketplace] || marketplace}</button>;
                    })}
                  </div>
                  {crosspostPreview.filter((entry) => String(entry.marketplace).toLowerCase() === previewMarketplace).map((entry) => (
                  <div key={entry.marketplace} className="space-y-3">
                    <MarketplaceVisualPreview entry={entry} imageUrls={form.image_urls.split('\n').map((value) => value.trim()).filter(Boolean)} formatMoney={formatMoney} />
                    <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-semibold text-[#101828]">{CHANNEL_LABELS[entry.marketplace] || entry.marketplace}</p>
                      <StatusPill status={entry.execution_mode === "direct_api" ? "success" : "warning"} label={entry.execution_mode} />
                    </div>
                    <div className="mt-3 grid gap-3 lg:grid-cols-2">
                      <div className="rounded-[10px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#667085]">Preview summary</p>
                        <dl className="mt-3 grid gap-2">
                          {renderPreviewDetails(entry).map(([label, value]) => (
                            <div key={label} className="flex items-start justify-between gap-3 text-sm">
                              <dt className="text-[#667085]">{label}</dt>
                              <dd className="text-right font-medium text-[#101828]">{value}</dd>
                            </div>
                          ))}
                        </dl>
                      </div>
                      <details className="rounded-[10px] border border-[#e5e7eb] bg-[#f8fafc] p-3">
                        <summary className="cursor-pointer text-sm font-medium text-[#101828]">Payload JSON</summary>
                        <pre className="mt-3 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-[#344054]">
                          {JSON.stringify(entry.payload, null, 2)}
                        </pre>
                      </details>
                    </div>
                    {entry.notes?.length ? (
                      <div className="mt-3 space-y-1 text-sm text-[#475467]">
                        {entry.notes.map((note) => (
                          <p key={note}>{note}</p>
                        ))}
                      </div>
                    ) : null}
                    {entry.execution_mode === "browser_assist" ? (
                      <div className="mt-3 rounded-[10px] border border-[#dbe7ff] bg-[#f7faff] p-3 text-sm text-[#1d4ed8]">
                        Queueing this target does not by itself guarantee final marketplace submission. Check Settings for the current bridge submit policy if you need to confirm whether this deployment stops at draft-fill or is allowed to click the final marketplace submit step.
                      </div>
                    ) : null}
                    </div>
                  </div>
                  ))}
                </>
              ) : (
                <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-sm text-[#667085]">
                  Save the listing first to generate an execution preview for each selected target marketplace.
                </div>
              )}
            </div>
          </SectionPanel>

          <SectionPanel title="Cross-Post Jobs" description="Queued and completed cross-post orchestration for this listing.">
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
