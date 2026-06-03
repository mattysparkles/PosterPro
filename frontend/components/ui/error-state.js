import Button from './button';

export default function ErrorState({ title = 'Something went wrong', description = 'Try again.', onRetry }) {
  return (
    <div className="rounded-xl border border-[#fecdca] bg-[#fff5f4] p-5">
      <h3 className="text-sm font-semibold text-[#912018]">{title}</h3>
      <p className="mt-1 text-sm text-[#b42318]">{description}</p>
      {onRetry ? (
        <Button className="mt-3" variant="danger" size="sm" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}
