import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { formatNotificationBadge } from './notificationBadge.mjs';

test('notification badge hides zero and caps large unread totals', () => {
  assert.equal(formatNotificationBadge(0), null);
  assert.equal(formatNotificationBadge(1), '1');
  assert.equal(formatNotificationBadge(99), '99');
  assert.equal(formatNotificationBadge(100), '99+');
  assert.equal(formatNotificationBadge(1000), '99+');
  assert.equal(formatNotificationBadge(27708), '99+');
});

test('app shell uses a single fixed sidebar track and a shrinkable main track', async () => {
  const css = await readFile(new URL('../styles/globals.css', import.meta.url), 'utf8');
  assert.match(css, /--pp-sidebar-width:\s*272px/);
  assert.match(css, /\.posterpro-app-shell\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*var\(--pp-sidebar-width\)\s+minmax\(0,\s*1fr\)/s);
  assert.match(css, /\.pp-shell-main\s*\{[^}]*grid-column:\s*2;[^}]*min-width:\s*0/s);
  assert.doesNotMatch(css, /\.posterpro-app-shell\s*>\s*\.pp-shell-main\s*\{[^}]*margin-left:/s);
  assert.match(css, /\.pp-dashboard-modules\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/s);
});

test('notification popup is viewport constrained and badge cannot change layout width', async () => {
  const css = await readFile(new URL('../styles/globals.css', import.meta.url), 'utf8');
  assert.match(css, /\.pp-notification-popover\s*\{[^}]*right:\s*12px;[^}]*width:\s*min\(440px,\s*calc\(100vw\s*-\s*24px\)\)/s);
  assert.match(css, /\.pp-notification-badge\s*\{[^}]*position:\s*absolute;[^}]*pointer-events:\s*none/s);
});
