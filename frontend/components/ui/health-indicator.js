import Badge from './badge';

export default function HealthIndicator({ healthy, label }) {
  return <Badge tone={healthy ? 'success' : 'warning'}>{label || (healthy ? 'Healthy' : 'Needs attention')}</Badge>;
}
