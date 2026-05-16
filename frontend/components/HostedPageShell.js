import Head from 'next/head';
import Link from 'next/link';

export function buildThemeStyles(theme = {}) {
  const palette = theme.palette || {};
  const typography = theme.typography || {};
  const layout = theme.layout || {};
  return {
    page: {
      background: palette.page_background || 'linear-gradient(180deg,#f8fbff 0%,#ffffff 55%,#f7f8fa 100%)',
      color: palette.surface_foreground || '#101828',
      fontFamily: typography.font_family || "'Plus Jakarta Sans', 'Segoe UI', sans-serif",
    },
    hero: {
      background: palette.hero_background || 'linear-gradient(135deg,#0f172a 0%,#1d4ed8 52%,#38bdf8 100%)',
      color: palette.hero_foreground || '#f8fbff',
      textAlign: layout.align || 'center',
    },
    shell: {
      maxWidth: layout.content_width || '860px',
    },
    surface: {
      background: palette.surface_background || 'rgba(255,255,255,0.96)',
      color: palette.surface_foreground || '#101828',
      borderColor: palette.border_color || '#d7e3f4',
    },
    body: {
      color: palette.surface_muted || '#475467',
    },
    accent: palette.accent_color || '#2563eb',
    border: palette.border_color || '#d7e3f4',
    success: palette.success_color || '#166534',
    warning: palette.warning_color || '#b54708',
    danger: palette.danger_color || '#b42318',
    headingFamily: typography.heading_family || typography.font_family || "'Plus Jakarta Sans', 'Segoe UI', sans-serif",
  };
}

function ActionLink({ href, label, accent, previewMode, secondary = false, borderColor, textColor }) {
  if (!label) return null;
  const className = `inline-flex items-center justify-center rounded-[12px] px-4 py-2 text-sm font-medium ${secondary ? 'border bg-white' : 'text-white'} transition`;
  const style = secondary
    ? { borderColor, color: textColor, opacity: previewMode ? 0.8 : 1 }
    : { background: accent };
  if (!href || previewMode) {
    return (
      <div className={className} style={style}>
        {label}
      </div>
    );
  }
  return (
    <Link href={href} className={className} style={style}>
      {label}
    </Link>
  );
}

function PageBlocks({ blocks = [], bodyStyle }) {
  if (!blocks.length) return null;
  return (
    <div className="space-y-5">
      {blocks.map((block, index) => {
        if (block.type === 'rich_text') {
          return <div key={index} className="prose prose-slate max-w-none text-sm leading-7" style={bodyStyle} dangerouslySetInnerHTML={{ __html: block.html || '' }} />;
        }
        if (block.type === 'steps') {
          const items = Array.isArray(block.items) ? block.items : [];
          return (
            <div key={index} className="rounded-[18px] border bg-[#fcfcfd] p-5">
              <p className="text-sm font-semibold text-[#101828]">Steps</p>
              <div className="mt-4 space-y-3">
                {items.map((item, stepIndex) => (
                  <div key={`${index}-${stepIndex}`} className="flex gap-3">
                    <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[#eff6ff] text-xs font-semibold text-[#1d4ed8]">
                      {stepIndex + 1}
                    </div>
                    <p className="text-sm leading-6" style={bodyStyle}>{item}</p>
                  </div>
                ))}
              </div>
            </div>
          );
        }
        if (block.type === 'feature_list') {
          const items = Array.isArray(block.items) ? block.items : [];
          return (
            <div key={index} className="grid gap-3 md:grid-cols-3">
              {items.map((item, featureIndex) => (
                <div key={`${index}-${featureIndex}`} className="rounded-[18px] border bg-[#fcfcfd] p-5">
                  <p className="text-sm font-semibold text-[#101828]">{item.title || `Feature ${featureIndex + 1}`}</p>
                  <p className="mt-2 text-sm leading-6" style={bodyStyle}>{item.body || ''}</p>
                </div>
              ))}
            </div>
          );
        }
        if (block.type === 'cta') {
          const button = block.button || {};
          return (
            <div key={index} className="rounded-[18px] border bg-[#fcfcfd] p-5">
              <p className="text-sm font-semibold text-[#101828]">{block.title || 'Call to action'}</p>
              <p className="mt-2 text-sm leading-6" style={bodyStyle}>{block.body || ''}</p>
              {button.label ? <div className="mt-4"><ActionLink href={button.href} label={button.label} accent="#2563eb" previewMode secondary={false} /></div> : null}
            </div>
          );
        }
        return null;
      })}
    </div>
  );
}

function HostedPageFrame({
  page,
  title,
  brandName,
  statusTone = 'default',
  statusMessage = '',
  primaryHref = '',
  primaryLabel = '',
  children,
  previewMode = false,
}) {
  const theme = page?.theme || {};
  const styles = buildThemeStyles(theme);
  const chrome = theme.chrome || {};
  const showBrandBadge = theme.layout?.show_brand_badge !== false;
  const hero = page?.hero || {};
  const primaryButton = page?.primary_button || {};
  const secondaryButton = page?.secondary_button || {};
  const blocks = Array.isArray(page?.blocks) ? page.blocks : [];
  const toneClassName =
    statusTone === 'success'
      ? 'bg-[rgba(22,101,52,0.08)]'
      : statusTone === 'warning'
      ? 'bg-[rgba(181,71,8,0.08)]'
      : statusTone === 'danger'
      ? 'bg-[rgba(180,35,24,0.08)]'
      : 'bg-[rgba(37,99,235,0.08)]';
  const toneColor =
    statusTone === 'success'
      ? styles.success
      : statusTone === 'warning'
      ? styles.warning
      : statusTone === 'danger'
      ? styles.danger
      : styles.accent;

  return (
    <main className={previewMode ? 'min-h-0 px-0 py-0' : 'min-h-screen px-4 py-8 md:py-12'} style={styles.page}>
      <div className={`mx-auto overflow-hidden border shadow-[0_24px_80px_rgba(15,23,42,0.12)] ${previewMode ? 'rounded-[24px]' : 'rounded-[32px]'}`} style={{ ...styles.shell, ...styles.surface }}>
        <section className="px-6 py-8 md:px-8 md:py-10" style={styles.hero}>
            {showBrandBadge ? (
              <div className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] ${toneClassName}`} style={{ color: styles.hero.color }}>
              {hero.eyebrow || theme.hero_eyebrow || 'Hosted by PosterPro'}
              </div>
            ) : null}
          <div className="mt-5">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] opacity-80">{brandName || 'PosterPro'}</p>
            <h1 className="mt-3 text-3xl font-semibold tracking-[-0.04em] md:text-4xl" style={{ fontFamily: styles.headingFamily }}>
              {hero.title || title}
            </h1>
            <p className="mx-auto mt-4 max-w-[680px] text-sm leading-7 opacity-90 md:text-[15px]">
              {hero.body || page?.summary || theme.hero_body || 'PosterPro serves this page from the hosted CMS workspace.'}
            </p>
          </div>
        </section>

        <section className="px-6 py-6 md:px-8 md:py-8">
          {statusMessage ? (
            <div className={`mb-5 rounded-[18px] border px-4 py-4 text-sm ${toneClassName}`} style={{ borderColor: styles.border, color: toneColor }}>
              {statusMessage}
            </div>
          ) : null}
          {blocks.length ? <PageBlocks blocks={blocks} bodyStyle={styles.body} /> : <div className="prose prose-slate max-w-none text-sm leading-7" style={styles.body}>{children}</div>}
          <div className="mt-8 flex flex-wrap gap-3 border-t pt-5" style={{ borderColor: styles.border }}>
            <ActionLink
              href={primaryButton.href || primaryHref}
              label={primaryButton.label || primaryLabel || chrome.primary_cta_label || 'Return to PosterPro'}
              accent={styles.accent}
              previewMode={previewMode}
            />
            <ActionLink
              href={secondaryButton.href}
              label={secondaryButton.label || chrome.secondary_cta_label || 'Close window'}
              accent={styles.accent}
              previewMode={previewMode}
              secondary
              borderColor={styles.border}
              textColor={styles.body.color}
            />
          </div>
          {chrome.footer_note ? (
            <p className="mt-6 text-xs leading-6" style={styles.body}>
              {chrome.footer_note}
            </p>
          ) : null}
        </section>
      </div>
    </main>
  );
}

export function HostedPagePreview(props) {
  return <HostedPageFrame {...props} previewMode />;
}

export default function HostedPageShell({ title, ...props }) {
  return (
    <>
      <Head>
        <title>{title}</title>
      </Head>
      <HostedPageFrame title={title} {...props} />
    </>
  );
}
