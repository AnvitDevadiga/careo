import type { ReactNode } from "react";

/** Scroll container for wide tables: focusable and labelled so keyboard users can scroll it. */
export function TableScroll({ label, tall, children }: { label: string; tall?: boolean; children: ReactNode }) {
  return (
    <div className={`table-wrap${tall ? " tall" : ""}`} role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
