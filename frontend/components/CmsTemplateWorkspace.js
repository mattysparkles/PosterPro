import CmsBlockBuilder from './CmsBlockBuilder';
import { HostedPagePreview } from './HostedPageShell';
import Button from './ui/button';
import FormSection from './ui/form-section';
import Input from './ui/input';
import StatusPill from './ui/status-pill';
import { CMS_PAGE_CONFIG, STANDARD_CMS_TEMPLATE_PACK, createDefaultDraftPage, getCmsPageDefinition } from '../lib/cmsTemplates';

function EditorSidebar({ pages, activePageKey, onSelectPage, onApplyTemplatePack, canManageServer }) {
  return (
    <aside className="space-y-4">
      <div className="rounded-[18px] border border-[#d6dae3] bg-[#f6f7fb] p-4">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#667085]">Template pack</p>
        <p className="mt-2 text-sm font-semibold text-[#101828]">{STANDARD_CMS_TEMPLATE_PACK.name}</p>
        <p className="mt-1 text-sm leading-6 text-[#667085]">{STANDARD_CMS_TEMPLATE_PACK.description}</p>
        <Button type="button" className="mt-4 w-full" disabled={!canManageServer} onClick={onApplyTemplatePack}>
          Apply standard pack
        </Button>
      </div>

      <div className="overflow-hidden rounded-[18px] border border-[#d6dae3] bg-white">
        <div className="border-b border-[#eaecf0] bg-[#f8fafc] px-4 py-3">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#667085]">Pages</p>
        </div>
        <div className="divide-y divide-[#eaecf0]">
          {CMS_PAGE_CONFIG.map((entry) => {
            const pageRecord = pages?.[entry.key] || createDefaultDraftPage(entry.key);
            const active = activePageKey === entry.key;
            return (
              <button
                key={entry.key}
                type="button"
                onClick={() => onSelectPage(entry.key)}
                className={`flex w-full flex-col gap-2 px-4 py-4 text-left transition ${active ? 'bg-[#eef4ff]' : 'bg-white hover:bg-[#f8fafc]'}`}
              >
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold text-[#101828]">{entry.label}</p>
                  <StatusPill status={pageRecord.status === 'published' ? 'success' : 'warning'} label={pageRecord.status === 'published' ? 'Published' : 'Draft'} />
                </div>
                <p className="text-xs text-[#667085]">/{pageRecord.route_group || entry.routeGroup}/{pageRecord.slug || entry.slug}</p>
                <p className="text-xs leading-5 text-[#667085]">{entry.templateName}</p>
              </button>
            );
          })}
        </div>
      </div>
    </aside>
  );
}

function TemplateSummary({ pageRecord, pageDefinition, onApplyTemplate, canManageServer, liveUrl }) {
  const published = pageRecord.published || {};
  return (
    <div className="space-y-4">
      <div className="rounded-[18px] border border-[#d6dae3] bg-[#f6f7fb] p-4">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#667085]">Template</p>
        <p className="mt-2 text-sm font-semibold text-[#101828]">{pageDefinition.templateName}</p>
        <p className="mt-1 text-sm leading-6 text-[#667085]">{pageDefinition.description}</p>
        <Button type="button" variant="outline" className="mt-4 w-full" disabled={!canManageServer} onClick={onApplyTemplate}>
          Reset draft to template
        </Button>
      </div>

      <div className="rounded-[18px] border border-[#e5e7eb] bg-white p-4">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#667085]">Live URL</p>
        <p className="mt-2 break-all text-sm text-[#101828]">{liveUrl || 'Set APP_BASE_URL first to generate the public URL.'}</p>
      </div>

      <div className="rounded-[18px] border border-[#e5e7eb] bg-white p-4">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#667085]">Published snapshot</p>
        <p className="mt-2 text-sm font-semibold text-[#101828]">{published.title || 'No published title yet'}</p>
        <p className="mt-1 text-sm text-[#667085]">Live blocks: {Array.isArray(published.blocks) ? published.blocks.length : 0}</p>
        {pageRecord.published_at ? <p className="mt-1 text-xs text-[#667085]">{`Published ${new Date(pageRecord.published_at).toLocaleString()}`}</p> : null}
      </div>
    </div>
  );
}

export default function CmsTemplateWorkspace({
  pages,
  activePageKey,
  onSelectPage,
  onUpdatePage,
  onApplyTemplate,
  onApplyTemplatePack,
  canManageServer,
  previewPage,
  previewBrandName,
  previewTitle,
  previewStatusTone,
  previewStatusMessage,
  activeTheme,
  liveUrl,
}) {
  const pageDefinition = getCmsPageDefinition(activePageKey);
  const pageRecord = pages?.[activePageKey] || createDefaultDraftPage(activePageKey);
  const draft = pageRecord.draft || createDefaultDraftPage(activePageKey).draft;

  return (
    <div className="grid gap-5 xl:grid-cols-[280px_minmax(0,1fr)]">
      <EditorSidebar
        pages={pages}
        activePageKey={activePageKey}
        onSelectPage={onSelectPage}
        onApplyTemplatePack={onApplyTemplatePack}
        canManageServer={canManageServer}
      />

      <div className="grid gap-5 2xl:grid-cols-[minmax(0,1.15fr)_360px]">
        <div className="space-y-4 rounded-[20px] border border-[#d6dae3] bg-white p-5 shadow-[0_20px_60px_rgba(15,23,42,0.06)]">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[#eaecf0] pb-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#667085]">Editor</p>
              <h3 className="mt-2 text-lg font-semibold tracking-[-0.02em] text-[#101828]">{pageDefinition.label}</h3>
              <p className="mt-1 max-w-[720px] text-sm leading-6 text-[#667085]">{pageDefinition.description}</p>
            </div>
            <StatusPill status={pageRecord.status === 'published' ? 'success' : 'warning'} label={pageRecord.status === 'published' ? 'Published' : 'Draft'} />
          </div>

          <FormSection title="Permalink + summary" description="Manage the page slug, title, and supporting summary copy just like a standard CMS edit screen.">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">URL slug</label>
                <Input value={pageRecord.slug || ''} onChange={(event) => onUpdatePage(activePageKey, (currentPage) => ({ ...currentPage, slug: event.target.value }))} />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Draft title</label>
                <Input
                  value={draft.title || ''}
                  onChange={(event) =>
                    onUpdatePage(activePageKey, (currentPage) => ({
                      ...currentPage,
                      draft: { ...(currentPage.draft || {}), title: event.target.value },
                    }))
                  }
                />
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-[#101828]">Draft summary</label>
              <textarea
                value={draft.summary || ''}
                onChange={(event) =>
                  onUpdatePage(activePageKey, (currentPage) => ({
                    ...currentPage,
                    draft: { ...(currentPage.draft || {}), summary: event.target.value },
                  }))
                }
                className="min-h-28 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
              />
            </div>
          </FormSection>

          <FormSection title="Hero" description="Set the eyebrow, hero headline, and introduction shown above the content blocks.">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Hero eyebrow</label>
                <Input
                  value={draft.hero?.eyebrow || ''}
                  onChange={(event) =>
                    onUpdatePage(activePageKey, (currentPage) => ({
                      ...currentPage,
                      draft: {
                        ...(currentPage.draft || {}),
                        hero: { ...(currentPage.draft?.hero || {}), eyebrow: event.target.value },
                      },
                    }))
                  }
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Hero title</label>
                <Input
                  value={draft.hero?.title || ''}
                  onChange={(event) =>
                    onUpdatePage(activePageKey, (currentPage) => ({
                      ...currentPage,
                      draft: {
                        ...(currentPage.draft || {}),
                        hero: { ...(currentPage.draft?.hero || {}), title: event.target.value },
                      },
                    }))
                  }
                />
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-[#101828]">Hero body</label>
              <textarea
                value={draft.hero?.body || ''}
                onChange={(event) =>
                  onUpdatePage(activePageKey, (currentPage) => ({
                    ...currentPage,
                    draft: {
                      ...(currentPage.draft || {}),
                      hero: { ...(currentPage.draft?.hero || {}), body: event.target.value },
                    },
                  }))
                }
                className="min-h-24 w-full rounded-[10px] border border-[#e5e7eb] bg-white p-3 text-sm text-[#101828] outline-none transition placeholder:text-[#98a2b3] focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
              />
            </div>
          </FormSection>

          <FormSection title="Calls to action" description="Keep the primary and secondary actions explicit so the hosted page has a clean next step.">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Primary CTA label</label>
                <Input
                  value={draft.primary_button?.label || ''}
                  onChange={(event) =>
                    onUpdatePage(activePageKey, (currentPage) => ({
                      ...currentPage,
                      draft: {
                        ...(currentPage.draft || {}),
                        primary_button: { ...(currentPage.draft?.primary_button || {}), label: event.target.value },
                      },
                    }))
                  }
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Primary CTA href</label>
                <Input
                  value={draft.primary_button?.href || ''}
                  onChange={(event) =>
                    onUpdatePage(activePageKey, (currentPage) => ({
                      ...currentPage,
                      draft: {
                        ...(currentPage.draft || {}),
                        primary_button: { ...(currentPage.draft?.primary_button || {}), href: event.target.value },
                      },
                    }))
                  }
                />
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Secondary CTA label</label>
                <Input
                  value={draft.secondary_button?.label || ''}
                  onChange={(event) =>
                    onUpdatePage(activePageKey, (currentPage) => ({
                      ...currentPage,
                      draft: {
                        ...(currentPage.draft || {}),
                        secondary_button: { ...(currentPage.draft?.secondary_button || {}), label: event.target.value },
                      },
                    }))
                  }
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-[#101828]">Secondary CTA href</label>
                <Input
                  value={draft.secondary_button?.href || ''}
                  onChange={(event) =>
                    onUpdatePage(activePageKey, (currentPage) => ({
                      ...currentPage,
                      draft: {
                        ...(currentPage.draft || {}),
                        secondary_button: { ...(currentPage.draft?.secondary_button || {}), href: event.target.value },
                      },
                    }))
                  }
                />
              </div>
            </div>
          </FormSection>

          <FormSection title="Blocks" description="Build the body with repeatable sections so new templates can be dropped in without changing the renderer.">
            <CmsBlockBuilder
              blocks={draft.blocks || []}
              onChange={(nextBlocks) =>
                onUpdatePage(activePageKey, (currentPage) => ({
                  ...currentPage,
                  draft: { ...(currentPage.draft || {}), blocks: nextBlocks },
                }))
              }
            />
          </FormSection>
        </div>

        <div className="space-y-4">
          <TemplateSummary
            pageRecord={pageRecord}
            pageDefinition={pageDefinition}
            onApplyTemplate={() => onApplyTemplate(activePageKey)}
            canManageServer={canManageServer}
            liveUrl={liveUrl}
          />
          <div className="rounded-[18px] border border-[#e5e7eb] bg-white p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#667085]">Preview</p>
            <p className="mt-2 text-sm text-[#667085]">This renders the active draft inside the same hosted shell used by the public route.</p>
            <div className="mt-4 max-h-[760px] overflow-auto rounded-[24px] border border-[#d0d5dd] bg-[#f8fafc] p-3">
              <HostedPagePreview
                page={{
                  ...(previewPage || {}),
                  theme: activeTheme || previewPage?.theme || {},
                }}
                title={previewTitle}
                brandName={previewBrandName}
                statusTone={previewStatusTone}
                statusMessage={previewStatusMessage}
                primaryHref="/"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
