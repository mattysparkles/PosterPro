import { useState } from "react";
import AppShell from "../../components/layout/AppShell";
import PageHeader from "../../components/ui/page-header";
import Button from "../../components/ui/button";
import { diagnoseListings } from "../../lib/api";

export default function ListingDiagnosticsPage() {
  const [ids, setIds] = useState("2177,2167,2170,2174,2186,2178,2179,2185,2187,2191");
  const [result, setResult] = useState(null); const [loading, setLoading] = useState(false); const [error, setError] = useState("");
  async function run() { setLoading(true); setError(""); try { setResult(await diagnoseListings(ids.split(/[,\s]+/).filter(Boolean).map(Number))); } catch (e) { setError(e.message || "Diagnostics failed"); } finally { setLoading(false); } }
  return <AppShell active="/admin/diagnostics" title="Listing diagnostics"><PageHeader title="Platform listing diagnostics" description="Fresh, safe readiness and correction blockers." /><div className="pp-card space-y-3 p-4"><textarea className="w-full rounded-lg border p-3" rows={3} value={ids} onChange={e => setIds(e.target.value)} /><Button onClick={run} disabled={loading}>{loading ? "Diagnosing…" : "Diagnose listings"}</Button>{error ? <p className="text-sm text-red-600">{error}</p> : null}</div>{result ? <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr><th>ID</th><th>Title</th><th>Correction</th><th>Condition</th><th>Category</th><th>Description</th><th>Preflight</th><th>Primary blocker</th></tr></thead><tbody>{result.items.map(row => <tr key={row.listing_id} className="border-t"><td>{row.listing_id}</td><td>{row.title || "—"}</td><td>{row.latest_correction_job?.status || "—"}</td><td>{row.condition?.value || "—"}</td><td>{row.category?.id || "—"}</td><td>{row.description?.quality || "—"}</td><td>{row.ebay_preflight?.status || row.status}</td><td>{row.primary_blocker || "—"}</td></tr>)}</tbody></table></div> : null}</AppShell>;
}
