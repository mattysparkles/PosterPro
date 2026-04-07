import { AlertTriangle, Brain, ChartColumn } from 'lucide-react';

import { Card, CardDescription, CardTitle } from './ui/card';

export default function IntelligencePanel({ analytics, alerts, recommendation, prediction, optimization }) {
  return (
    <section className="grid gap-4 lg:grid-cols-3" data-tour="analytics">
      <Card>
        <CardTitle className="flex items-center gap-2"><ChartColumn size={18} /> Analytics</CardTitle>
        <CardDescription className="mb-3">A quick snapshot of business health.</CardDescription>
        <p>Revenue: ${analytics?.total_revenue ?? 0}</p>
        <p>Profit: ${analytics?.total_profit ?? 0}</p>
        <p>ROI: {analytics?.roi_percentage ?? 0}%</p>
        <p>Sell-through: {analytics?.sell_through_rate ?? 0}%</p>
      </Card>

      <Card>
        <CardTitle className="flex items-center gap-2"><AlertTriangle size={18} /> Alerts</CardTitle>
        <CardDescription className="mb-3">Plain-English reminders so nothing slips.</CardDescription>
        {(alerts || []).slice(0, 4).map((a) => (
          <p key={`${a.type}-${a.listing_id}`} className="text-sm">• {a.message}</p>
        ))}
      </Card>

      <Card>
        <CardTitle className="flex items-center gap-2"><Brain size={18} /> Smart suggestions</CardTitle>
        <CardDescription className="mb-3">AI guidance for title, pricing, and sale probability.</CardDescription>
        {recommendation && <p>Suggested price: ${recommendation.recommended_price}</p>}
        {recommendation && <p>Confidence: {(recommendation.confidence * 100).toFixed(0)}%</p>}
        {optimization?.suggested_title && <p>Title idea: {optimization.suggested_title}</p>}
        {prediction && <p>Sale probability: {(prediction.probability_sale_7d * 100).toFixed(0)}% (7d)</p>}
      </Card>
    </section>
  );
}
