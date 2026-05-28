import { getGenreColor } from '@/lib/tokens';
import { cn } from '@/lib/utils';

// Genre chip — color-coded by genre family
export default function GenreBadge({ genre, className, size = 'sm' }) {
  if (!genre) return null;

  const isNeedsReview = genre === 'NeedsReview' || genre === 'Needs Review';
  const color = isNeedsReview ? 'var(--accent-amber)' : getGenreColor(genre);
  const bgAlpha = isNeedsReview ? 'var(--accent-amber-dim)' : `${color}20`;
  const borderAlpha = `${color}30`;

  return (
    <span
      className={cn(
        'inline-flex items-center font-semibold tracking-widest uppercase rounded',
        size === 'sm' ? 'text-10 px-1.5 py-0.5' : 'text-11 px-2 py-1',
        className
      )}
      style={{
        color,
        background: bgAlpha,
        border: `1px solid ${borderAlpha}`,
      }}
    >
      {isNeedsReview ? 'Review' : genre}
    </span>
  );
}
