import Button from '../ui/button';

export default function ExtensionVersionStatus({ state, compact = false }) {
  const devices = (state?.devices || []).filter((device) => !device.revoked);
  const minimum = state?.minimum_version || devices[0]?.minimum_version || 'unknown';
  const current = state?.current_version || devices[0]?.current_version || minimum;
  const outdated = devices.filter((device) => device.update_required);
  const online = devices.filter((device) => device.last_seen_at && Date.now() - new Date(device.last_seen_at).getTime() < 120000);
  const download = <a className="inline-flex min-h-9 items-center rounded-lg bg-blue-700 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-800" href="/api/browser-extension/download" download>Download current extension v{current}</a>;

  return (
    <section className={`rounded-xl border ${outdated.length ? 'border-amber-300 bg-amber-50' : 'border-slate-200 bg-white'} p-4`} aria-label="PosterPro extension version status">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold text-slate-950">{outdated.length ? 'POSTERPRO EXTENSION UPDATE REQUIRED' : 'PosterPro browser connection'}</h3>
          <p className="mt-1 text-sm text-slate-700">Installed device(s): {devices.length ? devices.map((device) => `${device.name || `Device ${device.id}`} · ${device.browser || 'browser unknown'} · v${device.extension_version || 'unknown'}`).join('; ') : 'none paired'}</p>
          <p className="mt-1 text-xs text-slate-600">Minimum compatible version: {minimum} · Latest version: {current} · Last seen: {online.length ? online.map((device) => new Date(device.last_seen_at).toLocaleString()).join('; ') : devices.length ? 'offline' : 'never connected'}</p>
        </div>
        {!compact ? download : null}
      </div>
      {outdated.length ? <div className="mt-3 rounded-lg border border-amber-200 bg-white p-3 text-sm text-amber-950">
        <p className="font-semibold">Update in place to preserve this browser’s pairing.</p>
        <ol className="mt-2 list-decimal space-y-1 pl-5">
          <li>{compact ? 'Download the current extension ZIP from Settings → Browser Automation.' : 'Click Download current extension above and extract the ZIP.'}</li>
          <li>Copy the files inside the ZIP’s <span className="font-mono">posterpro-extension</span> folder into the same extracted folder Chrome/Edge already has loaded, replacing the old files.</li>
          <li>Open <span className="font-mono">chrome://extensions</span> or <span className="font-mono">edge://extensions</span> and click Reload on PosterPro.</li>
          <li>Return to PosterPro. Do not click Remove, clear extension storage, or Unpair; re-pair only if PosterPro still shows offline after reload.</li>
        </ol>
        {compact ? download : null}
      </div> : devices.length ? <p className="mt-2 text-xs text-slate-600">An unpacked update loaded from the same folder normally keeps its pairing. Routine popup use is not required.</p> : <p className="mt-2 text-xs text-slate-600">Install once, then authorize this browser from Settings. Marketplace passwords and sessions stay in this browser.</p>}
      {!devices.length ? <div className="mt-3">{download}<Button className="ml-2" variant="outline" href="/settings?tab=marketplaces">Open Settings</Button></div> : null}
    </section>
  );
}
