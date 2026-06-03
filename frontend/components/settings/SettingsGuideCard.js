import HelpTip from '../ui/help-tip';

export function SettingsGuideCard({ title, description, tooltip, prerequisites = [], steps = [], tone = 'blue' }) {
  const toneClass =
    tone === 'amber'
      ? 'border-amber-200 bg-amber-50/80'
      : tone === 'slate'
      ? 'border-slate-200 bg-slate-50'
      : 'border-[#dbe7ff] bg-[#f6f9ff]';

  return (
    <div className={`rounded-[16px] border ${toneClass} p-4`}>
      <div className="flex items-start justify-between gap-3">
        <div className="max-w-[1024px]">
          <p className="text-sm font-semibold text-[#101828]">{title}</p>
          {description ? <p className="mt-1 text-sm text-[#475467]">{description}</p> : null}
        </div>
        {tooltip ? <HelpTip label={`${title} help`}>{tooltip}</HelpTip> : null}
      </div>
      {prerequisites.length ? (
        <div className="mt-4">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">Before you start</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {prerequisites.map((item) => (
              <span key={item} className="rounded-full border border-white/80 bg-white/80 px-3 py-1 text-xs font-medium text-[#344054]">
                {item}
              </span>
            ))}
          </div>
        </div>
      ) : null}
      {steps.length ? (
        <ol className="mt-4 space-y-2">
          {steps.map((step, index) => (
            <li key={step} className="flex gap-3 text-sm text-[#344054]">
              <span className="mt-[1px] flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white text-xs font-semibold text-[#1d4ed8]">
                {index + 1}
              </span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

export function SettingsInstructionTable({ title, rows }) {
  return (
    <div className="overflow-hidden rounded-[16px] border border-[#e5e7eb] bg-white">
      <div className="border-b border-[#e5e7eb] bg-[#f8fafc] px-4 py-3">
        <p className="text-sm font-semibold text-[#101828]">{title}</p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-[#fcfcfd] text-[#667085]">
            <tr>
              <th className="px-4 py-3 font-medium">Field</th>
              <th className="px-4 py-3 font-medium">Where to get it</th>
              <th className="px-4 py-3 font-medium">How to obtain it</th>
              <th className="px-4 py-3 font-medium">What it does</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.field} className="border-t border-[#e5e7eb] align-top">
                <td className="px-4 py-3 font-medium text-[#101828]">{row.field}</td>
                <td className="px-4 py-3 text-[#475467]">{row.where}</td>
                <td className="px-4 py-3 text-[#475467]">{row.how}</td>
                <td className="px-4 py-3 text-[#475467]">{row.purpose}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
