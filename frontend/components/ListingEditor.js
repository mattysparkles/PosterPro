import { useMemo, useState } from "react";
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
  publishState,
  statuses,
  crosspostPreview = [],
  crosspostPreviewLoading = false,
}) {
  const [openEditor, setOpenEditor] = useState(false);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [selectedPlatforms, setSelectedPlatforms] = useState(["ebay"]);
  const [activePreviewMarket, setActivePreviewMarket] = useState("ebay");
  const requiresApproval = workflowPreferences?.review_before_publish ?? true;
  const intelligence = listingIntelligence?.intelligence || {};
  const draftMeta = listingIntelligence?.draft_meta || {};
  const pricingAnalysis = listingIntelligence?.pricing_analysis || pricingRecommendation || {};
  const readiness = listingIntelligence?.readiness || {};

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
    const sourceMetadata = listing?.source_metadata || {};
    return Array.from(new Set([
      ...(Array.isArray(listing?.image_urls) ? listing.image_urls : []),
      ...(Array.isArray(sourceMetadata?.image_urls) ? sourceMetadata.image_urls : []),
      ...(Array.isArray(sourceMetadata?.original_image_urls) ? sourceMetadata.original_image_urls : []),
    ].filter(Boolean)));
  }, [listing]);
  const activePreviewEntry = previewMap[activePreviewMarket] || null;

  const togglePlatform = (platform) => {
    setSelectedPlatforms((prev) =>
      prev.includes(platform)
        ? prev.filter((p) => p !== platform)
        : [...prev, platform],
    );
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
          defaultValue={listing.title || ""}
          placeholder="Short clear title (ex: Vintage Canon camera with lens)"
          onBlur={(e) => onSave(listing.id, { title: e.target.value })}
          title="This is the headline buyers see first."
        />
        <textarea
          defaultValue={listing.description || ""}
          placeholder="Describe condition, size, defects, accessories, and what is included."
          className="min-h-28 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
          onBlur={(e) => onSave(listing.id, { description: e.target.value })}
          title="Explain the item in plain words so anyone can understand quickly."
        />
        <Input
          type="number"
          defaultValue={listing.suggested_price || ""}
          placeholder="Suggested price"
          onBlur={(e) =>
            onSave(listing.id, { suggested_price: Number(e.target.value) })
          }
          title="Set a clear asking price."
        />
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

        <div className="mt-4 rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
          <p className="text-sm font-semibold text-[#101828]">Suggested item specifics</p>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {Object.entries(intelligence.item_specifics || {}).length ? (
              Object.entries(intelligence.item_specifics || {}).map(([key, value]) => (
                <div key={key} className="rounded-[10px] border border-[#e5e7eb] bg-white px-3 py-2">
                  <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{key}</p>
                  <p className="mt-1 text-sm text-[#101828]">{String(value)}</p>
                </div>
              ))
            ) : (
              <p className="text-sm text-[#667085]">No structured specifics generated yet.</p>
            )}
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

      <div className="mt-4 rounded-[14px] border border-[#e5e7eb] bg-white p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-[#101828]">Pricing reasoning</p>
            <p className="mt-1 text-sm text-[#667085]">Use this before approving or changing the asking price.</p>
          </div>
          {pricingAnalysis?.recommended_price ? (
            <span className="rounded-full border border-[#dbe7ff] bg-[#eef4ff] px-3 py-1 text-xs font-semibold text-[#2563eb]">
              Recommend ${pricingAnalysis.recommended_price}
            </span>
          ) : null}
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
            <p className="mt-2 text-lg font-semibold text-[#101828]">{pricingAnalysis?.confidence ? `${Math.round(pricingAnalysis.confidence * 100)}%` : 'Pending'}</p>
          </div>
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Comparable sales used</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">
              {(pricingAnalysis?.historical_comparable_count || 0) + (pricingAnalysis?.external_comparable_count || 0)}
            </p>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">External market avg</p>
            <p className="mt-2 text-lg font-semibold text-[#101828]">${pricingAnalysis?.external_market_avg_sold ?? '0'}</p>
          </div>
        </div>
        <div className="mt-3 rounded-[12px] border border-[#e5e7eb] bg-[#f8fafc] p-3">
          <p className="text-sm text-[#475467]">
            {pricingAnalysis?.reasoning || 'Pricing reasoning becomes richer once historical sold data and external comps are available for this item.'}
          </p>
        </div>
        {(pricingAnalysis?.comparable_titles || []).length ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {pricingAnalysis.comparable_titles.map((title) => (
              <span key={title} className="pp-chip">{title}</span>
            ))}
          </div>
        ) : null}
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
