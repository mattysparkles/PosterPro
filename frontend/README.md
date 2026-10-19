# Frontend (Next.js Dashboard)

```bash
npm install
npm run dev
```

Set `NEXT_PUBLIC_API_BASE` to your FastAPI URL if needed.

## New eBay dashboard features
- Connect eBay button (OAuth URL generation)
- Publish-to-eBay action with loading/errors
- Poll listing status via `/ebay/status/{id}`
- Published listings panel (title, listing ID, status, eBay link)
