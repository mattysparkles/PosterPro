import { cn } from '../../lib/utils';

export function SettingsWorkspaceHero({ eyebrow, title, description, actions, stats = [] }) {
  return (
    <section className="rounded-[28px] border border-[var(--pp-border)] bg-[linear-gradient(135deg,#fffdf7_0%,#f7faff_48%,#ffffff_100%)] p-6 shadow-[0_20px_50px_rgba(16,24,40,0.08)]">
      <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
        <div className="max-w-3xl">
          {eyebrow ? <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--pp-muted)]">{eyebrow}</p> : null}
          {title ? <h2 className="mt-2 font-[var(--pp-heading-font)] text-[2rem] font-semibold tracking-[-0.04em] text-[var(--pp-text)]">{title}</h2> : null}
          {description ? <p className="mt-3 max-w-2xl text-sm leading-7 text-[var(--pp-muted)]">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
      </div>
      {stats.length ? (
        <div className="mt-6 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {stats.map((stat) => (
            <div key={stat.label} className="rounded-[18px] border border-[var(--pp-border)] bg-white/90 p-4">
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--pp-muted)]">{stat.label}</p>
              <p className="mt-3 text-2xl font-semibold tracking-[-0.04em] text-[var(--pp-text)]">{stat.value}</p>
              {stat.detail ? <p className="mt-2 text-sm leading-6 text-[var(--pp-muted)]">{stat.detail}</p> : null}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function SettingsWorkspaceGrid({ children, className }) {
  return <div className={cn('grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_360px]', className)}>{children}</div>;
}

export function SettingsWorkspaceMain({ children, className }) {
  return <div className={cn('space-y-6', className)}>{children}</div>;
}

export function SettingsWorkspaceAside({ children, className }) {
  return <aside className={cn('space-y-6 xl:sticky xl:top-[96px] xl:self-start', className)}>{children}</aside>;
}

export function SettingsWorkspaceRailCard({ title, description, children, tone = 'default', className }) {
  const toneClassName =
    tone === 'tint'
      ? 'border-[#dbe7ff] bg-[#f7faff]'
      : tone === 'warm'
      ? 'border-[#f2ddae] bg-[#fff8e8]'
      : 'border-[var(--pp-border)] bg-white';
  return (
    <section className={cn('rounded-[22px] border p-5 shadow-[0_16px_40px_rgba(16,24,40,0.06)]', toneClassName, className)}>
      {title ? <p className="text-sm font-semibold text-[var(--pp-text)]">{title}</p> : null}
      {description ? <p className="mt-2 text-sm leading-6 text-[var(--pp-muted)]">{description}</p> : null}
      {children ? <div className="mt-4">{children}</div> : null}
    </section>
  );
}
