# PosterPro Browser Extension

The extension is a background transport for PosterPro-assisted marketplace jobs. After installation and one-time browser authorization, heartbeat, job polling, claiming, lease renewal, and safe form preparation run automatically. Routine work does not require opening the popup or manually claiming jobs.

## Install once

1. Download the extension ZIP from PosterPro Settings → Marketplaces → Browser Automation.
2. Unpack the ZIP and load the unpacked folder from Chrome or Edge's extension manager.
3. Sign into PosterPro in the same browser profile and open Settings → Browser Automation.
4. When PosterPro detects the extension, click **Authorize this browser** once. The short-lived pairing code is delivered directly to the extension; the device token never returns to the page.

The five-minute pairing-code entry in the extension popup remains available for recovery, unsupported browser contexts, and troubleshooting.

Marketplace passwords, cookies, and session tokens stay in the browser profile. PosterPro receives only safe connection/job state and the scoped device credential is stored in extension-local storage. Jobs remain tenant-scoped and are protected by server-side leases.

The popup is limited to connection status, task summary, pause/resume, diagnostics, and a link back to PosterPro. Marketplace actions stop at PosterPro's operator-review boundary; the extension does not automatically submit arbitrary live listings.
