import { toPublicImageUrl } from '../../../lib/api';

export default function ListingsThumbnail({ src, alt, size = 'sm', roundedClassName, backgroundClassName = 'bg-[#f2f4f7]' }) {
  const sizeClassName = size === 'lg' ? 'h-16 w-16' : 'h-10 w-10';
  const radiusClassName = roundedClassName || (size === 'lg' ? 'rounded-[14px]' : 'rounded-[10px]');

  return (
    <div className={`${sizeClassName} overflow-hidden ${radiusClassName} ${backgroundClassName}`}>
      {src ? <img src={toPublicImageUrl(src)} alt={alt} className="h-full w-full object-cover" /> : null}
    </div>
  );
}
