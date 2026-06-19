import AppCard from '../ui/app-card';
import { PageAside, PageMain, PageSplit } from '../layout/PageFrame';

export default function SettingsLayout({ nav, children }) {
  return (
    <PageSplit className="mx-auto w-full max-w-[1440px] px-2 xl:px-0" columnsClassName="xl:grid-cols-[320px_minmax(0,1fr)]" gapClassName="gap-7">
      <PageAside stickyTopClassName="xl:top-[96px]">
        <AppCard className="border-[var(--pp-border)] bg-[var(--pp-surface)]/96 p-4 shadow-[0_20px_48px_rgba(16,24,40,0.08)]">
          {nav}
        </AppCard>
      </PageAside>
      <PageMain className="space-y-7">{children}</PageMain>
    </PageSplit>
  );
}
