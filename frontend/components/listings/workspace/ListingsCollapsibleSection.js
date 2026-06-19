export default function ListingsCollapsibleSection({
  id,
  title,
  description,
  badge,
  defaultOpen = false,
  children,
}) {
  return (
    <details
      id={id}
      open={defaultOpen}
      className="group rounded-[22px] border border-[var(--pp-border)] bg-[var(--pp-surface)] shadow-[0_16px_34px_rgba(15,23,42,0.05)]"
    >
      <summary className="flex cursor-pointer list-none items-start justify-between gap-4 px-5 py-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="font-[var(--pp-heading-font)] text-lg font-semibold tracking-[-0.03em] text-[var(--pp-text)]">
              {title}
            </h2>
            {badge ? (
              <span className="rounded-full border border-[#d0d5dd] bg-[#f8fafc] px-2.5 py-1 text-[11px] font-semibold text-[#475467]">
                {badge}
              </span>
            ) : null}
          </div>
          {description ? <p className="mt-1.5 text-sm text-[var(--pp-muted)]">{description}</p> : null}
        </div>
        <span className="mt-0.5 rounded-full border border-[#d0d5dd] bg-white px-2.5 py-1 text-[11px] font-semibold text-[#475467] transition group-open:bg-[#101828] group-open:text-white">
          <span className="group-open:hidden">Expand</span>
          <span className="hidden group-open:inline">Collapse</span>
        </span>
      </summary>
      <div className="border-t border-[var(--pp-border)] p-5">{children}</div>
    </details>
  );
}
