# PosterPro Marketplace Assistant

This is a thin Manifest V3 browser-extension scaffold for browser-assisted marketplace workflows.

It can:

- capture the current marketplace tab session
- store the snapshot locally in the extension
- send the session to PosterPro so the backend can save it into the existing bridge/session workflow
- act as the browser-side helper for Mercari, Facebook, and other assisted channels

## Load unpacked

1. Open Chrome or Edge.
2. Go to the extensions page.
3. Enable developer mode.
4. Load this folder as an unpacked extension.

## Notes

- PosterPro base URL defaults to `https://posterpro.sparkleserver.site`.
- The popup can copy the captured session JSON for manual fallback.
- The scaffold is intentionally thin; PosterPro still owns the workflow state and publish logic.
