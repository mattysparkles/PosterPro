import Button from '../ui/button';
import AppCard from '../ui/app-card';
import StatusPill from '../ui/status-pill';

export default function ThemePreviewCard({ theme, selected, onApply }) {
  return (
    <AppCard className={`p-4 ${selected ? 'ring-2 ring-[var(--pp-focus-ring)]' : ''}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-[var(--pp-text)]">{theme.name}</p>
          <p className="mt-1 text-xs text-[var(--pp-muted)]">{theme.description}</p>
        </div>
        <StatusPill status={theme.mode === 'dark' ? 'default' : theme.mode === 'high-contrast' ? 'warning' : 'info'} label={theme.mode} />
      </div>
      <div className="mt-3 flex items-center gap-2">
        {[theme.preview.background, theme.preview.surface, theme.preview.primary, theme.preview.accent].map((color, idx) => (
          <span key={`${theme.id}-${idx}`} className="h-5 w-5 rounded-full border border-white/80 shadow-sm" style={{ background: color }} />
        ))}
      </div>
      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-xs text-[var(--pp-muted)]">{theme.category}</span>
        <Button type="button" size="sm" variant={selected ? 'secondary' : 'default'} onClick={() => onApply(theme.id)}>
          {selected ? 'Applied' : 'Apply theme'}
        </Button>
      </div>
    </AppCard>
  );
}
