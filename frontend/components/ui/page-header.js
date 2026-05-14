import { cn } from '../../lib/utils';

export default function PageHeader({ title, description, actions, className }) {
  return (
    <section className={cn('pp-page-header', className)}>
      <div className="pp-page-header flex-col gap-4 lg:flex-row lg:items-center">
        <div className="max-w-3xl">
          <h1 className="pp-page-header__title">{title}</h1>
          {description ? <p className="pp-page-header__description">{description}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </section>
  );
}
