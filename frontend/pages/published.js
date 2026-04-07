import AppShell from '../components/layout/AppShell';
import PublishedListings from '../components/PublishedListings';
import useDashboardData from '../hooks/useDashboardData';
import { toggleAutonomousMode } from '../lib/api';

export default function PublishedPage({ theme, setTheme }) {
  const { listings, recentAutoPublished, autonomousConfig, reload } = useDashboardData();

  return (
    <AppShell
      active="/published"
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
      <PublishedListings listings={listings} />
      <div className="mt-4">
        <PublishedListings
          listings={recentAutoPublished}
          title="Recently Auto-Published"
          emptyMessage="No autonomous publishes yet."
          postedOnly={false}
        />
      </div>
    </AppShell>
  );
}
