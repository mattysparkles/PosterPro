# PosterPro performance baseline

Captured 2026-09-07. Public DNS and local services were unavailable from this
execution environment, so network/browser timings are explicitly unmeasured.

| Path | Before | Target | After | Bottleneck / evidence |
|---|---:|---:|---:|---|
| `/listings` | UNMEASURED | <500ms API | UNMEASURED | runtime probe blocked |
| `/jobs` | UNMEASURED | <500ms API | UNMEASURED | runtime probe blocked |
| `/intake/timeline` | UNMEASURED | <750ms metadata | UNMEASURED | runtime probe blocked |
| `/notifications` | UNMEASURED | <500ms API | UNMEASURED | runtime probe blocked |
| `/sales/dashboard` | UNMEASURED | <750ms API | UNMEASURED | runtime probe blocked |

Source findings: `useDashboardData` previously loaded nine static datasets by
default; Jobs now disables unrelated catalog/analytics/alerts/offers/templates
loads. Timeline uses lazy thumbnail loading and bounded thumbnail URLs. Final
query-count, payload, image-byte, and browser-interactive measurements require
runtime access and are not claimed here.
