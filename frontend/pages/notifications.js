import { useEffect, useState } from 'react';
import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import PageHeader from '../components/ui/page-header';
import StatusPill from '../components/ui/status-pill';
import { fetchProcessNotifications, markProcessNotificationRead, markAllProcessNotificationsRead } from '../lib/api';

export default function NotificationsPage() {
  const [rows, setRows] = useState([]); const [loading, setLoading] = useState(true); const [unreadOnly, setUnreadOnly] = useState(false);
  const load = async () => { setLoading(true); try { const data = await fetchProcessNotifications({ limit: 100, unreadOnly }); setRows(data?.items || data?.notifications || []); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, [unreadOnly]);
  const read = async (id) => { await markProcessNotificationRead(id); setRows((current) => current.map((row) => row.id === id ? { ...row, is_read: true, read: true } : row)); };
  return <AppShell active="/notifications" title="Notifications"><PageHeader title="Notifications" description="Review process notices and workflow actions." actions={<div className="flex gap-2"><Button variant="outline" onClick={() => setUnreadOnly((value) => !value)}>{unreadOnly ? 'Show all' : 'Unread only'}</Button><Button variant="outline" onClick={async () => { await markAllProcessNotificationsRead(); await load(); }}>Mark all read</Button></div>} />{loading ? <p>Loading notifications…</p> : <div className="space-y-3">{rows.map((row) => <article key={row.id} className="pp-card p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">{row.title || row.subject || 'Process notification'}</h2><p className="mt-1 text-sm text-slate-600">{row.message || row.body || ''}</p><p className="mt-2 text-xs text-slate-500">{row.created_at ? new Date(row.created_at).toLocaleString() : '—'} · {row.source || row.notification_type || 'system'}</p></div><div className="flex items-center gap-2"><StatusPill status={row.is_read || row.read ? 'read' : 'unread'} label={row.is_read || row.read ? 'Read' : 'Unread'} />{!(row.is_read || row.read) ? <Button size="sm" variant="outline" onClick={() => read(row.id)}>Mark read</Button> : null}</div></div></article>)}{!rows.length ? <p className="text-sm text-slate-500">No notifications match this view.</p> : null}</div>}</AppShell>;
}
