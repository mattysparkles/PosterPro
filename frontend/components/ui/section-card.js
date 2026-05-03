import { Card, CardDescription, CardTitle } from './card';

export default function SectionCard({ title, description, action, children, className = '' }) {
  return (
    <Card className={className}>
      {(title || description || action) ? (
        <div className="flex items-start justify-between gap-4">
          <div>
            {title ? <CardTitle className="text-xl tracking-tight">{title}</CardTitle> : null}
            {description ? <CardDescription className="mt-2">{description}</CardDescription> : null}
          </div>
          {action ? <div>{action}</div> : null}
        </div>
      ) : null}
      <div className={title || description || action ? 'mt-5' : ''}>{children}</div>
    </Card>
  );
}
