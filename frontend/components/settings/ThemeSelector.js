import { ADMIN_THEMES } from '../../lib/adminThemes';
import ThemePreviewCard from './ThemePreviewCard';

export default function ThemeSelector({ activeThemeId, onApply }) {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {ADMIN_THEMES.map((theme) => (
        <ThemePreviewCard key={theme.id} theme={theme} selected={activeThemeId === theme.id} onApply={onApply} />
      ))}
    </div>
  );
}
