import { useEffect, useMemo, useState } from 'react';
import { Gift, Percent, RefreshCcw, Send, Sparkles } from 'lucide-react';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import DataTableCard from '../components/ui/data-table-card';
import EmptyState from '../components/ui/empty-state';
import FormSection from '../components/ui/form-section';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import StatusPill from '../components/ui/status-pill';
import { useAuth } from '../contexts/AuthContext';
import {
  fetchAutonomousConfig,
  fetchOfferHistory,
  fetchOfferRules,
  sendOffersNow,
  toggleAutonomousMode,
  updateOfferRules,
} from '../lib/api';

function formatTime(value) {
  if (!value) return 'Pending';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value));
}

export default function OffersPage() {
  const { user } = useAuth();
  const [autonomousConfig, setAutonomousConfig] = useState({ autonomous_mode: true, autonomous_dry_run: true });
  const [offerRule, setOfferRule] = useState({ is_enabled: false, rules: {} });
  const [history, setHistory] = useState([]);

  const reload = async () => {
    if (!user?.id) return;
    const [autoData, ruleData, historyData] = await Promise.all([
      fetchAutonomousConfig(),
      fetchOfferRules(user.id),
      fetchOfferHistory(user.id),
    ]);
    setAutonomousConfig(autoData);
    setOfferRule(ruleData);
    setHistory(historyData.offers || []);
  };

  useEffect(() => {
    reload();
  }, [user?.id]);

  const rules = offerRule.rules || {};
  const metrics = useMemo(
    () => [
      { label: 'Automation status', value: offerRule.is_enabled ? 'Enabled' : 'Paused', detail: 'Whether offer automation can send watcher or liker offers.' },
      { label: 'Default discount', value: `${rules.discount_percent ?? 10}%`, detail: 'The baseline offer markdown when automation sends an offer.' },
      { label: 'Minimum price', value: `$${Number(rules.minimum_listing_price ?? 25).toFixed(0)}`, detail: 'Listings below this threshold will be skipped.' },
      { label: 'Recent offers', value: history.length, detail: 'Logged automated offers in the current history payload.' },
    ],
    [history.length, offerRule.is_enabled, rules.discount_percent, rules.minimum_listing_price],
  );

  const offersSubnav = useMemo(
    () => ({
      eyebrow: 'Automation CMS',
      title: 'Offers & Rules',
      description: 'Keep automation rules, pricing guardrails, and offer history in a focused automation workspace.',
      sections: [
        {
          label: 'Automation Areas',
          items: [
            { key: 'rule-builder', label: 'Rule Builder', active: false, description: 'Offer automation controls', onClick: () => document.getElementById('rule-builder')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
            { key: 'automation-posture', label: 'Posture', active: false, description: 'Automation status summary', onClick: () => document.getElementById('automation-posture')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
            { key: 'history', label: 'Offer History', active: false, badge: history.length, description: 'Recent automated sends', onClick: () => document.getElementById('history')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
          ],
        },
      ],
    }),
    [history.length],
  );

  return (
    <AppShell
      active="/offers"
      title="Offers"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
      contentWidth="wide"
      subnav={offersSubnav}
    >
      <PageHeader
        title="Offers and watcher automation"
        description="Configure rule-based offers, keep the messaging consistent, and inspect recent automated sends from one admin surface."
        actions={
          <>
            <Button variant="outline" onClick={reload}>
              <RefreshCcw size={16} />
              Refresh
            </Button>
            <Button
              onClick={async () => {
                await sendOffersNow(user.id);
                await reload();
              }}
            >
              <Send size={16} />
              Send offers now
            </Button>
          </>
        }
      />

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {metrics.map((card) => (
          <MetricCard key={card.label} label={card.label} value={card.value} detail={card.detail} />
        ))}
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
        <SectionPanel id="rule-builder" title="Offer program" description="Build one clean automation rule set instead of scattering thresholds across the workspace.">
          <form
            className="space-y-4"
            onSubmit={async (event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              await updateOfferRules(user.id, {
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
            <div className="grid gap-4 md:grid-cols-2">
              <FormSection title="Automation toggles" description="Turn offer automation on only when the message and thresholds are ready.">
                <label className="flex items-center justify-between rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm font-medium text-[#101828]">
                  Auto-send enabled
                  <input type="checkbox" name="is_enabled" defaultChecked={offerRule.is_enabled} />
                </label>
                <label className="flex items-center justify-between rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-3 text-sm font-medium text-[#101828]">
                  Auto-send to new watchers
                  <input type="checkbox" name="auto_send_to_new_watchers" defaultChecked={rules.auto_send_to_new_watchers ?? true} />
                </label>
              </FormSection>

              <FormSection title="Pricing thresholds" description="Keep the automation profitable instead of sending blanket discounts.">
                <label className="block text-sm font-medium text-[#101828]">
                  Discount percent
                  <input name="discount_percent" type="number" min="1" max="80" step="1" defaultValue={rules.discount_percent ?? 10} className="mt-2 h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]" />
                </label>
                <label className="block text-sm font-medium text-[#101828]">
                  Minimum listing price
                  <input name="minimum_listing_price" type="number" min="0" step="0.01" defaultValue={rules.minimum_listing_price ?? 25} className="mt-2 h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]" />
                </label>
              </FormSection>
            </div>

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
              <FormSection title="Scope" description="Exclude listing IDs that should never receive automated offers.">
                <label className="block text-sm font-medium text-[#101828]">
                  Exclude listing IDs
                  <input name="exclude_listing_ids" defaultValue={(rules.exclude_listing_ids || []).join(', ')} className="mt-2 h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]" />
                </label>
              </FormSection>

              <SectionPanel title="Rule notes" description="Keep the automation targeted and believable.">
                <div className="space-y-3">
                  {[
                    'Offer rules should stay aligned with actual margin targets, not just watcher growth.',
                    'Excluded listing IDs are useful for premium inventory or price-protected items.',
                    'Manual sends remain available when a listing needs a custom negotiation path.',
                  ].map((item) => (
                    <div key={item} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-sm text-[#667085]">
                      {item}
                    </div>
                  ))}
                </div>
              </SectionPanel>
            </div>

            <FormSection title="Offer message" description="Keep the watcher message brief, credible, and consistent with the brand voice.">
              <label className="block text-sm font-medium text-[#101828]">
                Message template
                <textarea
                  name="message_template"
                  defaultValue={rules.message_template || ''}
                  className="mt-2 h-28 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 py-2 text-sm text-[#101828]"
                />
              </label>
            </FormSection>

            <div className="flex justify-end">
              <Button type="submit">
                <Gift size={16} />
                Save rule builder
              </Button>
            </div>
          </form>
        </SectionPanel>

        <SectionPanel id="automation-posture" title="Automation posture" description="Offer logic should feel deliberate, not spammy.">
          <div className="space-y-3">
            <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold text-[#101828]">Rule state</p>
                <StatusPill status={offerRule.is_enabled ? 'success' : 'default'} label={offerRule.is_enabled ? 'Live' : 'Paused'} />
              </div>
              <p className="mt-2 text-sm text-[#667085]">Global offer automation is {offerRule.is_enabled ? 'enabled' : 'currently paused'} for this account.</p>
            </div>
            <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
              <div className="flex items-center gap-2">
                <Percent size={16} className="text-[#2563eb]" />
                <p className="text-sm font-semibold text-[#101828]">Discount profile</p>
              </div>
              <p className="mt-2 text-sm text-[#667085]">Default offer is {rules.discount_percent ?? 10}% off and only runs above ${Number(rules.minimum_listing_price ?? 25).toFixed(0)}.</p>
            </div>
            <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
              <div className="flex items-center gap-2">
                <Sparkles size={16} className="text-[#2563eb]" />
                <p className="text-sm font-semibold text-[#101828]">Message quality</p>
              </div>
              <p className="mt-2 text-sm text-[#667085]">
                {rules.message_template ? 'A custom message template is configured for automated sends.' : 'No custom message template is saved yet.'}
              </p>
            </div>
          </div>
        </SectionPanel>
      </section>

      <div id="history">
      <DataTableCard
        title="Recent automated offers"
        description="Latest watcher or liker offer events recorded by the automation layer."
        columns={[
          { key: 'listing_id', label: 'Listing', render: (item) => `#${item.listing_id}` },
          { key: 'platform', label: 'Marketplace', render: (item) => <span className="capitalize">{item.platform}</span> },
          { key: 'watcher_count', label: 'Audience', render: (item) => item.watcher_count || 0 },
          { key: 'offer_price', label: 'Offer', render: (item) => `$${Number(item.offer_price || 0).toFixed(2)}` },
          { key: 'offer_percent', label: 'Discount', render: (item) => `${item.offer_percent || 0}%` },
          { key: 'sent_at', label: 'Sent', render: (item) => formatTime(item.sent_at) },
        ]}
        rows={history}
        rowKey={(row) => row.id}
        emptyState={<EmptyState title="No automated offers yet" description="Offer history will appear here after the first automated or manual send runs." className="border-0 p-0 py-6" />}
      />
      </div>
    </AppShell>
  );
}

OffersPage.requireAuth = true;
