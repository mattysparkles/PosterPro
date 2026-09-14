import Button from '../ui/button';

const ONLINE_WINDOW_MS = 120000;

function deviceLabel(device) {
  return `${device.browser || 'Browser'} · v${device.extension_version || 'unknown'}`;
}

function lastSeen(device) {
  if (!device.last_seen_at) return 'Never connected';
  const date = new Date(device.last_seen_at);
  return Number.isNaN(date.getTime()) ? 'Last seen time unavailable' : `Last seen ${date.toLocaleString()}`;
}

function versionLessThan(installed, required) {
  const left = String(installed || '').split('.').map((part) => Number.parseInt(part, 10) || 0);
  const right = String(required || '').split('.').map((part) => Number.parseInt(part, 10) || 0);
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    if ((left[index] || 0) !== (right[index] || 0)) return (left[index] || 0) < (right[index] || 0);
  }
  return false;
}

export default function ExtensionVersionStatus({ state, currentDeviceId = null, detected = false, detectedVersion = '', showAction = true, compact = false }) {
  const devices = (state?.devices || []).filter((device) => !device.revoked);
  const minimum = state?.minimum_version || devices[0]?.minimum_version || 'unknown';
  const current = state?.current_version || devices[0]?.current_version || minimum;
  const now = Date.now();
  const recent = devices.filter((device) => device.last_seen_at && now - new Date(device.last_seen_at).getTime() < ONLINE_WINDOW_MS)
    .sort((a, b) => new Date(b.last_seen_at) - new Date(a.last_seen_at));
  const matchedCurrentDevice = recent.find((device) => device.id === currentDeviceId) || null;
  const currentDevice = matchedCurrentDevice || (!detected ? recent[0] : null);
  const otherRecent = recent.filter((device) => device.id !== currentDevice?.id);
  const stale = devices.filter((device) => !recent.some((online) => online.id === device.id));
  const currentNeedsUpdate = Boolean(currentDevice?.update_required || (detected && versionLessThan(detectedVersion, minimum)));

  return (
    <section className={`rounded-2xl border ${currentNeedsUpdate ? 'border-amber-300 bg-amber-50' : 'border-slate-200 bg-white'} p-4 sm:p-5`} aria-label="PosterPro browser connection status">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-950">{currentNeedsUpdate ? 'PosterPro extension update required' : 'Browser connection'}</h2>
          <p className="mt-1 text-sm text-slate-700">Latest version {current} · minimum compatible {minimum}</p>
        </div>
        {showAction && currentNeedsUpdate ? <Button href="/api/browser-extension/download" download>Update extension</Button> : showAction && detected && !currentDevice ? <Button href="/onboarding">Connect this browser</Button> : showAction && !currentDevice && !detected ? <Button href="/onboarding">Install &amp; connect</Button> : null}
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        <div className={`rounded-xl border p-3 ${currentDevice && !currentNeedsUpdate ? 'border-green-200 bg-green-50' : 'border-slate-200 bg-slate-50'}`}>
          <p className="text-sm font-semibold text-slate-950">{matchedCurrentDevice ? 'This browser' : 'Most recently connected browser'}</p>
          <p className="mt-1 text-sm text-slate-700">{currentDevice ? deviceLabel(currentDevice) : detected ? `Extension detected · v${detectedVersion || 'unknown'}` : 'No recent connection'}</p>
          <p className="mt-1 text-xs text-slate-600">{currentDevice ? lastSeen(currentDevice) : detected ? 'This browser has not connected to your PosterPro account yet.' : 'Open PosterPro in Chrome or Edge with the extension installed.'}</p>
          {currentNeedsUpdate ? <p className="mt-2 text-sm font-semibold text-amber-900">Update required before using this browser.</p> : currentDevice ? <p className="mt-2 text-sm font-semibold text-green-900">Connected now</p> : detected ? <p className="mt-2 text-sm font-semibold text-blue-900">Ready to connect</p> : null}
        </div>
        {otherRecent.length ? <div className="rounded-xl border border-blue-200 bg-blue-50 p-3"><p className="text-sm font-semibold text-slate-950">Other connected browsers</p><ul className="mt-1 space-y-1 text-sm text-slate-700">{otherRecent.map((device) => <li key={device.id}>{deviceLabel(device)} · online</li>)}</ul></div> : null}
        {stale.length ? <div className="rounded-xl border border-slate-300 bg-slate-100 p-3"><p className="text-sm font-semibold text-slate-950">Stale connections</p><ul className="mt-1 space-y-1 text-sm text-slate-700">{stale.map((device) => <li key={device.id}>{deviceLabel(device)} · stale · {lastSeen(device)}</li>)}</ul><p className="mt-2 text-xs text-slate-600">Old connections are kept for history and are not treated as current.</p></div> : null}
      </div>
      {currentNeedsUpdate ? <details className="mt-3 rounded-xl border border-amber-200 bg-white p-3"><summary className="cursor-pointer text-sm font-semibold text-amber-950">How to update and keep this browser connected</summary><ol className="mt-2 list-decimal space-y-1 pl-5 text-sm leading-6 text-slate-700"><li>Download the current extension ZIP and extract it.</li><li>Replace the files in the same folder Chrome or Edge already loaded.</li><li>Open the browser Extensions page and click Reload for PosterPro.</li><li>Do not remove the extension or clear its data. Return here; PosterPro checks the connection automatically.</li></ol><Button className="mt-3" href="/api/browser-extension/download" download variant="outline">Download current extension</Button></details> : null}
      {!compact ? <p className="mt-3 text-sm text-slate-600">PosterPro works with marketplace accounts already signed in to this browser. Your passwords stay in your browser.</p> : null}
    </section>
  );
}
