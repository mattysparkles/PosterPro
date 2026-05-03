import { AlertTriangle, Brain, ChartColumn } from 'lucide-react';

import { Card, CardDescription, CardTitle } from './ui/card';

function IntelligenceCard({ icon: Icon, title, description, children }) {
  return (
    <Card>
      <CardTitle className="flex items-center gap-2 text-xl tracking-tight">
        <Icon size={18} />
        {title}
      </CardTitle>
      <CardDescription className="mt-2 leading-6">{description}</CardDescription>
      <div className="mt-6">{children}</div>
    </Card>
  );
}

export default function IntelligencePanel({ analytics, alerts, recommendation, prediction, optimization }) {
  return (
    <section className="grid gap-5 xl:grid-cols-3" data-tour="analytics">
      <IntelligenceCard
        icon={ChartColumn}
        title="Performance snapshot"
        description="A concise read on current business health without forcing the user into the full analytics page."
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
            <p className="text-xs font-bold uppercase tracking-[0.22em] text-slate-400">Revenue</p>
            <p className="mt-2 text-2xl font-semibold text-slate-950">${analytics?.total_revenue ?? 0}</p>
          </div>
          <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
            <p className="text-xs font-bold uppercase tracking-[0.22em] text-slate-400">Profit</p>
            <p className="mt-2 text-2xl font-semibold text-slate-950">${analytics?.total_profit ?? 0}</p>
          </div>
          <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
            <p className="text-xs font-bold uppercase tracking-[0.22em] text-slate-400">ROI</p>
            <p className="mt-2 text-2xl font-semibold text-slate-950">{analytics?.roi_percentage ?? 0}%</p>
          </div>
          <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
            <p className="text-xs font-bold uppercase tracking-[0.22em] text-slate-400">Sell-through</p>
            <p className="mt-2 text-2xl font-semibold text-slate-950">{analytics?.sell_through_rate ?? 0}%</p>
          </div>
        </div>
      </IntelligenceCard>

      <IntelligenceCard
        icon={AlertTriangle}
        title="Alerts and blockers"
        description="Plain-language reminders so the user knows what needs attention without scanning multiple sections."
      >
        <div className="space-y-3">
          {(alerts || []).slice(0, 4).length ? (
            (alerts || []).slice(0, 4).map((alert) => (
              <div key={`${alert.type}-${alert.listing_id}`} className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                <p className="text-sm leading-6 text-slate-700">{alert.message}</p>
              </div>
            ))
          ) : (
            <div className="rounded-[22px] border border-dashed border-slate-300 bg-slate-50 p-4">
              <p className="text-sm leading-6 text-slate-600">No active alerts are surfaced right now.</p>
            </div>
          )}
        </div>
      </IntelligenceCard>

      <IntelligenceCard
        icon={Brain}
        title="Pricing and AI guidance"
        description="Suggestions for pricing, sell probability, and listing polish without crowding the primary workflow."
      >
        <div className="space-y-3">
          {recommendation ? (
            <div className="rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] p-4">
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-slate-400">Suggested price</p>
              <p className="mt-2 text-2xl font-semibold text-slate-950">${recommendation.recommended_price}</p>
              <p className="mt-1 text-sm text-slate-600">Confidence {(recommendation.confidence * 100).toFixed(0)}%</p>
            </div>
          ) : null}

          {optimization?.suggested_title ? (
            <div className="rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] p-4">
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-slate-400">Title suggestion</p>
              <p className="mt-2 text-sm leading-6 text-slate-700">{optimization.suggested_title}</p>
            </div>
          ) : null}

          {prediction ? (
            <div className="rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] p-4">
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-slate-400">7 day sale probability</p>
              <p className="mt-2 text-2xl font-semibold text-slate-950">{(prediction.probability_sale_7d * 100).toFixed(0)}%</p>
            </div>
          ) : null}

          {!recommendation && !optimization?.suggested_title && !prediction ? (
            <div className="rounded-[16px] border border-dashed border-[#e5e7eb] bg-[#f8fafc] p-4">
              <p className="text-sm leading-6 text-slate-600">AI guidance will appear once listing data is available to analyze.</p>
            </div>
          ) : null}
        </div>
      </IntelligenceCard>
    </section>
  );
}
