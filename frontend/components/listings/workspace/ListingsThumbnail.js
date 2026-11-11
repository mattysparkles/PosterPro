import { toThumbnailImageUrl } from '../../../lib/api';

export default function ListingsThumbnail({ src, alt, size = 'sm', roundedClassName, backgroundClassName = 'bg-[#f2f4f7]' }) {
  const sizeClassName = size === 'lg' ? 'h-16 w-16' : 'h-10 w-10';
  const radiusClassName = roundedClassName || (size === 'lg' ? 'rounded-[14px]' : 'rounded-[10px]');

  return (
    <div className={`${sizeClassName} overflow-hidden ${radiusClassName} ${backgroundClassName}`}>
      {src ? <img src={toThumbnailImageUrl(src, size === 'lg' ? 160 : 96, size === 'lg' ? 160 : 96)} alt={alt} loading="lazy" decoding="async" className="h-full w-full object-cover" /> : null}
    </div>
  );
}
