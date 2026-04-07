import { useEffect, useState } from 'react';
import { Gift, Send } from 'lucide-react';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import Input from '../components/ui/input';
import { Card, CardDescription, CardTitle } from '../components/ui/card';
import {
  fetchAutonomousConfig,
  fetchOfferHistory,
  fetchOfferRules,
  sendOffersNow,
  toggleAutonomousMode,
  updateOfferRules,
} from '../lib/api';

export default function OffersPage({ theme, setTheme }) {
  const [autonomousConfig, setAutonomousConfig] = useState({ autonomous_mode: true, autonomous_dry_run: true });
  const [offerRule, setOfferRule] = useState({ is_enabled: false, rules: {} });
  const [history, setHistory] = useState([]);

  const reload = async () => {
    const [autoData, ruleData, historyData] = await Promise.all([
      fetchAutonomousConfig(),
      fetchOfferRules(1),
      fetchOfferHistory(1),
    ]);
    setAutonomousConfig(autoData);
    setOfferRule(ruleData);
    setHistory(historyData.offers || []);
  };

  useEffect(() => {
    reload();
  }, []);

  const rules = offerRule.rules || {};

  return (
    <AppShell
      active="/offers"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
      theme={theme}
      onToggleTheme={() => {
        const next = theme === 'dark' ? 'light' : 'dark';
        setTheme(next);
        localStorage.setItem('posterpro-theme', next);
        document.documentElement.classList.toggle('dark', next === 'dark');
      }}
    >
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Send Offers</CardTitle>
            <CardDescription>Build rules to automatically send personalized offers to new watchers/likers.</CardDescription>
          </div>
          <Button onClick={async () => { await sendOffersNow(1); await reload(); }}><Send size={16} /> Send Offers Now</Button>
        </div>

        <form
          className="mt-4 grid gap-3 md:grid-cols-2"
          onSubmit={async (event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            await updateOfferRules(1, {
              is_enabled: form.get('is_enabled') === 'on',
              rules: {
                auto_send_to_new_watchers: form.get('auto_send_to_new_watchers') === 'on',
                discount_percent: Number(form.get('discount_percent') || 10),
                minimum_listing_price: Number(form.get('minimum_listing_price') || 0),
                exclude_listing_ids: String(form.get('exclude_listing_ids') || '')
                  .split(',')
                  .map((value) => value.trim())
                  .filter(Boolean)
                  .map((value) => Number(value)),
                message_template: String(form.get('message_template') || ''),
              },
            });
            await reload();
          }}
        >
          <label className="rounded-2xl border border-border p-3 text-sm">
            <div className="flex items-center justify-between">
              Auto-send enabled
              <input type="checkbox" name="is_enabled" defaultChecked={offerRule.is_enabled} />
            </div>
          </label>
          <label className="rounded-2xl border border-border p-3 text-sm">
            <div className="flex items-center justify-between">
              Auto-send to new watchers
              <input type="checkbox" name="auto_send_to_new_watchers" defaultChecked={rules.auto_send_to_new_watchers ?? true} />
            </div>
          </label>
          <div>
            <p className="mb-1 text-sm text-muted-foreground">Discount %</p>
            <Input name="discount_percent" type="number" min="1" max="80" step="1" defaultValue={rules.discount_percent ?? 10} />
          </div>
          <div>
            <p className="mb-1 text-sm text-muted-foreground">Min listing price</p>
            <Input name="minimum_listing_price" type="number" min="0" step="0.01" defaultValue={rules.minimum_listing_price ?? 25} />
          </div>
          <div className="md:col-span-2">
            <p className="mb-1 text-sm text-muted-foreground">Exclude listing IDs (comma-separated)</p>
            <Input name="exclude_listing_ids" defaultValue={(rules.exclude_listing_ids || []).join(', ')} />
          </div>
          <div className="md:col-span-2">
            <p className="mb-1 text-sm text-muted-foreground">Offer message</p>
            <textarea
              name="message_template"
              defaultValue={rules.message_template || ''}
              className="h-24 w-full rounded-2xl border border-border bg-background px-3 py-2 text-sm"
            />
          </div>
          <div className="md:col-span-2 flex justify-end">
            <Button type="submit"><Gift size={16} /> Save Rule Builder</Button>
          </div>
        </form>
      </Card>

      <Card className="mt-4">
        <CardTitle>Recent automated offers</CardTitle>
        <CardDescription className="mb-3">Latest personalized offers sent by your automation.</CardDescription>
        <div className="space-y-2">
          {history.map((item) => (
            <div key={item.id} className="rounded-2xl border border-border/70 p-3 text-sm">
              <div className="flex items-center justify-between">
                <p className="font-semibold">Listing #{item.listing_id} · {item.platform}</p>
                <p>${Number(item.offer_price || 0).toFixed(2)} ({item.offer_percent}% off)</p>
              </div>
              <p className="text-muted-foreground">Watchers targeted: {item.watcher_count} · {item.sent_at || 'pending'}</p>
            </div>
          ))}
          {!history.length && <p className="text-sm text-muted-foreground">No automated offers yet.</p>}
        </div>
      </Card>
    </AppShell>
  );
}
