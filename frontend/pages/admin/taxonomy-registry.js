import { useEffect, useState } from 'react';
import AppShell from '../../components/layout/AppShell';
import PageHeader from '../../components/ui/page-header';
import Button from '../../components/ui/button';
import {
  fetchMarketplaceTaxonomyRegistryStatus,
  searchMarketplaceTaxonomyRegistry,
  refreshOfficialEbayTaxonomy,
  synchronizeMarketplaceTaxonomyRegistry,
  fetchInventoryTaxonomyCohorts,
  fetchInventoryCohortTaxonomyCandidates,
  fetchMarketplaceExtensionDevices,
  createTaxonomyTestSession,
  fetchTaxonomyTestSessions,
  cancelTaxonomyTestSession,
  createTaxonomyTestJob,
  fetchTaxonomyTestJobs,
  cancelTaxonomyTestJob,
} from '../../lib/api';

const MARKETS = ['ebay', 'mercari', 'facebook'];
const LABELS = { ebay: 'eBay', mercari: 'Mercari', facebook: 'Facebook Marketplace' };

function titleCase(value) {
  return String(value || 'unknown').replaceAll('_', ' ');
}

export default function TaxonomyRegistryPage() {
  const [status, setStatus] = useState(null);
  const [marketplace, setMarketplace] = useState('ebay');
  const [query, setQuery] = useState('');
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [inventoryReport, setInventoryReport] = useState(null);
  const [inventoryBusy, setInventoryBusy] = useState(false);
  const [candidateBusy, setCandidateBusy] = useState('');
  const [candidateReport, setCandidateReport] = useState(null);
  const [devices, setDevices] = useState([]);
  const [devicesLoading, setDevicesLoading] = useState(true);
  const [devicesError, setDevicesError] = useState('');
  const [deviceCurrentVersion, setDeviceCurrentVersion] = useState('');
  const [testDeviceId, setTestDeviceId] = useState('');
  const [testSession, setTestSession] = useState(null);
  const [expiredTestSession, setExpiredTestSession] = useState(null);
  const [testOptIn, setTestOptIn] = useState(false);
  const [testCommand, setTestCommand] = useState('INSPECT_FORM');
  const [testParametersText, setTestParametersText] = useState('{}');
  const [testJobs, setTestJobs] = useState([]);
  const [testBusy, setTestBusy] = useState(false);

  async function refreshStatus() {
    setError('');
    try { setStatus(await fetchMarketplaceTaxonomyRegistryStatus()); }
    catch (e) { setError(e.message || 'Could not load taxonomy registry status.'); }
  }

  async function refreshInventoryReport() {
    setInventoryBusy(true);
    try { setInventoryReport(await fetchInventoryTaxonomyCohorts(30)); }
    catch (e) { setError(e.message || 'Could not load inventory taxonomy coverage.'); }
    finally { setInventoryBusy(false); }
  }

  async function analyzeCohort(cohort) {
    setCandidateBusy(cohort.cohort_key); setError(''); setCandidateReport(null);
    try { setCandidateReport(await fetchInventoryCohortTaxonomyCandidates(cohort.cohort_key)); }
    catch (e) { setError(e.message || 'Could not generate category suggestions for this cohort.'); }
    finally { setCandidateBusy(''); }
  }

  async function search() {
    setBusy(true); setError('');
    try { setItems((await searchMarketplaceTaxonomyRegistry(marketplace, query)).items || []); }
    catch (e) { setError(e.message || 'Could not search the taxonomy registry.'); }
    finally { setBusy(false); }
  }

  async function refreshDevices() {
    setDevicesLoading(true); setDevicesError('');
    try {
      const result = await fetchMarketplaceExtensionDevices();
      const paired = (result.devices || []).filter((device) => !device.revoked);
      setDevices(paired);
      setDeviceCurrentVersion(result.current_version || '');
      const eligible = paired.find((device) => device.extension_version === result.current_version
        && device.last_seen_at && Date.now() - Date.parse(device.last_seen_at) <= 180000);
      setTestDeviceId((selected) => paired.some((device) => String(device.id) === selected) ? selected : (eligible ? String(eligible.id) : ''));
    } catch (e) {
      setDevices([]); setTestDeviceId(''); setDevicesError(e.message || 'Could not load paired browser devices.');
    } finally { setDevicesLoading(false); }
  }

  async function refreshTestSessions() {
    try {
      const result = await fetchTaxonomyTestSessions();
      setTestSession(result.items?.[0] || null);
      setExpiredTestSession(result.items?.length ? null : (result.latest_expired || null));
    } catch (e) { setError(e.message || 'Could not load taxonomy authorization status.'); }
  }

  function deviceUnavailableReason(device) {
    if (device.revoked) return 'revoked';
    if (device.extension_version !== deviceCurrentVersion) return `needs extension ${deviceCurrentVersion || 'update'}`;
    const seen = Date.parse(device.last_seen_at || '');
    if (!Number.isFinite(seen) || Date.now() - seen > 180000) return 'offline';
    return '';
  }

  async function syncLocalSnapshots() {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await synchronizeMarketplaceTaxonomyRegistry(MARKETS);
      setMessage(`Synchronized ${result.snapshots?.reduce((sum, snapshot) => sum + snapshot.categories, 0) || 0} local category records. No provider or browser requests were made.`);
      await refreshStatus();
      await search();
    } catch (e) { setError(e.message || 'Could not synchronize taxonomy snapshots.'); }
    finally { setBusy(false); }
  }

  async function refreshEbayProvider() {
    setBusy(true); setError(''); setMessage('Refreshing the official eBay trees and aspects for active category use…');
    try {
      const result = await refreshOfficialEbayTaxonomy();
      const treeSummary = (result.trees || []).map((tree) => `${tree.marketplace_id} tree ${tree.tree_id} v${tree.tree_version}: ${tree.category_count} categories`).join(' · ');
      const aspectCount = result.aspects?.refreshed?.length || 0;
      setMessage(`Official provider refresh complete. ${treeSummary}. Refreshed ${aspectCount} category aspect sets.`);
      await refreshStatus(); await search();
    } catch (e) { setError(e.message || 'Official eBay taxonomy refresh failed.'); setMessage(''); }
    finally { setBusy(false); }
  }

  useEffect(() => { refreshStatus(); }, []);
  useEffect(() => {
    let cancelled = false;
    refreshDevices().catch(() => {});
    fetchTaxonomyTestJobs().then((result) => { if (!cancelled) setTestJobs(result.items || []); }).catch(() => {});
    refreshTestSessions().catch(() => {});
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    if (!testSession?.session_id) return undefined;
    const timer = setInterval(() => fetchTaxonomyTestJobs().then((result) => setTestJobs(result.items || [])).catch(() => {}), 2500);
    return () => clearInterval(timer);
  }, [testSession?.session_id]);

  async function authorizeTaxonomyTestSession() {
    setTestBusy(true); setError('');
    try {
      if (!testOptIn) throw new Error('Check the authorization consent before creating a taxonomy test session.');
      const selectedDevice = devices.find((device) => String(device.id) === testDeviceId);
      if (!selectedDevice) throw new Error('Select an eligible paired browser before authorizing the session.');
      const unavailable = deviceUnavailableReason(selectedDevice);
      if (unavailable) throw new Error(`The selected paired browser is ${unavailable}. Refresh the device list and retry when it is online and compatible.`);
      const result = await createTaxonomyTestSession({ device_id: Number(testDeviceId), operator_opt_in: testOptIn, expires_in_seconds: 1800 });
      setTestSession(result); setExpiredTestSession(null); setMessage(`Facebook taxonomy test session authorized for device ${result.device_id} until ${new Date(result.expires_at).toLocaleTimeString()}.${result.collector_binding_reactivated ? ' The existing collector window binding is ready to resume.' : ''}`);
    } catch (e) { setError(e.message || 'Could not authorize the taxonomy test session.'); }
    finally { setTestBusy(false); }
  }

  async function queueTaxonomyTest() {
    if (!testSession?.session_id) return;
    setTestBusy(true); setError('');
    try {
      const parameters = JSON.parse(testParametersText || '{}');
      if (!parameters || Array.isArray(parameters) || typeof parameters !== 'object') throw new Error('Parameters must be a JSON object containing only documented fields.');
      if (testCommand === 'RUN_COLLECTION_STEP' && parameters.max_nodes == null) parameters.max_nodes = 1;
      await createTaxonomyTestJob({ session_id: testSession.session_id, command: testCommand, parameters, expires_in_seconds: 180 });
      setTestJobs((await fetchTaxonomyTestJobs()).items || []);
    } catch (e) { setError(e.message || 'Could not queue the taxonomy test command.'); }
    finally { setTestBusy(false); }
  }

  async function stopTaxonomyTestSession() {
    if (!testSession?.session_id) return;
    setTestBusy(true);
    try { await cancelTaxonomyTestSession(testSession.session_id); setTestSession(null); setTestOptIn(false); setTestJobs((await fetchTaxonomyTestJobs()).items || []); }
    catch (e) { setError(e.message || 'Could not cancel the taxonomy test session.'); }
    finally { setTestBusy(false); }
  }
  useEffect(() => {
    let cancelled = false;
    searchMarketplaceTaxonomyRegistry(marketplace, '')
      .then((result) => { if (!cancelled) setItems(result.items || []); })
      .catch((e) => { if (!cancelled) setError(e.message || 'Could not load registry records.'); });
    return () => { cancelled = true; };
  }, [marketplace]);

  const markets = status?.marketplaces || [];
  const selectedStatus = markets.find((item) => item.marketplace === marketplace);

  return <AppShell active="/admin/taxonomy-registry" title="Taxonomy registry">
    <PageHeader title="Marketplace taxonomy registry" description="Versioned category evidence for eBay, Mercari, and Facebook Marketplace. Partial imports and inferred candidates are never presented as complete verified trees." />
    {error ? <div role="alert" className="mb-3 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</div> : null}
    {message ? <div role="status" className="mb-3 rounded border border-green-300 bg-green-50 p-3 text-sm text-green-900">{message}</div> : null}
    <section className="pp-card mb-5 p-4" aria-label="Facebook taxonomy test bridge">
      <h2 className="font-semibold">Facebook seller-form test bridge</h2>
      <p className="mt-1 max-w-4xl text-sm text-slate-600">Short-lived, device-scoped inspection commands run inside the paired Chrome session. Commands are allowlisted and cannot submit or publish listings. Open a blank Facebook Marketplace item-create form before collection steps.</p>
      {!testSession ? <div className="mt-3 flex flex-wrap items-center gap-3">
        <label className="text-sm">Paired browser
          <select className="ml-2 rounded border px-2 py-2" aria-label="Taxonomy test browser" value={testDeviceId} onChange={(e) => setTestDeviceId(e.target.value)} disabled={devicesLoading || !!devicesError}>
            <option value="">Select device</option>{devices.map((device) => {
              const unavailable = deviceUnavailableReason(device);
              return <option key={device.id} value={device.id} disabled={!!unavailable}>{device.name} · {device.extension_version} · #{device.id}{unavailable ? ` · ${unavailable}` : ''}</option>;
            })}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={testOptIn} onChange={(e) => setTestOptIn(e.target.checked)} />Authorize up to 30 minutes of Facebook taxonomy tests (max 40 commands)</label>
        <Button disabled={testBusy || devicesLoading || !!devicesError || !testOptIn || !testDeviceId || !devices.some((device) => String(device.id) === testDeviceId && !deviceUnavailableReason(device))} onClick={authorizeTaxonomyTestSession}>Authorize test session</Button>
        <Button variant="outline" disabled={devicesLoading} onClick={refreshDevices}>{devicesLoading ? 'Loading devices…' : 'Retry device list'}</Button>
        <span role="status" className="text-sm text-slate-600">{expiredTestSession ? `Previous taxonomy authorization expired ${new Date(expiredTestSession.expires_at).toLocaleString()}; authorize a new session for a paired device.` : 'No active taxonomy authorization.'}</span>
      </div> : <div className="mt-3 flex flex-wrap items-center gap-3">
        <span className="text-sm">Authorized until {new Date(testSession.expires_at).toLocaleTimeString()}</span>
        <label className="text-sm">Command
          <select className="ml-2 rounded border px-2 py-2" value={testCommand} onChange={(e) => setTestCommand(e.target.value)}>
            {['INSPECT_FORM','INSPECT_CATEGORY_PICKER','OPEN_CATEGORY_PICKER','ENUMERATE_VISIBLE_OPTIONS','SCROLL_CATEGORY_LIST','SELECT_CATEGORY_OPTION','BACKTRACK_CATEGORY','INSPECT_CATEGORY_FIELDS','GET_COLLECTOR_STATE','RUN_COLLECTION_STEP','STOP_COLLECTION'].map((command) => <option key={command}>{command}</option>)}
          </select>
        </label>
        <label className="text-sm">Structured parameters
          <input className="ml-2 w-72 rounded border px-2 py-2 font-mono text-xs" aria-label="Taxonomy test parameters" value={testParametersText} onChange={(e) => setTestParametersText(e.target.value)} placeholder='{"path":["Household"],"label":"Kitchen"}' />
        </label>
        <Button disabled={testBusy} onClick={queueTaxonomyTest}>Queue command</Button>
        <Button disabled={testBusy} variant="outline" onClick={stopTaxonomyTestSession}>Stop session</Button>
        <code className="max-w-full break-all text-xs text-slate-500">Session capability: {testSession.session_id}</code>
      </div>}
      {devicesError ? <div role="alert" className="mt-2 text-sm text-red-700">Paired browser list failed: {devicesError} <button className="underline" type="button" onClick={refreshDevices}>Retry</button></div> : null}
      {!devicesLoading && !devicesError && devices.length === 0 ? <p className="mt-2 text-sm text-amber-800">No non-revoked paired browser devices are registered to this account.</p> : null}
      {!devicesLoading && !devicesError && devices.length > 0 && !devices.some((device) => !deviceUnavailableReason(device)) ? <p className="mt-2 text-sm text-amber-800">Paired devices exist, but none is currently online and compatible. Device options show their status; retry after its heartbeat or version changes.</p> : null}
      {testJobs.length ? <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th className="p-2">Created</th><th className="p-2">Command</th><th className="p-2">Status</th><th className="p-2">Evidence</th><th className="p-2">Action</th></tr></thead><tbody>
        {testJobs.slice(0, 12).map((job) => <tr key={job.job_id} className="border-t"><td className="p-2">{job.created_at ? new Date(job.created_at).toLocaleTimeString() : '—'}</td><td className="p-2">{job.command}</td><td className="p-2">{job.status}{job.error_code ? ` · ${job.error_code}` : ''}</td><td className="max-w-md p-2"><pre className="max-h-24 overflow-auto whitespace-pre-wrap">{job.evidence ? JSON.stringify(job.evidence, null, 2) : 'Waiting for paired browser'}</pre></td><td className="p-2">{['QUEUED','CLAIMED'].includes(job.status) ? <button className="underline" onClick={() => cancelTaxonomyTestJob(job.job_id).then(() => fetchTaxonomyTestJobs()).then((result) => setTestJobs(result.items || [])).catch((e) => setError(e.message))}>Cancel</button> : '—'}</td></tr>)}
      </tbody></table></div> : null}
    </section>
    <section className="mb-4 grid gap-3 md:grid-cols-3">
      {MARKETS.map((market) => {
        const data = markets.find((item) => item.marketplace === market);
        return <article className="pp-card p-4" key={market}>
          <h2 className="font-semibold">{LABELS[market]}</h2>
          <p className="mt-1 text-sm">Coverage: <strong>{titleCase(data?.coverage_status || 'loading')}</strong></p>
          <p className="mt-1 text-xs text-slate-600">{data?.version_count || 0} current scope(s)</p>
          <p className="mt-1 text-xs text-slate-600">{data?.confirmed_reusable_mapping_count || 0} current operator-confirmed reusable concept mapping(s). Inventory listing assignments are not counted here.</p>
          {(data?.versions || []).map((version) => <div className="mt-3 border-t pt-2 text-xs" key={version.version_id}>
            <p>Version {version.native_version || version.version_id}</p>
            <p>{version.discovered_category_count ?? version.category_count} discovered · {version.branch_count ?? 0} branches · {version.terminal_candidate_count ?? version.terminal_count} terminal claims · {version.verified_publishable_leaf_count ?? 0} verified publishable leaves</p>
            <p>Attribute schemas: {version.schema_complete_count ?? 0} collected with fields · {version.schema_verified_empty_count ?? 0} verified empty · {version.schema_incomplete_count ?? 0} incomplete · {version.schema_uncollected_count ?? 0} uncollected · {version.schema_stale_count ?? 0} stale</p>
            <p>{version.verified_leaf_schema_count ?? 0} verified leaves have nonempty required attributes. An empty/unfetched schema is not treated as verified empty.</p>
            <p>{titleCase(version.verification_status)} · {titleCase(version.completeness_status)} · {titleCase(version.staleness_status)}</p>
            <p className="break-all text-slate-500">Source: {version.source}</p>
          </div>)}
          {market === 'facebook' && !data?.version_count ? <p className="mt-3 text-xs text-amber-800">No supported taxonomy snapshot is registered. Existing form labels are not yet verified category paths.</p> : null}
          {market === 'mercari' && (data?.versions || []).some((version) => version.completeness_status === 'partial') ? <p className="mt-2 text-xs text-amber-800">This is partial source coverage; missing branches are not treated as unavailable or terminal.</p> : null}
        </article>;
      })}
    </section>
    <section className="pp-card mb-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="font-semibold">Inventory cohorts and approved destination readiness</h2>
          <p className="mt-1 text-xs text-slate-600">Only already-approved listings are evaluated for destination publication here. Cohorts use exact source identifiers or exact normalized title/category matches; ambiguous one-word titles stay separate. Candidate text matches never count as confirmed mappings or readiness.</p></div>
        <Button disabled={inventoryBusy} variant="outline" onClick={refreshInventoryReport}>{inventoryBusy ? 'Calculating…' : inventoryReport ? 'Refresh inventory coverage' : 'Load inventory coverage'}</Button>
      </div>
      {inventoryReport ? <>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <p className="rounded border p-2 text-sm">{inventoryReport.inventory_count} account listings · {inventoryReport.active_inventory_count} active inventory · {inventoryReport.approved_ready_inventory_count} approved</p>
          <p className="rounded border p-2 text-sm">{inventoryReport.cohort_count} reusable product cohorts</p>
          {MARKETS.map((market) => <p className="rounded border p-2 text-sm" key={market}>{LABELS[market]}: {inventoryReport.approved_destination_publishable_counts?.[market] || 0} approved and destination-publishable · {inventoryReport.confirmed_mapped_inventory_counts?.[market] || 0} inventory rows covered by confirmed mappings</p>)}
        </div>
        <div className="mt-3 grid gap-3 lg:grid-cols-3">{MARKETS.map((market) => <article className="rounded border p-3" key={market}><h3 className="text-sm font-semibold">{LABELS[market]} blockers</h3>{inventoryReport.top_blockers?.[market]?.length ? <ul className="mt-1 list-disc pl-5 text-xs">{inventoryReport.top_blockers[market].map((row) => <li key={row.reason}>{row.reason} <span className="text-slate-500">({row.listing_count})</span></li>)}</ul> : <p className="mt-1 text-xs text-slate-600">No blockers reported for active inventory.</p>}</article>)}</div>
        <h3 className="mt-4 text-sm font-semibold">Largest exact-match cohorts</h3>
        <div className="mt-2 max-h-[36rem] overflow-auto rounded border">{(inventoryReport.top_cohorts || []).map((cohort) => <article className="border-b p-3 text-sm last:border-b-0" key={cohort.cohort_key}>
          <div className="flex flex-wrap items-center justify-between gap-2"><div><p className="font-medium">{cohort.label} <span className="text-slate-500">({cohort.listing_count})</span></p><p className="text-xs text-slate-600">Matched by {cohort.identity_basis} · category hints: {Object.keys(cohort.category_hints || {}).join(', ') || 'none'} · confirmed: {(cohort.confirmed_marketplaces || []).join(', ') || 'none'}</p></div>
            <div className="flex gap-3">{cohort.listing_ids?.[0] ? <a className="text-xs font-semibold text-blue-700 underline" href={`/listings/${cohort.listing_ids[0]}`}>Review listing #{cohort.listing_ids[0]}</a> : null}<Button size="sm" variant="outline" disabled={!!candidateBusy} onClick={() => analyzeCohort(cohort)}>{candidateBusy === cohort.cohort_key ? 'Matching…' : 'Suggest categories'}</Button></div>
          </div>
          {candidateReport?.cohort?.cohort_key === cohort.cohort_key ? <div className="mt-3 grid gap-2 lg:grid-cols-3">{candidateReport.marketplaces?.map((market) => <section className="rounded border bg-slate-50 p-2" key={market.marketplace}><h4 className="text-xs font-semibold capitalize">{market.marketplace} · suggestions only</h4>{market.candidates?.length ? market.candidates.map((candidate) => <div className="mt-2 border-t pt-2 text-xs" key={`${candidate.registry_version_id}:${candidate.category_key}`}><p className="font-medium">{candidate.full_path}</p><p className="text-slate-600">ID {candidate.category_id || 'not provided'} · {candidate.terminal_status} · {candidate.publishability_status} · {candidate.verification_status} · {candidate.staleness_status} · auto {candidate.automated_selection_eligible ? 'eligible' : 'blocked'}</p><p className="text-slate-600">Schema {candidate.required_aspects === null ? 'uncollected' : candidate.required_aspects.length ? `${candidate.required_aspects.length} required fields captured` : 'no nonempty required list; verify schema evidence'}</p></div>) : <p className="mt-2 text-xs text-slate-600">No registry candidate. Seller-form mapping remains unresolved.</p>}</section>)}</div> : null}
        </article>)}{!inventoryReport.top_cohorts?.length ? <p className="p-3 text-sm text-slate-600">No active inventory cohorts found.</p> : null}</div>
      </> : <p className="mt-3 text-sm text-slate-600">{inventoryBusy ? 'Calculating tenant-scoped readiness…' : 'Inventory coverage has not loaded.'}</p>}
    </section>
    <section className="pp-card p-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h2 className="font-semibold">Browse registry records</h2><p className="text-xs text-slate-600">Category selection eligibility is separate from publication readiness.</p></div>
        <div className="flex flex-wrap gap-2">
          {marketplace === 'ebay' ? <Button disabled={busy} onClick={refreshEbayProvider}>Refresh official eBay taxonomy</Button> : null}
          <Button disabled={busy} onClick={syncLocalSnapshots}>Synchronize local snapshots</Button>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <select aria-label="Marketplace taxonomy" className="rounded border px-3 py-2" value={marketplace} onChange={(event) => setMarketplace(event.target.value)}>
          {MARKETS.map((market) => <option value={market} key={market}>{LABELS[market]}</option>)}
        </select>
        <input aria-label="Search category names or paths" className="min-w-48 flex-1 rounded border px-3 py-2" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && search()} placeholder="Search category or path" />
        <Button disabled={busy} onClick={search}>Search</Button>
      </div>
      {selectedStatus?.versions?.length ? <p className="mt-2 text-xs text-slate-600">Current taxonomy scope: {selectedStatus.versions.map((version) => version.scope_key).join(' · ')}</p> : null}
      <div className="mt-3 max-h-[36rem] overflow-auto rounded border">
        {items.map((item) => <article className="border-b p-3 last:border-b-0" key={`${item.version_id}:${item.category_key}`}>
          <h3 className="text-sm font-medium">{item.full_path}</h3>
          <p className="mt-1 text-xs text-slate-600">ID {item.category_id || 'not supplied'} · {titleCase(item.terminal_status)} · {titleCase(item.publishability_status)}</p>
          <p className="text-xs text-slate-600">Evidence: {titleCase(item.verification_status)} · {titleCase(item.staleness_status)} · Auto selection {item.automated_selection_eligible ? 'eligible' : 'blocked'}</p>
          <p className="text-xs text-slate-500">{item.data_source} · version {item.version_id}</p>
          {item.required_attributes?.length ? <p className="mt-1 text-xs">Required: {item.required_attributes.map((aspect) => aspect.localizedAspectName || aspect.name || 'Unnamed').join(', ')}</p> : null}
          {item.recommended_attributes?.length ? <p className="text-xs">Recommended: {item.recommended_attributes.map((aspect) => aspect.localizedAspectName || aspect.name || 'Unnamed').join(', ')}</p> : null}
        </article>)}
        {!items.length ? <p className="p-4 text-sm text-slate-600">No records in this current registry scope.</p> : null}
      </div>
    </section>
  </AppShell>;
}
