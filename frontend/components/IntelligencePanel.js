export default function IntelligencePanel({ analytics, alerts, recommendation, prediction, optimization }) {
  return (
    <section className="card intelligence-grid">
      <div>
        <h2>📊 Analytics Dashboard</h2>
        <p>Revenue: ${analytics?.total_revenue ?? 0}</p>
        <p>Profit: ${analytics?.total_profit ?? 0}</p>
        <p>ROI: {analytics?.roi_percentage ?? 0}%</p>
        <p>Sell-through: {analytics?.sell_through_rate ?? 0}%</p>
      </div>

      <div>
        <h2>💡 Insights Panel</h2>
        {(alerts || []).slice(0, 4).map((a) => (
          <p key={`${a.type}-${a.listing_id}`}>• {a.message}</p>
        ))}
      </div>

      <div>
        <h2>🧠 Smart Recommendations</h2>
        {recommendation && (
          <>
            <p>Suggested price: ${recommendation.recommended_price}</p>
            <p>Confidence: {(recommendation.confidence * 100).toFixed(0)}%</p>
            <p>{recommendation.reasoning}</p>
          </>
        )}
        {optimization?.suggested_title && <p>Title idea: {optimization.suggested_title}</p>}
        {prediction && (
          <p>
            Sale probability: {(prediction.probability_sale_7d * 100).toFixed(0)}% (7d),{' '}
            {(prediction.probability_sale_30d * 100).toFixed(0)}% (30d)
          </p>
        )}
      </div>
    </section>
  );
}
