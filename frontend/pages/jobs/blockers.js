import { useEffect, useState } from "react";
import { useRouter } from "next/router";
import AppShell from "../../components/layout/AppShell";
import ActionLink from "../../components/ui/action-link";
import Button from "../../components/ui/button";
import Input from "../../components/ui/input";
import PageHeader from "../../components/ui/page-header";
import { fetchProcessingBlockers, updateListing } from "../../lib/api";

export default function BlockerQueuePage() {
  const router = useRouter();
  const reason = typeof router.query.reason === "string" ? router.query.reason : "";
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [titles, setTitles] = useState({});
  const [saving, setSaving] = useState({});

  useEffect(() => {
    if (!router.isReady || !reason) return;
    setLoading(true);
    fetchProcessingBlockers(reason, 250).then(setData).finally(() => setLoading(false));
  }, [router.isReady, reason]);

  const identityBlocker = /title|identity|generic|unknown|caption/i.test(reason);
  const imageBlocker = /image|photo|primary|gallery/i.test(reason);

  async function saveTitle(item) {
    setSaving((current) => ({ ...current, [item.id]: true }));
    try {
      const title = titles[item.id] ?? item.title;
      await updateListing(item.id, { title });
      setData((current) => ({ ...current, items: current.items.map((row) => row.id === item.id ? { ...row, title } : row) }));
    } finally {
      setSaving((current) => ({ ...current, [item.id]: false }));
    }
  }

  return (
    <AppShell active="/jobs" title="Blocker queue">
      <PageHeader title={reason || "Blocker queue"} description="Affected listings, evidence, and inline repair options." actions={<Button variant="outline" onClick={() => router.push("/jobs")}>Back to Jobs</Button>} />
      <div className="mb-4 flex flex-wrap items-center justify-between rounded-xl border border-[var(--pp-border)] bg-[var(--pp-surface)] p-4"><p className="text-sm font-semibold">{data?.count ?? "—"} affected listings</p><Button onClick={() => router.push(`/listings?queue=needs_attention&blocker=${encodeURIComponent(reason)}`)}>Open filtered Listings</Button></div>
      {loading ? <p className="text-sm text-[var(--pp-muted)]">Loading affected listings…</p> : <div className="space-y-3">{(data?.items || []).map((item) => <article key={item.id} className="pp-card grid gap-3 p-4 md:grid-cols-[56px_1fr_auto] md:items-center"><div className="h-12 w-12 overflow-hidden rounded-lg bg-slate-100">{item.image_urls?.[0] ? <img src={item.image_urls[0]} alt="" className="block h-12 w-12 object-cover" /> : null}</div><div className="min-w-0"><ActionLink className="font-semibold" href={`/listings/${item.id}`}>{item.title || `Listing #${item.id}`}</ActionLink><p className="mt-1 text-xs text-[var(--pp-muted)]">#{item.id} · {item.source_type || "unknown"} · {item.processing_stage || item.processing_state}</p><p className="mt-1 text-sm text-slate-600">{item.next_action}</p>{identityBlocker ? <label className="mt-2 block text-xs font-semibold text-slate-600">Replacement title / identity<Input aria-label={`Replacement title for listing ${item.id}`} className="mt-1 h-10 rounded-lg" value={titles[item.id] ?? item.title ?? ""} onChange={(event) => setTitles((current) => ({ ...current, [item.id]: event.target.value }))} placeholder="Enter a specific sellable identity" /></label> : <p className="mt-2 text-xs font-semibold text-amber-700">Required correction: {imageBlocker ? "upload/approve the correct item image and primary image" : "resolve the blocker evidence shown above"}</p>}</div><div className="flex flex-wrap gap-2">{identityBlocker ? <Button size="sm" variant="outline" disabled={saving[item.id]} onClick={() => saveTitle(item)}>{saving[item.id] ? "Saving…" : "Save field"}</Button> : null}<Button size="sm" onClick={() => router.push(`/listings/${item.id}?mode=repair`)}>Fix automatically</Button></div></article>)}{!data?.items?.length ? <p className="text-sm text-[var(--pp-muted)]">No listings currently match this blocker.</p> : null}</div>}
    </AppShell>
  );
}
