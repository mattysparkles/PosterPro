import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import AppShell from '../../components/layout/AppShell';
import { createInventoryPackage, fetchInventoryPackages, queueInventoryPackageLabel } from '../../lib/api';

export default function InventoryPackagesPage() {
  const [packages, setPackages] = useState([]);
  const [profiles, setProfiles] = useState(['thermal_4x6', 'thermal_54x100mm', 'receipt_58mm']);
  const [code, setCode] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const result = await fetchInventoryPackages();
      setPackages(result.packages || []); setProfiles(result.paper_profiles || profiles);
    } catch (error) { setMessage(error.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const create = async () => {
    if (!code.trim()) return;
    setBusy(true); setMessage('');
    try {
      const result = await createInventoryPackage({ package_code: code.trim() });
      setMessage(result.created ? `Created ${result.package.package_code}.` : `Using existing ${result.package.package_code}.`);
      setCode(''); await load();
    } catch (error) { setMessage(error.message); } finally { setBusy(false); }
  };
  const print = async (pkg) => {
    setBusy(true); setMessage('');
    try {
      const result = await queueInventoryPackageLabel(pkg.package_code, { copies: 1, paper_profile: 'thermal_4x6' });
      setMessage(`Queued label job #${result.job_id}. Open printable label to print from this browser.`);
      window.open(`/api${result.printable_url}`, '_blank', 'noopener,noreferrer');
      await load();
    } catch (error) { setMessage(error.message); } finally { setBusy(false); }
  };
  return <AppShell title="Packages" subtitle="Permanent physical package identities, nested containment, and thermal labels.">
    <div className="mx-auto max-w-5xl space-y-5 p-4 md:p-6">
      <section className="rounded-xl border bg-white p-4 shadow-sm">
        <h2 className="font-semibold text-slate-900">Create package</h2>
        <p className="mt-1 text-sm text-slate-600">Use a permanent, scannable code such as PKG-AO9-001. A package can remain outside a tote or be placed into one from its detail workflow.</p>
        <div className="mt-3 flex flex-wrap gap-2"><input aria-label="Package code" value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} placeholder="PKG-AO9-001" className="rounded border px-3 py-2" /><button type="button" disabled={busy || !code.trim()} onClick={create} className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-50">Create package</button></div>
        {message ? <p role="status" className="mt-3 text-sm text-slate-700">{message}</p> : null}
      </section>
      <section className="rounded-xl border bg-white p-4 shadow-sm">
        <div className="flex items-baseline justify-between gap-3"><h2 className="font-semibold text-slate-900">Packages</h2><span className="text-xs text-slate-500">Profiles: {profiles.join(', ')}</span></div>
        {!packages.length ? <p className="mt-3 text-sm text-slate-600">No packages yet. Create one before packing stock or printing a label.</p> : <div className="mt-3 divide-y">{packages.map((pkg) => <article key={pkg.package_uuid} className="flex flex-wrap items-center justify-between gap-3 py-3"><div><p className="font-medium text-slate-900">{pkg.package_code} <span className="text-sm font-normal text-slate-500">· {pkg.status}</span></p><p className="text-sm text-slate-600">{pkg.quantity} units · {pkg.parent_location_label}</p><p className="text-xs text-slate-500">Stable lookup: {pkg.lookup_url}</p></div><div className="flex gap-2"><Link className="rounded border px-3 py-2 text-sm" href={`/inventory/packages/${encodeURIComponent(pkg.package_code)}`}>Open</Link><button type="button" className="rounded border px-3 py-2 text-sm" disabled={busy} onClick={() => print(pkg)}>Print label</button></div></article>)}</div>}
      </section>
    </div>
  </AppShell>;
}
