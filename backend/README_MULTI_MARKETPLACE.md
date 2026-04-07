# Multi-marketplace architecture notes

## Connector strategies for non-public APIs

For marketplaces with no stable public listing APIs (Facebook Marketplace, Mercari, Poshmark), PosterPro supports an abstracted fallback connector that can be wired to:

1. **Unified API provider** (e.g., API2Cart-like service)
   - Pros: less operational burden, normalized auth + payloads.
   - Cons: vendor lock-in, partial feature coverage, extra cost.
2. **Browser automation microservice** (Playwright/Puppeteer)
   - Pros: full UI parity when APIs are unavailable.
   - Cons: brittle selectors, CAPTCHA/MFA handling complexity, high maintenance.

## Compliance / TOS considerations

- Confirm marketplace Terms of Service and automation policy before enabling browser automation in production.
- Respect rate limits, anti-abuse controls, and user-consent boundaries.
- Keep account credentials encrypted at rest and isolate automation workers with strict access controls.

## Example publish flow logs

```text
[2026-04-07T10:12:00Z] listing=124 publish request targets=[ebay,mercari,poshmark]
[2026-04-07T10:12:01Z] task=9f1 queue marketplace=ebay status=QUEUED
[2026-04-07T10:12:01Z] task=9f2 queue marketplace=mercari status=QUEUED_AUTOMATION
[2026-04-07T10:12:01Z] task=9f3 queue marketplace=poshmark status=QUEUED_AUTOMATION
[2026-04-07T10:12:18Z] task=9f1 marketplace=ebay status=PUBLISHED listing_id=392001122
[2026-04-07T10:12:22Z] task=9f2 marketplace=mercari status=PENDING_PROVIDER_SYNC
[2026-04-07T10:12:23Z] task=9f3 marketplace=poshmark status=PENDING_PROVIDER_SYNC
```
