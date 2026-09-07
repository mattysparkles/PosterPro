import { useEffect, useState } from "react";
import AppShell from "../components/layout/AppShell";
import Button from "../components/ui/button";
import PageHeader from "../components/ui/page-header";
import StatusPill from "../components/ui/status-pill";
import { useAuth } from "../contexts/AuthContext";
import { bulkProcessNotifications, fetchProcessNotifications } from "../lib/api";

export default function NoticesPage() {
  const { user } = useAuth();
  const pageSize = 50;
  const [rows, setRows] = useState([]); const [selected, setSelected] = useState([]); const [selectAllResults, setSelectAllResults] = useState(false); const [page, setPage] = useState(0); const [total, setTotal] = useState(0); const [loading, setLoading] = useState(true);
  const load = async (nextPage = page) => { setLoading(true); try { const data = await fetchProcessNotifications({ limit: pageSize, offset: nextPage * pageSize }); setRows(data?.notifications || []); setTotal(Number(data?.total || 0)); setPage(nextPage); setSelected([]); setSelectAllResults(false); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  const confirmBulk = async (action) => {
    if (!selected.length) return;
    const noun = action === "delete" ? "permanently delete" : action === "archive" ? "archive" : "mark as read";
    const countLabel = selectAllResults ? total : selected.length;
    if (!window.confirm(`Are you sure you want to ${noun} ${countLabel} notice${countLabel === 1 ? "" : "s"}?`)) return;
    await bulkProcessNotifications(selected, action, { selectAll: selectAllResults }); setSelected([]); setSelectAllResults(false); await load(page);
  };
  const toggle = (id) => setSelected((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  return <AppShell active="/notices" title="Notices"><PageHeader title="Process Notices" description="Review, resolve, archive, or remove workflow notices in bulk." actions={<Button variant="outline" onClick={load}>Refresh</Button>} />
    <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-[var(--pp-border)] bg-[var(--pp-surface)] p-3">
      <label className="flex items-center gap-2 text-sm font-semibold"><input type="checkbox" checked={rows.length > 0 && selected.length === rows.length} onChange={(event) => { setSelected(event.target.checked ? rows.map((row) => row.id) : []); setSelectAllResults(false); }} /> Select page</label>
      {selected.length === rows.length && rows.length > 0 && total > rows.length && !selectAllResults ? <Button size="sm" variant="outline" onClick={() => setSelectAllResults(true)}>Select all {total} notices</Button> : null}
      {selectAllResults ? <span className="text-sm font-semibold text-[var(--pp-accent)]">All {total} notices selected</span> : <span className="text-sm font-semibold">{selected.length} selected</span>}
      <Button size="sm" variant="outline" disabled={!selected.length && !selectAllResults} onClick={() => void confirmBulk("read")}>Mark read</Button><Button size="sm" variant="outline" disabled={!selected.length && !selectAllResults} onClick={() => void confirmBulk("archive")}>Archive</Button><Button size="sm" variant="outline" disabled={!selected.length && !selectAllResults} onClick={() => void confirmBulk("delete")}>Delete</Button>
    </div>
    <div className="space-y-2">{loading ? <p className="text-sm text-[var(--pp-muted)]">Loading notices…</p> : rows.map((row) => <article key={row.id} className="pp-card flex gap-3 p-4"><input type="checkbox" checked={selected.includes(row.id) || selectAllResults} onChange={() => toggle(row.id)} aria-label={`Select notice ${row.id}`} /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">{row.title}</h2><StatusPill status={row.read_at ? "default" : "warning"} label={row.read_at ? "Read" : "Unread"} /></div><p className="mt-1 text-sm text-[var(--pp-muted)]">{row.message || "No additional details."}</p><p className="mt-2 text-xs text-[var(--pp-muted)]">{row.notification_type} · {row.created_at ? new Date(row.created_at).toLocaleString() : ""}</p></div>{row.href ? <a className="text-sm font-semibold text-blue-600" href={row.href}>Open →</a> : null}</article>)}{!loading && !rows.length ? <p className="text-sm text-[var(--pp-muted)]">No active notices.</p> : null}</div>
    <div className="mt-4 flex items-center justify-between"><span className="text-sm text-[var(--pp-muted)]">Showing {total ? page * pageSize + 1 : 0}–{Math.min((page + 1) * pageSize, total)} of {total}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={page === 0 || loading} onClick={() => void load(page - 1)}>Previous</Button><Button size="sm" variant="outline" disabled={(page + 1) * pageSize >= total || loading} onClick={() => void load(page + 1)}>Next</Button></div></div>
  </AppShell>;
}
