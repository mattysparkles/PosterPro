import AppCard from '../ui/app-card';

export default function SettingsLayout({ nav, children }) {
  return (
    <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
      <aside className="sticky top-[92px] h-fit">
        <AppCard className="border-[var(--pp-border)] bg-[var(--pp-surface)]/95 p-4 shadow-[0_12px_30px_rgba(16,24,40,0.06)]">
          {nav}
        </AppCard>
      </aside>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
