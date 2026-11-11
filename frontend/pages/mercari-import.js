import { useEffect, useMemo, useState } from 'react';
import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import SectionPanel from '../components/ui/section-panel';
import { exportMercariSpreadsheet, fetchMercariSpreadsheetReadiness } from '../lib/api';

export default function MercariImportPage() {
  const [report, setReport] = useState(null);
  const [selected, setSelected] = useState([]);
  const [template, setTemplate] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { fetchMercariSpreadsheetReadiness().then(setReport).catch((e) => setError(e.message)); }, []);
  const ready = useMemo(() => (report?.items || []).filter((row) => row.status === 'export_ready'), [report]);
  const exportSelected = async () => {
    if (!template || !selected.length) return;
    setBusy(true); setError('');
    try {
      const result = await exportMercariSpreadsheet(template, selected);
      const url = URL.createObjectURL(result.blob);
      const link = document.createElement('a'); link.href = url; link.download = 'posterpro-mercari-import-ready.xlsx'; link.click(); URL.revokeObjectURL(url);
    } catch (e) { setError(e.message || 'Spreadsheet export failed.'); }
    finally { setBusy(false); }
  };
  return <AppShell><main className="mx-auto max-w-6xl space-y-5 p-4 md:p-8">
    <header><h1 className="text-2xl font-semibold">Mercari spreadsheet preparation</h1><p className="mt-1 text-sm text-slate-600">PosterPro fills Mercari’s official workbook with selected inventory. The workbook imports listings as drafts; review and activate them on Mercari.</p></header>
    <SectionPanel title="Inventory readiness" description="Only active eBay-linked PosterPro inventory is included. Category IDs must have verified or operator-confirmed evidence.">
      {report?.ebay_snapshot ? <p className={`mb-3 text-xs ${report.ebay_snapshot.status === 'REMOTE_VERIFIED' ? 'text-emerald-800' : 'text-amber-800'}`}>eBay source: {report.ebay_snapshot.source} · {report.ebay_snapshot.status} · verified {report.ebay_snapshot.verified_at || 'never'}</p> : null}
      {report?.summary ? <div className="mb-4 grid gap-2 sm:grid-cols-4">{[['Export ready', report.summary.export_ready], ['Needs repair', report.summary.needs_repair], ['Category', report.summary.missing_category], ['Shipping', report.summary.missing_shipping]].map(([label, count]) => <div className="rounded border p-3" key={label}><div className="text-xs text-slate-500">{label}</div><div className="text-xl font-semibold">{count}</div></div>)}</div> : null}
      {report?.summary?.missing_required_information ? <p className="mb-3 text-sm text-amber-800">{report.summary.missing_required_information} records also need required content, condition, price, or photos.</p> : null}
      <div className="mb-3 flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={() => setSelected(ready.map((row) => row.listing_id))}>Select all {ready.length} export-ready</Button><Button variant="outline" size="sm" onClick={() => setSelected([])}>Clear selection</Button></div>
      {error ? <p role="alert" className="mb-3 rounded bg-red-50 p-3 text-sm text-red-800">{error}</p> : null}
      <div className="max-h-[50vh] overflow-auto rounded border">{(report?.items || []).map((row) => <label key={row.listing_id} className="flex gap-3 border-b p-3 text-sm last:border-0"><input type="checkbox" disabled={row.status !== 'export_ready'} checked={selected.includes(row.listing_id)} onChange={(e) => setSelected((current) => e.target.checked ? [...current, row.listing_id] : current.filter((id) => id !== row.listing_id))} /><span className="min-w-0 flex-1"><span className="block truncate font-medium">#{row.listing_id} · {row.title || 'Untitled'}</span><span className="text-xs text-slate-500">{row.status === 'export_ready' ? 'Export ready' : `Needs repair: ${row.blockers.join(', ')}`}{row.category_path ? ` · ${row.category_path}` : ''}</span></span></label>)}</div>
    </SectionPanel>
    <SectionPanel title="Build import workbook" description="Upload the current official Mercari template. PosterPro preserves its import_template columns and reference tabs, then adds a hidden PosterPro row map to retain inventory identities.">
      <p className="mb-3 text-sm text-slate-600">Download the template from Mercari’s spreadsheet import page, then upload the .xlsx file here.</p>
      <div className="flex flex-wrap items-center gap-3"><a className="rounded border px-3 py-2 text-sm" href="https://www.mercari.com/us/import-listing/" target="_blank" rel="noreferrer">Open Mercari import page</a><input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(e) => setTemplate(e.target.files?.[0] || null)} /><Button onClick={exportSelected} disabled={busy || !template || !selected.length}>{busy ? 'Preparing workbook…' : `Download ${selected.length} selected draft rows`}</Button></div>
      <p className="mt-3 text-xs text-slate-500">Importing and activating are separate Mercari actions. PosterPro does not upload the file, submit listings, or activate drafts.</p>
    </SectionPanel>
  </main></AppShell>;
}
