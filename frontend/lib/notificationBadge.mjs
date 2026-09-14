export function formatNotificationBadge(unreadCount) {
  const count = Number(unreadCount);
  if (!Number.isFinite(count) || count <= 0) return null;
  return count >= 100 ? '99+' : String(Math.floor(count));
}
