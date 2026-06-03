import Link from 'next/link';
import { useRouter } from 'next/router';
import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import StatusPill from '../components/ui/status-pill';
import {
  buildBridgeDesktopFrameUrl,
  buildBridgeDesktopWebsocketUrl,
  fetchBridgeConnectSession,
  sendBridgeDesktopAction,
  startBridgeAccountConnectSession,
} from '../lib/api';

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'canceled']);
const MARKETPLACE_LABELS = {
  facebook: 'Facebook Marketplace',
  mercari: 'Mercari',
  poshmark: 'Poshmark',
  etsy: 'Etsy',
  whatnot: 'Whatnot',
};

function statusTone(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'completed') return 'success';
  if (normalized === 'failed' || normalized === 'canceled') return 'danger';
  if (normalized === 'waiting_for_login') return 'warning';
  return 'info';
}

function terminalSessionMessage(status, marketplaceLabel) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'completed') {
    return `${marketplaceLabel} session captured. Start a fresh session only if you need to reconnect or recapture browser state.`;
  }
  if (normalized === 'failed') {
    return `This ${marketplaceLabel} connect session failed. Start a fresh session to reopen the bridge browser and try again.`;
  }
  if (normalized === 'canceled') {
    return `This ${marketplaceLabel} connect session was canceled. Start a fresh session to reopen the bridge browser and continue.`;
  }
  return `This ${marketplaceLabel} connect session is no longer active. Start a fresh session to continue.`;
}

export default function BridgeDesktopPage() {
  const router = useRouter();
  const viewerRef = useRef(null);
  const rfbRef = useRef(null);
  const startedRef = useRef(false);
  const [session, setSession] = useState(null);
  const [desktopAccess, setDesktopAccess] = useState(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const [viewerState, setViewerState] = useState('idle');
  const [viewerMessage, setViewerMessage] = useState('Preparing the bridge desktop session.');
  const [frameVersion, setFrameVersion] = useState(0);
  const [desktopText, setDesktopText] = useState('');
  const [runningDesktopAction, setRunningDesktopAction] = useState(false);
  const [sessionLoginHandle, setSessionLoginHandle] = useState('');

  const marketplace = typeof router.query.marketplace === 'string' ? router.query.marketplace : 'facebook';
  const accountKey = typeof router.query.accountKey === 'string' ? router.query.accountKey : '';
  const connectSessionId = typeof router.query.connectSessionId === 'string' ? router.query.connectSessionId : '';
  const marketplaceLabel = MARKETPLACE_LABELS[marketplace] || marketplace;
  const displayName = typeof router.query.displayName === 'string' ? router.query.displayName : marketplaceLabel;
  const loginHandle = typeof router.query.loginHandle === 'string' ? router.query.loginHandle : '';
  const notes = typeof router.query.notes === 'string' ? router.query.notes : '';

  useEffect(() => {
    setSessionLoginHandle(loginHandle || '');
  }, [loginHandle]);

  const beginConnectSession = async () => {
    if (!accountKey) {
      const message = `Missing ${marketplaceLabel} bridge account key.`;
      setError(message);
      toast.error(message);
      return;
    }

    setStarting(true);
    setError('');

    try {
      const requestedLoginHandle = sessionLoginHandle.trim();
      const response = await startBridgeAccountConnectSession(marketplace, accountKey, {
        display_name: displayName || marketplaceLabel,
        login_handle: requestedLoginHandle || undefined,
        notes: notes || '',
        provider_enabled: false,
        browser_enabled: true,
        expires_at: null,
        wait_timeout_seconds: 600,
      });
      setSession(response);
      setDesktopAccess(response.desktop_access || null);
      setFrameVersion(Date.now());
      const nextQuery = {
        ...router.query,
        connectSessionId: response.connect_session_id,
      };
      if (requestedLoginHandle) {
        nextQuery.loginHandle = requestedLoginHandle;
      } else {
        delete nextQuery.loginHandle;
      }
      await router.replace(
        {
          pathname: router.pathname,
          query: nextQuery,
        },
        undefined,
        { shallow: true },
      );
    } catch (requestError) {
      const message = requestError.message || `Could not start the ${marketplaceLabel} connect session.`;
      setError(message);
      toast.error(message);
    } finally {
      setStarting(false);
    }
  };

  useEffect(() => {
    if (!router.isReady) return;
    if (connectSessionId) return;
    if (!accountKey || startedRef.current) return;

    startedRef.current = true;
    beginConnectSession();
  }, [accountKey, connectSessionId, router.isReady]);

  useEffect(() => {
    if (!connectSessionId) return undefined;

    let cancelled = false;
    let timeoutId;

    const load = async () => {
      let nextStatus = '';
      try {
        const nextSession = await fetchBridgeConnectSession(connectSessionId);
        if (cancelled) return;
        nextStatus = String(nextSession.status || '').toLowerCase();
        setSession(nextSession);
        if (nextSession.desktop_access && !TERMINAL_STATUSES.has(nextStatus)) {
          setDesktopAccess(nextSession.desktop_access);
        } else {
          setDesktopAccess(null);
        }
        if (nextStatus === 'completed') {
          localStorage.setItem(
            `posterpro-marketplace-connect-complete:${String(nextSession.marketplace || marketplace).toLowerCase()}`,
            JSON.stringify({
              marketplace: String(nextSession.marketplace || marketplace).toLowerCase(),
              accountKey: nextSession.account_key,
              completedAt: Date.now(),
            }),
          );
        }
        if (nextStatus === 'failed' && nextSession.error) {
          setError(nextSession.error);
        }
      } catch (requestError) {
        if (!cancelled) {
          setError(requestError.message || `Could not refresh the ${marketplaceLabel} connect session.`);
        }
      } finally {
        if (!cancelled && !TERMINAL_STATUSES.has(nextStatus)) {
          timeoutId = window.setTimeout(load, 2000);
        }
      }
    };

    load();

    return () => {
      cancelled = true;
      if (timeoutId) window.clearTimeout(timeoutId);
    };
  }, [connectSessionId, marketplace, marketplaceLabel]);

  useEffect(() => {
    if (!desktopAccess?.token || !desktopAccess?.websocket_path || !viewerRef.current) return undefined;

    let disposed = false;

    const connectViewer = async () => {
      try {
        const novncModule = await import('@novnc/novnc');
        if (disposed || !viewerRef.current) return;
        const RFB = novncModule.default;
        const url = buildBridgeDesktopWebsocketUrl(desktopAccess.websocket_path, desktopAccess.token);
        const rfb = new RFB(viewerRef.current, url);
        rfbRef.current = rfb;
        rfb.scaleViewport = true;
        rfb.resizeSession = true;
        rfb.viewOnly = false;
        rfb.focusOnClick = true;
        setViewerState('connecting');
        setViewerMessage('Connecting to the bridge desktop.');
        rfb.addEventListener('connect', () => {
          setViewerState('connected');
          setViewerMessage(`Bridge desktop connected. Complete ${marketplaceLabel} login and any MFA here.`);
        });
        rfb.addEventListener('disconnect', (event) => {
          setViewerState('disconnected');
          setViewerMessage(
            event.detail?.clean
              ? 'Bridge desktop disconnected.'
              : 'Bridge desktop disconnected unexpectedly. Refresh the page if the connect session is still active.',
          );
        });
        rfb.addEventListener('credentialsrequired', () => {
          setViewerState('waiting');
          setViewerMessage('Bridge desktop requested VNC credentials unexpectedly.');
        });
      } catch (viewerError) {
        setViewerState('error');
        setViewerMessage(viewerError.message || 'Could not load the in-browser bridge desktop.');
      }
    };

    connectViewer();

    return () => {
      disposed = true;
      if (rfbRef.current) {
        try {
          rfbRef.current.disconnect();
        } catch (disconnectError) {
          console.error(disconnectError);
        }
        rfbRef.current = null;
      }
    };
  }, [desktopAccess?.token, desktopAccess?.websocket_path]);

  const sessionStatus = String(session?.status || (starting ? 'starting' : 'idle')).toLowerCase();
  const sessionIsTerminal = TERMINAL_STATUSES.has(sessionStatus);
  const returnHref = `/settings?tab=marketplaces&marketplace=${encodeURIComponent(marketplace)}`;
  const desktopFrameUrl = connectSessionId ? buildBridgeDesktopFrameUrl(connectSessionId, frameVersion) : '';
  const canRestartSession = Boolean(accountKey) && !starting;
  const desktopControlsEnabled = Boolean(connectSessionId) && !sessionIsTerminal;
  const terminalMessage = terminalSessionMessage(sessionStatus, marketplaceLabel);

  useEffect(() => {
    if (!sessionIsTerminal) return;
    setViewerState(sessionStatus);
    setViewerMessage(terminalMessage);
  }, [sessionIsTerminal, sessionStatus, terminalMessage]);

  useEffect(() => {
    if (!connectSessionId) return undefined;
    if (sessionIsTerminal) return undefined;

    refreshDesktopFrame();
    const intervalId = window.setInterval(() => {
      refreshDesktopFrame();
    }, 4000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [connectSessionId, sessionIsTerminal, sessionStatus]);

  const refreshDesktopFrame = () => {
    setFrameVersion(Date.now());
  };

  const runDesktopAction = async (action, payload) => {
    if (!desktopControlsEnabled) return;
    setRunningDesktopAction(true);
    try {
      await sendBridgeDesktopAction(connectSessionId, action, payload);
      window.setTimeout(() => {
        refreshDesktopFrame();
      }, 300);
    } catch (requestError) {
      const message = requestError.message || 'Desktop action failed.';
      setError(message);
      if (message.toLowerCase().includes('no longer active')) {
        setViewerMessage(terminalSessionMessage(sessionStatus, marketplaceLabel));
      }
      toast.error(message);
    } finally {
      setRunningDesktopAction(false);
    }
  };

  const clickDesktopFrame = async (event) => {
    if (!connectSessionId) return;
    const image = event.currentTarget;
    const rect = image.getBoundingClientRect();
    if (!rect.width || !rect.height || !image.naturalWidth || !image.naturalHeight) return;
    const relativeX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
    const relativeY = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
    const x = Math.round((relativeX / rect.width) * image.naturalWidth);
    const y = Math.round((relativeY / rect.height) * image.naturalHeight);
    await runDesktopAction('click', { x, y });
  };

  const typeDesktopText = async () => {
    if (!desktopText.trim()) {
      toast.error('Enter text to send to the bridge desktop first.');
      return;
    }
    await runDesktopAction('type', { text: desktopText });
  };

  const clearFocusedField = async () => {
    if (!connectSessionId) return;
    setRunningDesktopAction(true);
    try {
      await sendBridgeDesktopAction(connectSessionId, 'key', { key: 'ctrl+a' });
      await sendBridgeDesktopAction(connectSessionId, 'key', { key: 'BackSpace' });
      window.setTimeout(() => {
        refreshDesktopFrame();
      }, 300);
    } catch (requestError) {
      toast.error(requestError.message || 'Could not clear the focused field.');
    } finally {
      setRunningDesktopAction(false);
    }
  };

  return (
    <AppShell title={`${marketplaceLabel} Connect Workspace`} contentWidth="wide">
      <div className="space-y-6">
        <PageHeader
          eyebrow="Automation Bridge"
          title={`${marketplaceLabel} connect workspace`}
          description={`PosterPro launches the bridge browser here so the operator can complete ${marketplaceLabel} login and MFA without leaving the product.`}
          actions={(
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={() => window.location.reload()}>
                Refresh status
              </Button>
              <Link href={returnHref}>
                <Button type="button" variant="outline">
                  Return to settings
                </Button>
              </Link>
            </div>
          )}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.7fr)_minmax(320px,0.9fr)]">
          <SectionPanel
            title="Live bridge desktop"
            description={`Use this desktop surface to sign into ${marketplaceLabel} on the bridge-host Chromium session.`}
          >
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill status={statusTone(sessionStatus)} label={sessionStatus.replace(/_/g, ' ')} />
                <StatusPill status={statusTone(viewerState)} label={viewerState} />
                {TERMINAL_STATUSES.has(sessionStatus) ? (
                  <Button type="button" variant="outline" onClick={beginConnectSession} disabled={!canRestartSession}>
                    Start fresh session
                  </Button>
                ) : null}
              </div>
              <div className="rounded-[18px] border border-[#d0d5dd] bg-[#0f172a] p-3">
                <div
                  ref={viewerRef}
                  className="min-h-[620px] w-full overflow-hidden rounded-[14px] bg-[#020617]"
                />
              </div>
              <p className="text-sm text-[#475467]">{viewerMessage}</p>
              <div className="rounded-[18px] border border-[#dbe7ff] bg-[#f7faff] p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-[#101828]">Fallback login surface</p>
                    <p className="text-sm text-[#475467]">
                      If the live desktop above stays black, use the screenshot below. Click inside it to focus fields,
                      then use the controls to type and submit.
                    </p>
                  </div>
                  <Button type="button" variant="outline" onClick={refreshDesktopFrame} disabled={!desktopControlsEnabled}>
                    Refresh screenshot
                  </Button>
                </div>
                <div className="mt-4 overflow-hidden rounded-[16px] border border-[#d0d5dd] bg-[#0f172a]">
                  {sessionIsTerminal ? (
                    <div className="flex min-h-[420px] items-center justify-center px-6 text-center text-sm text-[#cbd5e1]">
                      {terminalMessage}
                    </div>
                  ) : connectSessionId ? (
                    <img
                      src={desktopFrameUrl}
                      alt="Bridge desktop screenshot"
                      className="block w-full cursor-crosshair bg-[#020617]"
                      onClick={clickDesktopFrame}
                    />
                  ) : (
                    <div className="flex min-h-[420px] items-center justify-center px-6 text-center text-sm text-[#98a2b3]">
                      {`Start a ${marketplaceLabel} connect session to load the browser screenshot surface.`}
                    </div>
                  )}
                </div>
                <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
                  <input
                    type="text"
                    value={desktopText}
                    onChange={(event) => setDesktopText(event.target.value)}
                    placeholder="Type email, password, or MFA code"
                    disabled={!desktopControlsEnabled}
                    className="h-11 rounded-[12px] border border-[#d0d5dd] bg-white px-4 text-sm text-[#101828] outline-none transition focus:border-[#111827]"
                  />
                  <Button type="button" onClick={typeDesktopText} disabled={runningDesktopAction || !desktopControlsEnabled}>
                    Type text
                  </Button>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={clearFocusedField}
                    disabled={runningDesktopAction || !desktopControlsEnabled}
                  >
                    Clear field
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => runDesktopAction('key', { key: 'Tab' })}
                    disabled={runningDesktopAction || !desktopControlsEnabled}
                  >
                    Tab
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => runDesktopAction('key', { key: 'shift+Tab' })}
                    disabled={runningDesktopAction || !desktopControlsEnabled}
                  >
                    Shift+Tab
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => runDesktopAction('key', { key: 'Return' })}
                    disabled={runningDesktopAction || !desktopControlsEnabled}
                  >
                    Enter
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => runDesktopAction('key', { key: 'BackSpace' })}
                    disabled={runningDesktopAction || !desktopControlsEnabled}
                  >
                    Backspace
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => runDesktopAction('key', { key: 'Escape' })}
                    disabled={runningDesktopAction || !desktopControlsEnabled}
                  >
                    Escape
                  </Button>
                </div>
              </div>
            </div>
          </SectionPanel>

          <div className="space-y-6">
            <SectionPanel
              title="Connect status"
              description={`This session will remain active while PosterPro waits for ${marketplaceLabel} authentication to complete.`}
            >
              <div className="space-y-3 text-sm text-[#475467]">
                <div className="rounded-[14px] border border-[#d0d5dd] bg-white p-4">
                  <p><span className="font-semibold text-[#101828]">Account key:</span> {accountKey || session?.account_key || 'Not set'}</p>
                  <p><span className="font-semibold text-[#101828]">Display name:</span> {displayName || session?.display_name || marketplaceLabel}</p>
                  <div className="mt-3 space-y-2">
                    <label className="text-sm font-medium text-[#101828]">Login handle for this session</label>
                    <input
                      type="text"
                      value={sessionLoginHandle}
                      onChange={(event) => setSessionLoginHandle(event.target.value)}
                      placeholder="onqdirector@gmail.com"
                      className="h-11 w-full rounded-[12px] border border-[#d0d5dd] bg-white px-4 text-sm text-[#101828] outline-none transition focus:border-[#111827]"
                    />
                    <p className="text-xs text-[#667085]">
                      {`PosterPro will pass this to the bridge record for this launch, but the ${marketplaceLabel} login screen will stay operator-editable.`}
                    </p>
                  </div>
                </div>
                <div className="rounded-[14px] border border-[#dbe7ff] bg-[#f7faff] p-4">
                  <p className="font-semibold text-[#101828]">Bridge message</p>
                  <p className="mt-1">{session?.message || (starting ? `Starting the ${marketplaceLabel} connect session.` : 'Waiting for the bridge to report status.')}</p>
                </div>
                {sessionIsTerminal ? (
                  <div className="rounded-[14px] border border-[#fde68a] bg-[#fffbeb] p-4 text-[#92400e]">
                    {terminalMessage}
                  </div>
                ) : null}
                {session?.error || error ? (
                  <div className="rounded-[14px] border border-[#fecaca] bg-[#fef2f2] p-4 text-[#991b1b]">
                    {session?.error || error}
                  </div>
                ) : null}
                {sessionStatus === 'completed' ? (
                  <div className="rounded-[14px] border border-[#c7f9d4] bg-[#ecfdf3] p-4 text-[#027a48]">
                    {`${marketplaceLabel} session captured. Return to Settings to review the saved bridge session and continue with browser-assist posting.`}
                  </div>
                ) : null}
              </div>
            </SectionPanel>

            <SectionPanel
              title="Operator steps"
              description="The bridge session is shared with the live Chromium window running on the automation host."
            >
              <div className="space-y-2 text-sm text-[#475467]">
                <p>{`1. Wait for ${marketplaceLabel} to render inside the bridge desktop.`}</p>
                <p>{`2. Sign in to the correct ${marketplaceLabel} account and finish any checkpoint or MFA challenge.`}</p>
                <p>3. Leave the browser open until PosterPro reports that the session was captured successfully.</p>
                <p>4. Return to Settings and continue with browser-assist posting.</p>
              </div>
            </SectionPanel>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
