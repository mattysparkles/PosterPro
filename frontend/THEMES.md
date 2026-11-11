# PosterPro Admin Themes

## Scope
- Admin themes style the authenticated dashboard/admin UI.
- Hosted public page themes (Settings -> Hosted Pages / Themes) are a separate CMS system.

## Registry
- File: `frontend/lib/adminThemes.js`
- Each theme includes:
  - `id`, `name`, `description`, `category`, `mode`
  - `className` (metadata)
  - `preview` swatches used by the selector UI

## Runtime
- Provider: `frontend/contexts/AdminThemeContext.js`
- Hook: `frontend/hooks/useAdminTheme.js`
- Persistence: `localStorage` key `posterpro.adminTheme`
- Applied to `<html>` as `data-admin-theme="<id>"`

## CSS variables
- Base and overrides are in `frontend/styles/globals.css`
- Core variables include:
  - `--pp-bg`, `--pp-surface`, `--pp-surface-strong`
  - `--pp-text`, `--pp-muted`, `--pp-border`
  - `--pp-primary`, `--pp-primary-hover`, `--pp-primary-soft`
  - `--pp-success`, `--pp-warning`, `--pp-danger`, `--pp-info`
  - `--pp-card`, `--pp-card-border`, `--pp-card-shadow`
  - `--pp-shell-bg`, `--pp-shell-sidebar`, `--pp-shell-header`
  - `--pp-shell-title`, `--pp-shell-copy`, `--pp-shell-soft-copy`
  - `--pp-shell-hover`, `--pp-shell-active`, `--pp-focus-ring`

## Add a new admin theme
1. Add a new entry to `ADMIN_THEMES` in `frontend/lib/adminThemes.js`.
2. Add a matching CSS override block in `frontend/styles/globals.css`:
   - `:root[data-admin-theme='<theme-id>'] { ... }`
3. Keep contrast readable for tables, forms, and nav active states.
4. Verify in Settings -> Appearance and run lint/build.
