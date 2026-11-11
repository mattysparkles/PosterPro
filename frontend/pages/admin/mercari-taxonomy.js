import { useEffect, useState } from "react";
import AppShell from "../../components/layout/AppShell";
import PageHeader from "../../components/ui/page-header";
import Button from "../../components/ui/button";
import { fetchMercariTaxonomyStatus, importMercariTaxonomy, importMercariOfficialWorkbook, searchMercariTaxonomy, resolveMercariTaxonomy, approveMercariTaxonomyMapping } from "../../lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";
export default function MercariTaxonomyAdmin() {
  const [status, setStatus] = useState(null), [query, setQuery] = useState(""), [items, setItems] = useState([]);
  const [payload, setPayload] = useState(""), [sourceComplete, setSourceComplete] = useState(false), [resolveText, setResolveText] = useState(""), [resolution, setResolution] = useState(null);
  const [mappingKey, setMappingKey] = useState(""); const [mappingLabel, setMappingLabel] = useState("");
  const [officialWorkbook, setOfficialWorkbook] = useState(null), [referenceImport, setReferenceImport] = useState(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function refresh() { setError(""); try { setStatus(await fetchMercariTaxonomyStatus()); } catch (e) { setError(e.message || "Could not load taxonomy status"); } }
  useEffect(() => { refresh(); }, []);
  async function search() { setBusy(true); setError(""); try { setItems((await searchMercariTaxonomy(query)).items || []); } catch (e) { setError(e.message || "Search failed"); } finally { setBusy(false); } }
  async function importData() {
    setBusy(true); setError("");
    try { await importMercariTaxonomy(payload, { source_completeness: sourceComplete ? "complete" : "partial" }); setPayload(""); await refresh(); await search(); }
    catch (e) { setError(e.message || "Import failed. Paste a valid JSON or CSV category export."); }
    finally { setBusy(false); }
  }
  async function importWorkbook() {
    if (!officialWorkbook) return;
    setBusy(true); setError("");
    try { setReferenceImport(await importMercariOfficialWorkbook(officialWorkbook)); await refresh(); await search(); }
    catch (e) { setError(e.message || "Could not read the official Mercari template."); }
    finally { setBusy(false); }
  }
  async function resolve() { setBusy(true); setError(""); try { setResolution(await resolveMercariTaxonomy({ title: resolveText })); } catch (e) { setError(e.message || "Resolve failed"); } finally { setBusy(false); } }
  async function approveMapping(item) { setBusy(true); setError(""); try { await approveMercariTaxonomyMapping({ mapping_key: mappingKey, mapping_label: mappingLabel, version_id: item.version_id, category_key: item.category_key, rationale: "Operator-approved after diagnostic path verification" }); await search(); } catch (e) { setError(e.message || "Could not save approved mapping"); } finally { setBusy(false); } }
  const latest = status?.latest;
  return <AppShell active="/admin/mercari-taxonomy" title="Mercari taxonomy">
    <PageHeader title="Mercari US category taxonomy" description="Versioned source data, searchable paths, and conservative category suggestions. External IDs remain source-scoped until validated against the authenticated listing form." />
    {error ? <div role="alert" className="mb-3 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</div> : null}
    <section className="pp-card mb-4 p-4">
      <h2 className="font-semibold">Acquisition status</h2>
      <p className="mt-2 text-sm">{status?.coverage_status || "Loading…"} · {latest ? `${latest.node_count} registry categories (${latest.source_node_count ?? latest.node_count} in imported source), ${latest.leaf_count} confirmed leaves, ${latest.verified_leaf_count ?? 0} verified leaves, ${latest.root_count} roots, depth ${latest.max_depth}` : "No taxonomy imported"}</p>
      <p className="text-sm text-slate-600">Latest: {latest?.version_id || "—"} · Source: {latest?.source || "—"} · Captured: {latest?.captured_at || "—"}</p>
      <p className="text-sm text-slate-600">Unresolved branches: {latest?.unresolved_branches ?? "—"} · Unknown terminal candidates: {status?.unknown_terminal_count ?? "—"} · Source coverage: {latest?.source_completeness || "—"} · Imported snapshot verification: {latest?.verification_status || "—"}</p>
      <div className="mt-3 flex flex-wrap gap-2"><a className="rounded border px-3 py-2 text-sm" href={`${API_BASE}/admin/mercari-taxonomy/export?fmt=json`}>Export JSON</a><a className="rounded border px-3 py-2 text-sm" href={`${API_BASE}/admin/mercari-taxonomy/export?fmt=csv`}>Export CSV</a><Button onClick={refresh}>Refresh</Button></div>
      <div className="mt-4 rounded-lg border border-sky-200 bg-sky-50 p-3"><h3 className="font-medium">Import official spreadsheet category IDs</h3><p className="mt-1 text-sm text-slate-700">Download Mercari&apos;s current .xlsx template from its import page, then upload it here. This records only IDs and hierarchy actually present in the workbook; missing leaf data stays unknown.</p><div className="mt-2 flex flex-wrap items-center gap-2"><input aria-label="Official Mercari workbook" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={event => setOfficialWorkbook(event.target.files?.[0] || null)} /><Button disabled={busy || !officialWorkbook} onClick={importWorkbook}>{busy ? "Importing…" : "Import workbook references"}</Button><a className="rounded border bg-white px-3 py-2 text-sm" href="https://www.mercari.com/us/help_center/article/554/" target="_blank" rel="noreferrer">Mercari template instructions</a></div>{referenceImport ? <p role="status" className="mt-2 text-sm text-emerald-900">Imported {referenceImport.spreadsheet_compatible_id_count} spreadsheet category IDs · version {referenceImport.version_id} · paths/leaves remain independently verified.</p> : null}</div>
    </section>
    <section className="pp-card mb-4 p-4">
      <h2 className="font-semibold">Import a taxonomy dataset</h2><p className="mb-2 text-sm text-slate-600">Paste or choose a JSON/CSV export. Apify’s Mercari Category URL actor export is supported. No paid actor run is started here.</p>
      <input className="mb-2 block text-sm" type="file" accept=".json,.csv,application/json,text/csv" onChange={async e => { const file = e.target.files?.[0]; if (file) setPayload(await file.text()); }} />
      <textarea className="w-full rounded border p-2 font-mono text-xs" rows={5} value={payload} onChange={e => setPayload(e.target.value)} placeholder='[{"mainCategoryNameL1":"Women", ...}] or CSV headers' />
      <label className="mt-2 flex items-start gap-2 text-sm"><input type="checkbox" checked={sourceComplete} onChange={e => setSourceComplete(e.target.checked)} /><span>Source explicitly represents a complete category acquisition. Leave unchecked for samples or partial exports; unknown paths remain unverified until independently confirmed in the live form.</span></label>
      <Button className="mt-2" disabled={busy || !payload.trim()} onClick={importData}>{busy ? "Working…" : "Import as new version"}</Button>
    </section>
    <section className="pp-card mb-4 p-4">
      <h2 className="font-semibold">Search taxonomy</h2><div className="mt-2 flex gap-2"><input className="min-w-0 flex-1 rounded border p-2" value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === "Enter" && search()} placeholder="Category name or path" /><Button disabled={busy} onClick={search}>Search</Button></div>
      <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm"><h3 className="font-medium">Authenticated browser discovery is unavailable</h3><p className="mt-1">Mercari&apos;s current US Prohibited Conduct policy bars automated access and category-data extraction, including browser-driven crawling. PosterPro therefore does not queue category-selector diagnostics or taxonomy discovery. You can import a reference dataset obtained through an officially supported Mercari spreadsheet workflow; browser category IDs remain distinct from spreadsheet IDs until the source verifies their equivalence.</p><a className="mt-2 inline-block underline" href="https://www.mercari.com/us/help_center/topics/account/policies/prohibited-conduct/" target="_blank" rel="noreferrer">Mercari Prohibited Conduct policy</a></div>
      <div className="mt-3 max-h-96 overflow-auto">{items.map(item => <div key={item.category_key} className="flex items-center justify-between gap-2 border-b py-2 text-sm"><div><div className="font-medium">{item.full_path}</div><div className="text-slate-500">{item.leaf_status === "confirmed_leaf" ? "Confirmed leaf" : item.leaf_status === "branch" ? "Branch" : "Terminal status unknown"} · ID {item.source_category_id || "not provided"} · {item.source_namespace} · {item.verification_status}</div></div>{item.verification_status === "path_verified" || item.verification_status === "operator_verified" ? <Button disabled={busy || !mappingKey.trim() || !mappingLabel.trim()} onClick={() => approveMapping(item)}>Approve mapping</Button> : null}</div>)}</div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2"><input className="rounded border p-2 text-sm" value={mappingKey} onChange={e => setMappingKey(e.target.value)} placeholder="Internal mapping key (e.g. womens_blouse)" /><input className="rounded border p-2 text-sm" value={mappingLabel} onChange={e => setMappingLabel(e.target.value)} placeholder="Human-readable mapping label" /></div>
    </section>
    <section className="pp-card p-4"><h2 className="font-semibold">Suggest a category for an item</h2><p className="text-sm text-slate-600">Suggestions are review-only. PosterPro will not auto-select a category without an approved mapping.</p><div className="mt-2 flex gap-2"><input className="min-w-0 flex-1 rounded border p-2" value={resolveText} onChange={e => setResolveText(e.target.value)} placeholder="Item title / category" /><Button disabled={busy || !resolveText.trim()} onClick={resolve}>Suggest</Button></div>{resolution ? <pre className="mt-3 max-h-80 overflow-auto rounded bg-slate-50 p-3 text-xs">{JSON.stringify(resolution, null, 2)}</pre> : null}</section>
  </AppShell>;
}
