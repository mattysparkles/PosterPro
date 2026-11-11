# PosterPro Browser Extension

The extension is a background transport for PosterPro-assisted marketplace jobs. After installation and one-time browser authorization, heartbeat, job polling, claiming, lease renewal, and safe form preparation run automatically. Routine work does not require opening the popup or manually claiming jobs.

## Install once

1. Download the extension ZIP from PosterPro Settings → Marketplaces → Browser Automation.
2. Unpack the ZIP and load the unpacked folder from Chrome or Edge's extension manager.
3. Sign into PosterPro in the same browser profile and open Settings → Browser Automation.
4. When PosterPro detects the extension, click **Authorize this browser** once. The short-lived pairing code is delivered directly to the extension; the device token never returns to the page.

The five-minute pairing-code entry in the extension popup remains available for recovery, unsupported browser contexts, and troubleshooting.

Marketplace passwords, cookies, and session tokens stay in the browser profile. PosterPro receives only safe connection/job state and the scoped device credential is stored in extension-local storage. Jobs remain tenant-scoped and are protected by server-side leases.

The popup is limited to connection status, task summary, bridge pause/resume, diagnostics, and a link back to PosterPro. A CREATE job exists only after an operator explicitly queues that listing from PosterPro. Facebook and Mercari CREATE jobs submit only after all required fields are verified and record completion only when a resulting live item identity/URL is visible; heartbeat or field-fill alone is never treated as publication.
# Updating the unpacked extension

For a release, replace the files in the same folder Chrome originally loaded and
use **Reload** for the existing PosterPro entry at `chrome://extensions`; do not
click **Load unpacked** again for every release. Reloading the existing entry
retains the pairing token; a true uninstall/reinstall requires one-time pairing.

Version 0.3.48 adds read-only Facebook form-readiness snapshots after initial
field fill, immediately before the shipping Update activation, and after the
shipping commit. It records bounded selling-form controls, requested versus
observed values, visible acceptance/validation evidence, media state, Next's
raw disabled/accessibility state, and Next render identity. It does not relax
readiness checks or remove the diagnostic final-Publish guard. The proven
foreground focus lease and one-use trusted Update activation remain in place.
`debugger` permission warning remains broad (**Access the page debugger
backend** and **Read and change all your data on all websites**); runtime policy
still allows only the validated Facebook Update target during an authorized
CREATE. The worker uses a one-use token and fixed move/press/release sequence,
detaches immediately, and refuses a tab already attached to DevTools. Opening
DevTools during attachment can cause Chrome to detach its debugger session.

Version 0.3.52 is the production Facebook CREATE build for the existing guarded
publish workflow. It retains the validated focus lease, current-target
re-resolution and trusted Update activation, but no longer applies the 0.3.48
diagnostic stop at the final Publish boundary. The worker only invokes the
submit action for a claimed, operator-authorized CREATE job and records success
only after it observes a resulting remote listing identity.
