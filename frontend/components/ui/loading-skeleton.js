export default function LoadingSkeleton({ lines = 4 }) {
  return (
    <div className="animate-pulse rounded-xl border border-[#eaecf0] bg-white p-5">
      <div className="h-5 w-40 rounded bg-[#eef2f6]" />
      <div className="mt-4 space-y-2">
        {Array.from({ length: lines }).map((_, index) => (
          <div key={index} className="h-4 rounded bg-[#f2f4f7]" />
        ))}
      </div>
    </div>
  );
}
