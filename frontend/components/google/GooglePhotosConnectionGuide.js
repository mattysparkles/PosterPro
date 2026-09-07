import Button from '../ui/button';
import StatusPill from '../ui/status-pill';

function Step({ number, title, description, action, done = false, tone = 'default' }) {
  const pillStatus = done ? 'success' : tone === 'warning' ? 'warning' : 'default';
  return (
    <div className="rounded-[20px] border border-[var(--pp-border)] bg-white p-4 shadow-[0_12px_28px_rgba(16,24,40,0.06)]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill status={pillStatus} label={done ? 'Done' : `Step ${number}`} />
            <p className="text-sm font-semibold text-[var(--pp-text)]">{title}</p>
          </div>
          {description ? <p className="mt-2 text-sm leading-6 text-[var(--pp-muted)]">{description}</p> : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  );
}

export default function GooglePhotosConnectionGuide({
  connected = false,
  accountLabel,
  albumLabel = 'PosterPro',
  albumId,
  connectionState,
  redirectUri,
  connectUrl,
  apiKeysUrl,
  slateUrl,
  onRefresh,
  onSaveConfig,
  onOpenGooglePhotos,
  onStartLogin,
  missingConfig = false,
  compact = false,
}) {
  return (
    <section className="rounded-[28px] border border-[var(--pp-border)] bg-[linear-gradient(135deg,#fffdf7_0%,#f7faff_48%,#ffffff_100%)] p-5 shadow-[0_20px_50px_rgba(16,24,40,0.08)]">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="max-w-3xl">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--pp-muted)]">Google Photos setup</p>
          <h2 className="mt-2 font-[var(--pp-heading-font)] text-[1.6rem] font-semibold tracking-[-0.04em] text-[var(--pp-text)]">
            {connected ? 'Google Photos is connected' : 'Connect Google Photos step by step'}
          </h2>
          <p className="mt-3 text-sm leading-7 text-[var(--pp-muted)]">
            PosterPro should not leave you guessing. This flow takes you to the exact page for each step: configure OAuth, authorize Google, verify the
            account, then open Slate and test an upload.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill status={connected ? 'success' : 'warning'} label={connected ? 'Connected' : 'Not connected'} />
          <StatusPill status={missingConfig ? 'warning' : 'default'} label={missingConfig ? 'OAuth settings missing' : 'OAuth configured'} />
          {connectionState ? <StatusPill status={connected ? 'success' : 'default'} label={String(connectionState).replaceAll('_', ' ')} /> : null}
        </div>
      </div>

      <div className={`mt-5 grid gap-3 ${compact ? 'xl:grid-cols-2' : 'xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]'}`}>
        <div className="space-y-3">
          {redirectUri ? (
            <div className="rounded-[20px] border border-[#dbe4ff] bg-[#f6f8ff] p-4">
              <p className="text-sm font-semibold text-[var(--pp-text)]">Google Cloud Console redirect URI</p>
              <p className="mt-2 text-sm leading-6 text-[var(--pp-muted)]">
                Register this exact redirect URI in your Google OAuth client. Do not enter the site root.
              </p>
              <code className="mt-3 block overflow-x-auto rounded-[14px] border border-[#d5def7] bg-white px-3 py-2 text-sm text-[#1d4ed8]">
                {redirectUri}
              </code>
              <p className="mt-2 text-xs leading-5 text-[var(--pp-muted)]">
                OAuth client type: Web application. If Google asks for authorized JavaScript origins, use{' '}
                <code className="rounded bg-white px-1.5 py-0.5 text-[0.82em] text-[#1d4ed8]">https://posterpro.sparkleserver.site</code>{' '}
                for production.
              </p>
            </div>
          ) : null}
          <Step
            number={1}
            title="Open Google OAuth settings"
            description="If the Client ID, Client Secret, or redirect URI are missing, use the exact API-keys section where PosterPro stores them."
            action={(
              <Button href={apiKeysUrl || '/settings?tab=api-keys#google-photos-oauth'} variant="secondary">
                Open API keys
              </Button>
            )}
            done={!missingConfig}
          />
          <Step
            number={2}
            title="Authorize the Google account"
            description="PosterPro sends you to Google’s consent screen with the right scope. Pick the account that owns or can access the PosterPro album."
            action={(
              <Button onClick={onStartLogin} variant="default">
                {connected ? 'Reconnect Google Photos' : 'Start Google login'}
              </Button>
            )}
            done={connected}
          />
          <Step
            number={3}
            title="Verify the connected account and album"
            description={`Connected account: ${accountLabel || 'Not connected'} · Album: ${albumLabel || 'PosterPro'}${albumId ? ` · Album ID: ${albumId}` : ''}`}
            action={onRefresh ? (
              <Button onClick={onRefresh} variant="outline">
                Refresh status
              </Button>
            ) : null}
            done={connected}
          />
        </div>

        <div className="space-y-3">
          <Step
            number={4}
            title="Open Slate and generate a test upload"
            description="This proves the Slate image is created, saved on the server, and sent to Google Photos with the right album target."
            action={(
              <Button href={slateUrl || '/intake/slate'} variant="secondary">
                Open Slate
              </Button>
            )}
            done={connected}
          />
          <Step
            number={5}
            title="Inspect the saved Slate image"
            description="The generated Slate should always have a server-side image URL or download path so you can recover it even if Google upload fails."
            action={onOpenGooglePhotos ? (
              <Button onClick={onOpenGooglePhotos} variant="outline">
                Open Google destination
              </Button>
            ) : (
              <Button href={slateUrl || '/intake/slate'} variant="outline">
                View Slate tools
              </Button>
            )}
            done={connected}
          />
          {onSaveConfig ? (
            <div className="rounded-[20px] border border-dashed border-[var(--pp-border)] bg-[var(--pp-surface-strong)] p-4">
              <p className="text-sm font-semibold text-[var(--pp-text)]">Need to save OAuth settings first?</p>
              <p className="mt-2 text-sm leading-6 text-[var(--pp-muted)]">
                Save the Google OAuth Client ID, Client Secret, and redirect URI, then return here and click Start Google login.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button onClick={onSaveConfig} variant="secondary">
                  Save Google settings
                </Button>
                <Button href={apiKeysUrl || '/settings?tab=api-keys#google-photos-oauth'} variant="outline">
                  Open exact fields
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
