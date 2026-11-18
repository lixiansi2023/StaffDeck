import { cn } from '@/lib/utils';
import logoMark from '../assets/LOGO.svg';

export type BrandLogoProps = {
  /** Size of the square logo mark in pixels. */
  markSize?: number;
  className?: string;
};

/** Brand logo mark. */
export default function BrandLogo({ markSize = 28, className }: BrandLogoProps) {
  return (
    <span className={cn('flex items-center gap-[8px] overflow-hidden p-[4px]', className)}>
      <img
        src={logoMark}
        alt=""
        className="shrink-0"
        style={{ width: markSize, height: markSize }}
      />
    </span>
  );
}
