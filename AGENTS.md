# PosterPro Deployment Log

## 2026-04-07 - Task Start

### About To Do
- Establish this file as the running source of truth for PosterPro deployment work.
- Inspect the current server/workspace state before cloning or changing any live config.
- Review the GitHub repo structure and identify missing deployment directives, secrets, service definitions, and safety checks.

### Completed
- Created this `AGENTS.md` runbook.
- Confirmed `/opt/apps/posterpro` is currently not a git checkout and contains no existing project files to preserve.
- Confirmed the repo target is `mattysparkles/PosterPro` on GitHub and that it is accessible via the connected GitHub app.
- Cloned `mattysparkles/PosterPro` into `/opt/apps/posterpro/repo` for inspection.
- Inspected the live Caddy config at `/etc/caddy/Caddyfile` and confirmed `sparkleserver.site` is currently served from `/opt/apps/shellgames`.
- Confirmed the SparkleServer hub project grid lives in `/opt/apps/shellgames/sparkles.html` and `/opt/apps/shellgames/index.html`.
- Confirmed live port usage includes `127.0.0.1:8010` and `*:8000`, so the proposed `8010` backend port conflicts with an existing service.
- Enumerated the current repo requirements and gaps:
  - Backend env example exists at `/opt/apps/posterpro/repo/backend/.env.example`.
  - Frontend is Next.js and currently expects `NEXT_PUBLIC_API_BASE`.
  - Root frontend page is an internal dashboard, not a public lander.
  - No real auth/login system exists yet.
  - No `/admin` route or admin GUI exists yet.
  - The app assumes `user_id=1` in many places and does not currently seed a default user.
  - `PhotoRoom` is optional but required for background removal features.
  - `npm run build` produces a Next.js production build, but the repo is not currently configured for static export through Caddy alone.

### Missing Pieces / Inputs Still Needed
- Network-approved clone/pull access if sandbox restrictions block `git clone`.
- Server-specific confirmation for the active Caddy email/contact and live Caddyfile path.
- Runtime secrets still need to be enumerated from the repo after clone.
- Final backend/frontend production ports must be reassigned because `8010` is already occupied.
- Required env/config values now identified from code:
  - `DATABASE_URL`
  - `REDIS_URL`
  - `STORAGE_ROOT`
  - `OPENAI_API_KEY`
  - `EBAY_CLIENT_ID`
  - `EBAY_CLIENT_SECRET`
  - `EBAY_REDIRECT_URI`
  - `PHOTOROOM_API_KEY` for background removal
  - Optional tuning flags: `ENVIRONMENT`, `AUTONOMOUS_MODE`, `AUTONOMOUS_DRY_RUN`, `AUTONOMOUS_CROSSPOST_ENABLED`, `AUTO_RELIST_ENABLED`, `AUTO_RELIST_MIN_PRICE`, `AUTO_RELIST_USER_RULES_JSON`, `SALE_DETECTION_ENABLED`, `SALE_DETECTION_DRY_RUN`, `SALE_DETECTION_POLL_MINUTES`, `MAX_CONCURRENT_BULK_TASKS`, `BULK_CHUNK_SIZE`
- A deployment decision is still needed for frontend serving:
  - Either run a Node/Next service in production, or
  - modify the app for static export before serving only static assets through Caddy.
- A seed user/admin strategy is needed before login/admin requirements can be considered complete.

### Start Commands
- Clone repo:
  - `git clone --branch main https://github.com/mattysparkles/PosterPro.git /opt/apps/posterpro/repo`
- Inspect live ports:
  - `ss -ltnp | rg ':3010|:8010|:8000|:3000|:5432|:6379'`
- Inspect live Caddy config:
  - `sed -n '1,260p' /etc/caddy/Caddyfile`

### Status / Logs
- Current workspace state: `ls -la /opt/apps/posterpro`
- GitHub repo metadata available through the connected GitHub app.
- Repo working tree: `/opt/apps/posterpro/repo`
- Main SparkleServer hub source: `/opt/apps/shellgames/sparkles.html`
- Main Caddy config: `/etc/caddy/Caddyfile`

### Notes For Next Run
- Do not assume anything is deployed yet. This directory is effectively empty.
- Treat this run as initial reconnaissance plus deployment-plan hardening unless clone/setup proceeds.
- Treat the original deployment brief as partially inaccurate:
  - `8010` is not safe to reuse.
  - Static-only Caddy serving is not currently compatible with the repo as-is.
  - Login/admin are new feature work, not existing deployable features.

## 2026-04-07 - Task Update

### About To Do
- Populate `/opt/apps/posterpro/repo/backend/.env` with the missing secrets (`OPENAI_API_KEY`, `EBAY_*`, `PHOTOROOM_API_KEY`, etc.).
- Install backend/python dependencies (`pip install -r requirements.txt`) and frontend/node dependencies (`npm install`).
- Ensure PostgreSQL is reachable from this container (psql/createdb currently return permission errors) and run every SQL migration in `/opt/apps/posterpro/repo/backend/migrations`.
- Launch uvicorn (127.0.0.1:8030), Celery worker, Celery beat, and Next (`NEXT_PUBLIC_API_BASE=https://posterpro.sparkleserver.site/api` on 127.0.0.1:3030), then verify Caddy proxies through the new block.

### Completed
- Created `/opt/apps/posterpro/repo/backend/.env` using the production schema and initialized `/opt/apps/posterpro/repo/backend/storage`.
- Attempted `pip install -r requirements.txt` inside `backend/.venv` but the sandbox cannot reach PyPI (DNS/permission blocked), so the dependencies are not installed yet.
- Added the PosterPro block to `/etc/caddy/Caddyfile` so `/api/*` and `/media/*` now go to 127.0.0.1:8030, while the remainder proxies to 127.0.0.1:3030.
- Created `/var/log/caddy/shellgames.access.log`, validated the Caddyfile (with elevated permissions), and reloaded the Caddy service to pick up the new site definition.
- Ran `npm install` inside `/opt/apps/posterpro/repo/frontend`, but npm aborted with `EPERM` while fetching packages; the frontend dependencies still need to be installed.

### Missing Pieces / Inputs Still Needed
- Secrets: `OPENAI_API_KEY`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `PHOTOROOM_API_KEY`, and any other API keys referenced in `/opt/apps/posterpro/repo/backend/app/core/config.py`.
- Backend packages: rerun `.venv/bin/pip install -r requirements.txt` once outbound network access is restored (or supply an offline package mirror).
- Frontend packages: rerun `npm install` from `/opt/apps/posterpro/repo/frontend` once the npm registry is reachable or provide the packages locally.
- PostgreSQL connectivity: current commands fail due to permission restrictions when accessing `psql` sockets; a workaround (perhaps `psql` over TCP with credentials) is required to create the `posterpro` database and run migrations.
- After dependencies/migrations succeed, Celery worker/beat and Next server must be launched on their respective ports (8030, 3030).

### Start Commands
- Backend API: `cd /opt/apps/posterpro/repo/backend && . .venv/bin/activate && set -o allexport && source .env && set +o allexport && uvicorn app.main:app --host 127.0.0.1 --port 8030 --workers 3`
- Celery worker: `cd /opt/apps/posterpro/repo/backend && . .venv/bin/activate && set -o allexport && source .env && set +o allexport && celery -A app.workers.celery_app.celery_app worker --loglevel=info`
- Celery beat: `cd /opt/apps/posterpro/repo/backend && . .venv/bin/activate && set -o allexport && source .env && set +o allexport && celery -A app.workers.celery_app.celery_app beat`
- Frontend: `cd /opt/apps/posterpro/repo/frontend && export NEXT_PUBLIC_API_BASE=https://posterpro.sparkleserver.site/api && npm run build && npm start -- --hostname 127.0.0.1 --port 3030`
- Additional: `caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy`
- Migrations: `psql postgresql://postgres:postgres@127.0.0.1:5432/posterpro -f backend/migrations/20260407_...sql` (repeat for each SQL file)

### Status / Logs
- Caddy now listens for `posterpro.sparkleserver.site` and proxies `/api` + `/media` to the new backend port 8030.
- Dependency installs cannot complete inside the sandbox (`pip` cannot reach PyPI; `npm` returns `EPERM` while fetching packages).
- PostgreSQL tooling (`psql`, `createdb`) fails with permission issues in this environment, so migrations are still pending.

### Notes For Next Run
- Keep this folder layout anchored at `/opt/apps/posterpro/repo`.
- Collect the missing API keys before running the services.
- Install dependencies (pip + npm), run all migrations, launch backend/worker/frontend, then revalidate Caddy.
- Document any log paths, service commands, and port assignments back in this file before handing off to the next engineer.

## 2026-04-27 - Product + Deploy Progress

### About To Do
- Install backend and frontend dependencies so the new auth-enabled build can actually start.
- Verify PostgreSQL connectivity using the configured `DATABASE_URL`, run the pending SQL migrations, and bootstrap the auth columns in the live database.
- Launch the backend and frontend on the existing Caddy-routed ports (`8030`, `3030`) and verify `posterpro.sparkleserver.site` serves the new onboarding page plus authenticated app.

### Completed
- Re-read `AGENTS.md` and `realitycheck.txt` before changing code so the current repo and server constraints stayed aligned with reality.
- Confirmed the earlier Caddy route for `posterpro.sparkleserver.site` is still present and still targets backend `127.0.0.1:8030` and frontend `127.0.0.1:3030`.
- Reconfirmed the frontend and backend processes are not currently listening on the PosterPro ports, so the site is still down pending dependency install and process launch.
- Added a minimal real authentication layer in the backend:
  - `POST /auth/register`
  - `POST /auth/login`
  - `POST /auth/logout`
  - `GET /auth/me`
- Added session-cookie support and password hashing without introducing new auth-specific Python dependencies.
- Added user schema support for `password_hash` and `is_admin`, plus a new SQL migration file:
  - `/opt/apps/posterpro/repo/backend/migrations/20260427_auth_bootstrap.sql`
- Added runtime SQL guards in `backend/app/main.py` so the auth columns are created if the `users` table already exists.
- Reworked key backend routes to use the authenticated user instead of blindly assuming `user_id=1`, including listings, analytics, sales, marketplace config, inventory bulk jobs, and storage-unit batch access.
- Added a new public onboarding/marketing page at:
  - `/opt/apps/posterpro/repo/frontend/pages/index.js`
- Moved the existing internal dashboard to:
  - `/opt/apps/posterpro/repo/frontend/pages/app.js`
- Added frontend auth wiring:
  - `/login`
  - `/register`
  - auth context/provider
  - protected-route gate for the internal app pages
- Updated the app shell so signed-in users can see the active account and sign out.
- Updated the SparkleServer hub pages so they now include a PosterPro project card linking to `https://posterpro.sparkleserver.site`:
  - `/opt/apps/shellgames/sparkles.html`
  - `/opt/apps/shellgames/index.html`
- Ran `python3 -m compileall backend/app` successfully after the backend edits to catch syntax issues early.
- Captured an additional server datum from the current session:
  - server IP provided: `64.227.20.213`
- Installed backend dependencies successfully into `/opt/apps/posterpro/repo/backend/.venv`.
- Installed frontend dependencies successfully into `/opt/apps/posterpro/repo/frontend/node_modules`.
- Confirmed the local PostgreSQL listener was reachable outside the sandbox, repaired the `postgres` password to match the repo `.env`, and created the `posterpro` database.
- Initialized the base schema by importing the backend app against the live database.
- Ran the full migration set and repaired one stale migration script:
  - patched `/opt/apps/posterpro/repo/backend/migrations/20260407_multi_marketplace_listings.sql`
  - reran it successfully after making the enum cast conditional
- Built the Next.js frontend successfully with `npm run build`.
- Brought the live services up on the intended localhost ports:
  - backend: `127.0.0.1:8030`
  - frontend: `127.0.0.1:3030`
- Verified direct health and proxy routing:
  - `http://127.0.0.1:8030/health` returned `{"ok": true}`
  - `https://posterpro.sparkleserver.site/api/health` returned `{"ok": true}` through Caddy
  - the new lander and login page render through the frontend
- Smoke-tested the auth flow through the public domain path:
  - registration succeeded
  - session cookie worked with `/api/auth/me`
- Deleted the temporary smoke-test user immediately after verification so the first real user signup still becomes the bootstrap admin.
- Isolated Celery onto a dedicated `posterpro` queue and fixed task registration:
  - updated `/opt/apps/posterpro/repo/backend/app/workers/celery_app.py`
  - relaunched worker on `-Q posterpro`
- Fixed a background task crash in sale polling caused by logging reserved `message` keys from stub events:
  - updated `/opt/apps/posterpro/repo/backend/app/services/sale_detection_service.py`
- Relaunched Celery worker + beat successfully after the queue and logging fixes.
- Added persistent systemd unit files under `/opt/apps/posterpro/deploy/systemd` for:
  - `posterpro-backend.service`
  - `posterpro-frontend.service`
  - `posterpro-worker.service`
  - `posterpro-beat.service`
- Installed those unit files into `/etc/systemd/system`, ran `systemctl daemon-reload`, and enabled + started all four services.
- Verified systemd service health:
  - backend: active
  - frontend: active
  - worker: active
  - beat: active
- Reconfirmed the `users` table count is `0` after smoke-test cleanup, so the first real signup will become admin.

### Missing Pieces / Inputs Still Needed
- No real marketplace account is connected yet, so background eBay polling logs expected connector errors until a real account is linked.
- The first real operator still needs to register through the live UI to create the bootstrap admin account.

### Start Commands
- Backend dependency install:
  - `cd /opt/apps/posterpro/repo/backend && ./.venv/bin/pip install -r requirements.txt`
- Frontend dependency install:
  - `cd /opt/apps/posterpro/repo/frontend && npm install`
- Backend compile/sanity:
  - `cd /opt/apps/posterpro/repo && python3 -m compileall backend/app`
- Backend API:
  - `cd /opt/apps/posterpro/repo/backend && . .venv/bin/activate && set -o allexport && source .env && set +o allexport && uvicorn app.main:app --host 127.0.0.1 --port 8030 --workers 3`
- Frontend:
  - `cd /opt/apps/posterpro/repo/frontend && export NEXT_PUBLIC_API_BASE=https://posterpro.sparkleserver.site/api && npm run build && npm start -- --hostname 127.0.0.1 --port 3030`
- Celery worker:
  - `cd /opt/apps/posterpro/repo/backend && . .venv/bin/activate && set -o allexport && source .env && set +o allexport && celery -A app.workers.celery_app.celery_app worker -Q posterpro --loglevel=info`
- Celery beat:
  - `cd /opt/apps/posterpro/repo/backend && . .venv/bin/activate && set -o allexport && source .env && set +o allexport && celery -A app.workers.celery_app.celery_app beat`

### Status / Logs
- Product gap closure is no longer just in code; the new lander, auth flow, backend API, and worker stack are all running under systemd.
- `posterpro.sparkleserver.site` now serves the marketing/onboarding landing page and proxies `/api/*` to the live backend.
- The login/register flow is live; the first real signup will become admin because the smoke-test account was removed.
- Celery is isolated to the `posterpro` queue so it no longer consumes unrelated global broker traffic.

### Notes For Next Run
- The internal dashboard route is `/app`; the root `/` route is the public marketing/onboarding lander.
- Expect sale-polling noise until an actual marketplace account is connected, but the worker should stay healthy now.

## 2026-04-28 - Setup UX + Lander Upgrade

### About To Do
- Re-check `AGENTS.md` and `realitycheck.txt` before making more product-level changes so the current deployment status stays grounded in the actual server state.
- Audit whether the logged-in dashboard really exposes account-specific setup for marketplace use, rather than assuming the current UI is sufficient.
- Replace the lightweight onboarding page with a more professional one-page lander that explains the product value clearly and routes users into registration/login.

### Completed
- Re-read `/opt/apps/posterpro/AGENTS.md` and `/opt/apps/posterpro/realitycheck.txt` before proceeding.
- Audited the current product and confirmed the user concern was valid:
  - the product had auth and a working dashboard,
  - but it did not yet explain account-specific setup cleanly,
  - and the landing page was still too thin for a serious onboarding/sales surface.
- Added a new authenticated account-setup summary API:
  - `PATCH /auth/me`
  - `GET /users/{user_id}/setup`
- Extended the backend setup response so the UI now receives:
  - account profile completeness
  - listing counts
  - connected marketplace counts
  - per-marketplace connection/readiness state
  - server-level readiness state for `OPENAI`, `PHOTOROOM`, `EBAY_*`, and storage
- Added a new reusable setup checklist panel:
  - `/opt/apps/posterpro/repo/frontend/components/SetupChecklistPanel.js`
- Added a new authenticated setup/settings page:
  - `/opt/apps/posterpro/repo/frontend/pages/settings.js`
- Updated the app shell navigation so the new setup center is first-class in the logged-in experience.
- Updated the main dashboard at `/app` so first-time users immediately see a setup checklist instead of landing in a context-poor screen.
- Replaced the public root page with a more deliberate one-page marketing/onboarding lander that:
  - explains the product value more clearly,
  - sells the workflow and user experience,
  - gives stronger registration/login calls to action,
  - and visually reads more like a finished product page than a placeholder.
- Added supporting visual polish in:
  - `/opt/apps/posterpro/repo/frontend/styles/globals.css`
- Re-ran build/sanity validation successfully:
  - `python3 -m compileall /opt/apps/posterpro/repo/backend/app`
  - `cd /opt/apps/posterpro/repo/frontend && npm run build`
- Restarted the deployed services and verified they came back healthy:
  - `posterpro-backend.service`
  - `posterpro-frontend.service`
- Verified the live domain after restart:
  - `https://posterpro.sparkleserver.site/api/health` returned `{"ok": true}`
  - `https://posterpro.sparkleserver.site/` served the new one-page lander
- Ran a temporary public-domain auth smoke test to validate the new setup summary path, then cleaned up immediately:
  - registration/login worked
  - `GET /api/users/{id}/setup` returned the new setup payload
  - temporary user deleted
  - `users` table count returned to `0`

### Missing Pieces / Inputs Still Needed
- The dashboard now has a real setup center, but marketplace account setup is only partially complete:
  - eBay is the only marketplace with a real user-level connection path modeled end-to-end
  - the other marketplaces are represented in the UI/data model, but their account-specific connection flows are still unfinished
- The live setup summary currently reports these server-level blockers:
  - `openai_configured=false`
  - `photoroom_configured=false`
  - `ebay_oauth_configured=false`
- Because the `EBAY_*` credentials are not configured yet, even the eBay account-connection path is not actually available to end users on the live server today.
- Because `OPENAI_API_KEY` and `PHOTOROOM_API_KEY` are not configured yet, AI/photo-enhancement features remain incomplete at runtime even though the UI now explains that clearly.

### Start Commands
- Backend sanity:
  - `python3 -m compileall /opt/apps/posterpro/repo/backend/app`
- Frontend build:
  - `cd /opt/apps/posterpro/repo/frontend && npm run build`
- Restart live app services:
  - `systemctl restart posterpro-backend.service posterpro-frontend.service`
- Health checks:
  - `curl -k -sSf https://posterpro.sparkleserver.site/api/health`
  - `curl -k -sS https://posterpro.sparkleserver.site/`
- Confirm bootstrap state after any smoke testing:
  - `psql postgresql://postgres:postgres@127.0.0.1:5432/posterpro -c "SELECT COUNT(*) FROM users;"`

### Status / Logs
- PosterPro now has a materially better first-time UX:
  - public one-page onboarding/marketing lander at `/`
  - login/register flow
  - authenticated dashboard at `/app`
  - dedicated setup center at `/settings`
- The UI now distinguishes clearly between:
  - account-specific actions the user can take,
  - and server-level dependencies the user cannot solve from inside the dashboard.
- The live product is more honest and usable than before, but it is not yet a fully self-serve multi-marketplace SaaS:
  - marketplace connection UX is only real for eBay in principle,
  - and even that remains blocked until the server-level eBay OAuth credentials are populated.

### Notes For Next Run
- If the goal is full account-level marketplace self-service, the next product gap is not deployment; it is connector completion for the non-eBay marketplaces plus a finished credential/connection UX.
- If the goal is better runtime capability for the current product, populate `OPENAI_API_KEY`, `PHOTOROOM_API_KEY`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, and `EBAY_REDIRECT_URI`, then re-check the setup summary response.

## 2026-04-28 - Landing Page Redesign Pass 2

### About To Do
- Replace the still-underwhelming public lander with a more convincing marketing page instead of continuing to iterate on the previous “explainer” layout.
- Rework the information hierarchy so the homepage sells outcomes, communicates a cleaner product identity, and looks intentional on first load.
- Push the updated frontend live and verify the public domain serves the new design.

### Completed
- Re-read the current landing page implementation and shared frontend styles before editing.
- Replaced the previous root page structure in `/opt/apps/posterpro/repo/frontend/pages/index.js` with a stronger one-page sales layout:
  - clearer hero headline and subhead
  - stronger CTA structure
  - cleaner metrics strip
  - more polished product/mock-console presentation
  - better “why switch” framing
  - tighter workflow story and feature grid
  - more credible final CTA section
- Reworked the supporting visual system in `/opt/apps/posterpro/repo/frontend/styles/globals.css`:
  - new background treatment
  - mesh/noise layers
  - updated atmospheric flares
  - new hero-stage panel treatment for the mock product area
- Removed the “unfinished internal explainer” feel from the homepage copy and replaced it with more marketable product positioning focused on resale workflow value.
- Rebuilt the frontend successfully with `npm run build`.
- Restarted `posterpro-frontend.service` and confirmed the service returned to `active`.
- Verified the live domain `https://posterpro.sparkleserver.site/` is now serving the new landing page HTML with the new hero:
  - “Run your resale operation from intake to sold without living in ten different tools.”

### Missing Pieces / Inputs Still Needed
- The public lander is now substantially stronger, but if an even more premium brand direction is wanted after this pass, the next step would be custom illustration/art direction rather than another round of basic layout cleanup.
- Product capability gaps still remain separate from the marketing surface:
  - marketplace self-serve setup is still only partially implemented
  - server-level keys for `OPENAI`, `PHOTOROOM`, and `EBAY_*` are still missing on the live system

### Start Commands
- Frontend build:
  - `cd /opt/apps/posterpro/repo/frontend && npm run build`
- Restart frontend:
  - `systemctl restart posterpro-frontend.service`
- Verify public page:
  - `curl -k -sS https://posterpro.sparkleserver.site/`

### Status / Logs
- The homepage is no longer the prior thin onboarding explainer; it is now a more deliberate product-marketing page.
- The updated landing page is live on `posterpro.sparkleserver.site`.

### Notes For Next Run
- If further brand/design iteration is requested, start from this version instead of trying to salvage the older layout.

## 2026-04-28 - Landing Page Redesign Pass 3

### About To Do
- Abandon the prior glass-card SaaS treatment and replace it with a stronger editorial/product-marketing composition.
- Make the landing page feel more premium and intentional through typography, contrast, and a cleaner narrative arc.
- Push the new visual direction live and verify the public domain reflects it.

### Completed
- Replaced `/opt/apps/posterpro/repo/frontend/pages/index.js` again with a new marketing layout built around:
  - dark premium hero stage
  - serif/sans typography pairing
  - stronger product-positioning headline
  - more deliberate mock-console tableau
  - clearer three-part narrative
  - warmer, less generic visual palette
- Added Google-hosted `Instrument Serif` + `Sora` font loading on the landing page only.
- Reworked `/opt/apps/posterpro/repo/frontend/styles/globals.css` for the new direction:
  - warm neutral background treatment
  - grain/orb atmosphere
  - new hero-frame styling
  - new floating tableau styling
  - landing-specific typography helpers
- Rebuilt the frontend successfully with `npm run build`.
- Restarted `posterpro-frontend.service` successfully.
- Verified `https://posterpro.sparkleserver.site/` is now serving the newest landing-page hero:
  - “PosterPro turns resale chaos into one clean operating surface.”

### Missing Pieces / Inputs Still Needed
- If the design still needs to move upward after this pass, the next step is likely image/illustration/brand-system work rather than more surface-level layout changes alone.
- Runtime product limitations remain separate from the homepage redesign:
  - incomplete marketplace self-serve flows
  - missing live server keys for `OPENAI`, `PHOTOROOM`, and `EBAY_*`

### Start Commands
- Frontend build:
  - `cd /opt/apps/posterpro/repo/frontend && npm run build`
- Restart frontend:
  - `systemctl restart posterpro-frontend.service`
- Verify live page:
  - `curl -k -sS https://posterpro.sparkleserver.site/`

### Status / Logs
- The public homepage is now on its third full redesign pass and is materially different from the prior versions.
- The newest redesign is live on `posterpro.sparkleserver.site`.

### Notes For Next Run
- If more redesign work is requested, compare against the live current version first instead of assuming the previous pass is still deployed.

## 2026-04-28 - Landing Page Reset Pass 4

### About To Do
- Scrap the editorial/dark-hero direction and rebuild the homepage as a cleaner, more conventional modern one-page marketing site.
- Reduce visual gimmicks, simplify the layout, and switch to a more direct hero-banner structure with real marketing copy.
- Make sure the root page remains fully static-export compatible before pushing it live.

### Completed
- Replaced `/opt/apps/posterpro/repo/frontend/pages/index.js` again with a simpler one-page marketing homepage built around:
  - straightforward hero banner
  - clearer product headline
  - direct CTA structure
  - cleaner benefits section
  - simpler three-step story
  - lighter, more conventional product-marketing layout
- Reworked landing-specific styles in `/opt/apps/posterpro/repo/frontend/styles/globals.css`:
  - removed the prior dark/editorial landing-specific treatment
  - added a lighter marketing background
  - kept the visual system minimal and less decorative
- Discovered and fixed the actual export blocker on `/` during this pass:
  - an undefined icon import (`CircleStack`) was breaking static export
  - replaced it with `Database`
- Simplified the landing page further so it no longer depends on runtime auth state for CTA rendering.
- Rebuilt successfully with `npm run build`.
- Restarted `posterpro-frontend.service` successfully.
- Verified `https://posterpro.sparkleserver.site/` is live with the new simpler homepage hero:
  - “PosterPro gives resellers one clean place to run the work.”

### Missing Pieces / Inputs Still Needed
- The homepage is now structurally closer to a standard SaaS/product landing page, but further refinement can still be done if a more specific visual reference or brand direction is provided.
- Product/runtime limitations are unchanged by the marketing reset:
  - incomplete marketplace self-serve flows
  - missing server-level keys for `OPENAI`, `PHOTOROOM`, and `EBAY_*`

### Start Commands
- Frontend build:
  - `cd /opt/apps/posterpro/repo/frontend && npm run build`
- Restart frontend:
  - `systemctl restart posterpro-frontend.service`
- Verify live page:
  - `curl -k -sS https://posterpro.sparkleserver.site/`

### Status / Logs
- The public homepage has been reset to a lighter, more conventional one-page product-marketing structure.
- The current live homepage is the simpler hero-banner version, not the previous dark/editorial variant.

### Notes For Next Run
- If another redesign is requested, start from the current simpler structure instead of reviving the prior experimental directions.

## 2026-04-28 - Landing Page Reset Pass 5

### About To Do
- Replace the still-too-card-heavy reset page with a more conventional modern SaaS homepage layout.
- Use a centered hero, lighter section rhythm, fewer visual tricks, and more normal product-site copy.
- Push the new structure live and verify the public domain is serving it.

### Completed
- Replaced `/opt/apps/posterpro/repo/frontend/pages/index.js` again with a cleaner marketing structure:
  - conventional header
  - centered hero banner
  - simpler value proposition
  - lighter preview section
  - flatter benefits layout
  - straightforward three-step explanation
  - simple closing CTA block
- Kept the landing-specific CSS restrained in `/opt/apps/posterpro/repo/frontend/styles/globals.css` so the page stays lighter and less ornamental.
- Rebuilt the frontend successfully with `npm run build`.
- Restarted `posterpro-frontend.service` successfully.
- Verified `https://posterpro.sparkleserver.site/` is serving the new simpler homepage with the hero:
  - “Run inventory, listings, and marketplace work from one clean system.”

### Missing Pieces / Inputs Still Needed
- If another design pass is needed after this, the fastest path is to work against a named visual reference or a concrete example homepage rather than continuing blind iteration.
- Product limitations remain unchanged:
  - incomplete marketplace self-serve flows
  - missing server-level keys for `OPENAI`, `PHOTOROOM`, and `EBAY_*`

### Start Commands
- Frontend build:
  - `cd /opt/apps/posterpro/repo/frontend && npm run build`
- Restart frontend:
  - `systemctl restart posterpro-frontend.service`
- Verify live page:
  - `curl -k -sS https://posterpro.sparkleserver.site/`

### Status / Logs
- The live homepage is now a much more conventional one-page SaaS/product marketing layout.
- This version is materially different from the earlier experimental visual directions.

### Notes For Next Run
- Start from the current live simpler layout if more work is requested.

## 2026-04-28 - Landing Page Upgrade (Vendoo-Style Corporate Pass)

### About To Do
- Stop iterating on "basic stacked cards" and rebuild the homepage using a real competitor-style corporate marketing layout (Vendoo as baseline).
- Ensure the root `/` page remains static-export compatible before restarting the live frontend.

### Completed
- Replaced `/opt/apps/posterpro/repo/frontend/pages/index.js` with a different information architecture closer to competitor marketing sites:
  - real header nav (anchors into sections)
  - corporate hero + stronger copy
  - marketplace rail
  - "why" value strip
  - alternating feature sections with product-style mockups
  - proper closing CTA
- Added purpose-built mock UI "shots" (pure HTML/CSS, no images required) so the page has real visual weight without adding asset pipelines.
- Added landing-only styling in `/opt/apps/posterpro/repo/frontend/styles/globals.css` under the `posterpro-corporate*` class family.
- Fixed a production export blocker:
  - `STEPS` was referenced but not defined in the new landing page, which caused `next export` to fail on `/`
  - added `const STEPS = [...]` and verified `npm run build` succeeds again
- Restarted `posterpro-frontend.service` and confirmed it is `active (running)`.
- Verified `https://posterpro.sparkleserver.site/` is serving the new corporate homepage HTML (header/nav/hero/mockups present).

## 2026-04-28 - Landing Page Visual Upgrade (Brand + Graphics + Responsive)

### Completed
- Improved the corporate lander to look less flat/gray and more like a real competitor homepage:
  - added a branded accent palette + background atmosphere (gradients + subtle texture) via `posterpro-corporate` CSS variables
  - added a hero “graphic” as an inline SVG (no asset pipeline required) so the top fold has real visual weight
  - switched landing typography to `Plus Jakarta Sans` (loaded on `/` only)
  - upgraded the marketplace row from text pills to logo-style chips with per-marketplace accent marks
  - adjusted the hero right-column layout to stack cleanly on mobile
- Verified `npm run build` still prerenders `/` as static content and restarted `posterpro-frontend.service`.

### Start Commands
- Frontend build:
  - `cd /opt/apps/posterpro/repo/frontend && npm run build`
- Restart frontend:
  - `systemctl restart posterpro-frontend.service`
- Verify live page:
  - `curl -k -sS https://posterpro.sparkleserver.site/ | head -n 80`

## 2026-04-29 - Workspace UX Rollout Verification

### About To Do
- Resume from the interrupted frontend rollout and verify the new workspace shell, dashboard, and setup center are actually present on disk and deployed.
- Confirm the production frontend service restarted cleanly after the latest UI changes.
- Record the resumed verification state so the next run does not have to reconstruct what was already shipped.

### Completed
- Confirmed the new frontend workspace files are present in `/opt/apps/posterpro/repo`, including:
  - `/opt/apps/posterpro/repo/frontend/pages/app.js`
  - `/opt/apps/posterpro/repo/frontend/pages/settings.js`
  - `/opt/apps/posterpro/repo/frontend/components/layout/AppShell.js`
  - `/opt/apps/posterpro/repo/frontend/components/ClusterPreview.js`
  - `/opt/apps/posterpro/repo/frontend/components/IntelligencePanel.js`
  - `/opt/apps/posterpro/repo/frontend/components/PublishedListings.js`
- Confirmed the latest `settings.js` on disk is the redesigned Setup Center version, not the earlier pre-redesign page.
- Confirmed the latest `app.js` on disk is the overhauled workspace dashboard with:
  - command-center framing
  - first-run tour
  - pipeline overview
  - listing snapshot
  - upgraded intake / intelligence / published sections
- Confirmed the local frontend production build had already passed successfully before the interruption.
- Confirmed `posterpro-frontend.service` is healthy after restart:
  - active/running under systemd
  - `ExecStartPre=/usr/bin/npm run build` completed successfully
  - Next started on `127.0.0.1:3030`
- Verified the live frontend process outside the sandbox using localhost:
  - `http://127.0.0.1:3030/` returns the corporate marketing lander HTML
  - page title served: `PosterPro | Reseller Operating Software`
  - `http://127.0.0.1:3030/app` responds with `HTTP/1.1 200 OK`
- Confirmed `posterpro-backend.service` is still active while serving the updated frontend.
- Reconfirmed the expected current runtime caveat remains unchanged:
  - background eBay-related logs still appear until a real marketplace account is connected

### Missing Pieces / Inputs Still Needed
- Sandbox DNS could not resolve `posterpro.sparkleserver.site` during this resumed session, so final HTTP verification was performed against the live localhost service instead of the public hostname.
- Runtime capability gaps remain separate from this UI rollout:
  - non-eBay marketplace account flows are still unfinished
  - `OPENAI`, `PHOTOROOM`, and `EBAY_*` server credentials still need to be populated for full readiness

### Start Commands
- Frontend build:
  - `cd /opt/apps/posterpro/repo/frontend && npm run build`
- Restart frontend:
  - `systemctl restart posterpro-frontend.service`
- Verify frontend service:
  - `systemctl status posterpro-frontend.service --no-pager`
- Verify localhost frontend directly:
  - `curl -sS http://127.0.0.1:3030/ | head -n 80`
  - `curl -sSI http://127.0.0.1:3030/app`

### Status / Logs
- The redesigned marketing lander is serving from the live Next process.
- The redesigned workspace shell, dashboard, and setup center are present on disk and were included in the successful production build.
- The frontend rollout that was interrupted by the earlier connectivity loss is now closed out and verified.

### Notes For Next Run
- Treat the current workspace/UI overhaul as deployed unless a newer change set replaces it.
- If public-domain verification is needed again from this environment, expect to use elevated localhost checks if sandbox DNS resolution is unavailable.

## 2026-05-02 - Runtime Credential Update

### About To Do
- Populate the live backend env with the newly provided OpenAI and eBay credentials so the existing AI and eBay integration code can leave the "server-level blockers" state.
- Restart the backend/worker/beat services after the env change so the new values are loaded into the running processes.
- Reconfirm which credentials are still missing after the update instead of assuming every feature is now unblocked.

### Completed
- Updated `/opt/apps/posterpro/repo/backend/.env` with:
  - `OPENAI_API_KEY`
  - `EBAY_CLIENT_ID`
  - `EBAY_CLIENT_SECRET`
  - `EBAY_DEV_ID` for future reference, even though the current backend code does not read it yet
- Left the existing production `EBAY_REDIRECT_URI` unchanged:
  - `https://posterpro.sparkleserver.site/ebay/callback`
- Confirmed from the current backend config and service code that the active runtime currently reads:
  - `OPENAI_API_KEY`
  - `EBAY_CLIENT_ID`
  - `EBAY_CLIENT_SECRET`
  - `EBAY_REDIRECT_URI`
  - `PHOTOROOM_API_KEY`

### Missing Pieces / Inputs Still Needed
- `PHOTOROOM_API_KEY` is still missing, so background-removal features remain unavailable until that key is added.
- A service restart/health recheck is still required after this env update so the running backend and Celery processes actually pick up the new values.

### Start Commands
- Restart backend stack:
  - `systemctl restart posterpro-backend.service posterpro-worker.service posterpro-beat.service`
- Check service health:
  - `systemctl status posterpro-backend.service posterpro-worker.service posterpro-beat.service --no-pager`
- Optional API health:
  - `curl -sS http://127.0.0.1:8030/health`

### Status / Logs
- The env file is no longer missing the OpenAI key or the core eBay OAuth credentials.
- Runtime readiness is improved, but PhotoRoom-backed features are still blocked pending `PHOTOROOM_API_KEY`.

### Notes For Next Run
- Do not assume `EBAY_DEV_ID` affects the current runtime; it is stored now, but the present backend implementation does not consume it.
- After adding `PHOTOROOM_API_KEY`, restart the same backend/worker/beat services and recheck the setup summary payload.
