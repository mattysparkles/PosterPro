import { useEffect, useState } from 'react';
import AppShell from '../../components/layout/AppShell';
import Button from '../../components/ui/button';
import PageHeader from '../../components/ui/page-header';
import SectionPanel from '../../components/ui/section-panel';
import Input from '../../components/ui/input';
import Checkbox from '../../components/ui/checkbox';
import { fetchMarketplaceRoutingRules, saveMarketplaceRoutingRules } from '../../lib/api';

const MARKETS = [
  ['ebay', 'eBay'], ['facebook', 'Facebook Marketplace'], ['mercari', 'Mercari'],
  ['poshmark', 'Poshmark'], ['vinted', 'Vinted'], ['etsy', 'Etsy'], ['offerup', 'OfferUp'],
];

const EMPTY_FORM = {
  name: '', category_terms: '', brand_terms: '', condition_terms: '', source_types: '',
  min_price: '', max_price: '', include_markets: [], exclude_markets: [], priority: 100, match_all: false,
};

function splitTerms(value) {
  return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
}

function toggleValue(values, value) {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

export default function MarketplaceRoutingPage() {
  const [rules, setRules] = useState([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const result = await fetchMarketplaceRoutingRules();
      setRules(Array.isArray(result?.rules) ? result.rules : []);
      setError('');
    } catch (err) {
      setError(err?.message || 'Could not load marketplace routing rules.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const createRule = async (event) => {
    event.preventDefault();
    setError('');
    setNotice('');
    const include = form.include_markets;
    const conditions = ['category_terms', 'brand_terms', 'condition_terms', 'source_types'].some((key) => splitTerms(form[key]).length)
      || form.min_price !== '' || form.max_price !== '';
    if (!include.length) {
      setError('Choose at least one destination marketplace.');
      return;
    }
    if (!conditions && !form.match_all) {
      setError('Add a match condition, or explicitly enable Match all items.');
      return;
    }
    const rawId = String(form.name || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    if (!rawId) {
      setError('Enter a rule name.');
      return;
    }
    if (rules.some((rule) => rule.id === rawId)) {
      setError('A rule with that normalized name already exists.');
      return;
    }
    const toNumber = (value) => value === '' ? null : Number(value);
    const rule = {
      id: rawId,
      enabled: true,
      priority: Math.max(0, Math.min(10000, Number(form.priority) || 100)),
      match_all: Boolean(form.match_all),
      category_terms: splitTerms(form.category_terms),
      brand_terms: splitTerms(form.brand_terms),
      condition_terms: splitTerms(form.condition_terms),
      source_types: splitTerms(form.source_types),
      min_price: toNumber(form.min_price),
      max_price: toNumber(form.max_price),
      include_markets: include,
      exclude_markets: form.exclude_markets,
    };
    const next = [...rules, rule];
    setSaving(true);
    try {
      const result = await saveMarketplaceRoutingRules(next);
      setRules(result.rules || next);
      setForm(EMPTY_FORM);
      setNotice('Routing rule saved. Matching listings will use it for future bulk crossposts unless destinations are manually selected.');
    } catch (err) {
      setError(err?.message || 'Could not save the routing rule.');
    } finally {
      setSaving(false);
    }
  };

  const updateRule = async (next) => {
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const result = await saveMarketplaceRoutingRules(next);
      setRules(result.rules || next);
      setNotice('Routing rules saved.');
    } catch (err) {
      setError(err?.message || 'Could not update routing rules.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <AppShell active="/settings" title="Marketplace routing">
      <div className="space-y-5">
        <PageHeader
          eyebrow="Automation"
          breadcrumbs={[{ label: 'Settings', href: '/settings?tab=marketplaces' }, { label: 'Routing rules', active: true }]}
          title="Marketplace routing rules"
          description="Route eligible inventory by canonical category, brand, condition, source, and price. Earlier priorities run first; exclusions win. A manual destination selection on Listings overrides these rules."
          actions={<Button variant="outline" href="/settings?tab=marketplaces">Marketplace connections</Button>}
        />
        {error && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
        {notice && <p role="status" className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</p>}
        <SectionPanel title="Saved rules" description={loading ? 'Loading account-scoped rules…' : `${rules.length} rule${rules.length === 1 ? '' : 's'} stored for the authenticated account.`}>
          {rules.length === 0 && !loading ? <p className="text-sm text-slate-600">No routing rules yet. Bulk crosspost will use each listing&apos;s saved targets; unrouted listings will not silently default to eBay.</p> : null}
          <div className="space-y-3">
            {rules.map((rule, index) => (
              <article key={rule.id} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="font-semibold text-slate-900">{rule.id}</h2>
                    <p className="mt-1 text-xs text-slate-600">Priority {rule.priority} · {rule.enabled ? 'Enabled' : 'Disabled'} · Include: {(rule.include_markets || []).join(', ') || 'none'} · Exclude: {(rule.exclude_markets || []).join(', ') || 'none'}</p>
                    <p className="mt-1 text-xs text-slate-600">Filters: {[
                      ...(rule.category_terms || []).map((value) => `category ${value}`),
                      ...(rule.brand_terms || []).map((value) => `brand ${value}`),
                      ...(rule.condition_terms || []).map((value) => `condition ${value}`),
                      ...(rule.source_types || []).map((value) => `source ${value}`),
                      rule.min_price != null ? `min $${rule.min_price}` : '',
                      rule.max_price != null ? `max $${rule.max_price}` : '',
                      rule.match_all ? 'match all' : '',
                    ].filter(Boolean).join(' · ') || 'no filters'}</p>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" disabled={saving} onClick={() => void updateRule(rules.map((item, i) => i === index ? { ...item, enabled: !item.enabled } : item))}>{rule.enabled ? 'Disable' : 'Enable'}</Button>
                    <Button variant="outline" size="sm" disabled={saving} onClick={() => void updateRule(rules.filter((_, i) => i !== index))}>Delete</Button>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </SectionPanel>
        <SectionPanel title="Add a routing rule" description="When multiple filters are filled, all specified filters must match. Enter multiple terms as comma-separated values.">
          <form onSubmit={createRule} className="space-y-4">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <label className="text-sm">Rule name<Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Clothing to resale apps" /></label>
              <label className="text-sm">Priority<Input type="number" min="0" max="10000" value={form.priority} onChange={(event) => setForm({ ...form, priority: event.target.value })} /></label>
              <label className="text-sm">Category terms<Input value={form.category_terms} onChange={(event) => setForm({ ...form, category_terms: event.target.value })} placeholder="Clothing, Shoes" /></label>
              <label className="text-sm">Brand terms<Input value={form.brand_terms} onChange={(event) => setForm({ ...form, brand_terms: event.target.value })} placeholder="Nike, Patagonia" /></label>
              <label className="text-sm">Condition terms<Input value={form.condition_terms} onChange={(event) => setForm({ ...form, condition_terms: event.target.value })} placeholder="New, Used - Excellent" /></label>
              <label className="text-sm">Source types<Input value={form.source_types} onChange={(event) => setForm({ ...form, source_types: event.target.value })} placeholder="amazon_vine, manual" /></label>
              <label className="text-sm">Minimum price<Input type="number" min="0" step="0.01" value={form.min_price} onChange={(event) => setForm({ ...form, min_price: event.target.value })} /></label>
              <label className="text-sm">Maximum price<Input type="number" min="0" step="0.01" value={form.max_price} onChange={(event) => setForm({ ...form, max_price: event.target.value })} /></label>
            </div>
            <Checkbox checked={form.match_all} onChange={(event) => setForm({ ...form, match_all: event.target.checked })} label="Match all items (use sparingly)" />
            <div className="grid gap-4 md:grid-cols-2">
              {[
                ['include_markets', 'Include marketplaces'],
                ['exclude_markets', 'Exclude marketplaces'],
              ].map(([field, label]) => (
                <fieldset key={field} className="rounded-lg border p-3">
                  <legend className="px-1 text-sm font-semibold">{label}</legend>
                  <div className="flex flex-wrap gap-x-4 gap-y-2">
                    {MARKETS.map(([value, name]) => <Checkbox key={value} className="border-0 bg-transparent p-1" checked={form[field].includes(value)} onChange={() => setForm({ ...form, [field]: toggleValue(form[field], value) })} label={name} />)}
                  </div>
                </fieldset>
              ))}
            </div>
            <Button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Add rule'}</Button>
          </form>
        </SectionPanel>
      </div>
    </AppShell>
  );
}
