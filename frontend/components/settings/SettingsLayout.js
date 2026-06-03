import AppCard from '../ui/app-card';

export default function SettingsLayout({ nav, children }) {
  return (
    <div className="grid gap-4 xl:grid-cols-[260px_minmax(0,1fr)]">
      <AppCard className="h-fit p-3">{nav}</AppCard>
      <div>{children}</div>
    </div>
  );
}
