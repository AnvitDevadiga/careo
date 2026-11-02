import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

interface Tab {
  id: string;
  label: string;
  content: ReactNode;
}

/** Accessible tabs (WAI-ARIA pattern: arrow keys, Home/End, roving tabindex). */
export function Tabs({ tabs, label }: { tabs: Tab[]; label: string }) {
  const [active, setActive] = useState(0);
  const base = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const focus = (i: number) => {
    const n = (i + tabs.length) % tabs.length;
    setActive(n);
    refs.current[n]?.focus();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowRight") focus(active + 1);
    else if (e.key === "ArrowLeft") focus(active - 1);
    else if (e.key === "Home") focus(0);
    else if (e.key === "End") focus(tabs.length - 1);
    else return;
    e.preventDefault();
  };
  return (
    <div className="tabs">
      <div role="tablist" aria-label={label} className="tablist" onKeyDown={onKey}>
        {tabs.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            type="button"
            id={`${base}-tab-${t.id}`}
            aria-selected={i === active}
            aria-controls={`${base}-panel-${t.id}`}
            tabIndex={i === active ? 0 : -1}
            onClick={() => setActive(i)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tabs.map((t, i) => (
        <div
          key={t.id}
          role="tabpanel"
          id={`${base}-panel-${t.id}`}
          aria-labelledby={`${base}-tab-${t.id}`}
          hidden={i !== active}
        >
          {t.content}
        </div>
      ))}
    </div>
  );
}
