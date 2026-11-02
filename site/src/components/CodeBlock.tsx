import { useState } from "react";

interface Props {
  code: string;
  label: string;
  language?: string;
  copy?: boolean;
}

/** Preformatted code with an optional copy-to-clipboard action. */
export function CodeBlock({ code, label, language, copy = true }: Props) {
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");
  return (
    <figure className="code">
      <figcaption className="code-head">
        <span>{label}</span>
        {copy && (
          <button
            type="button"
            className="text-button"
            onClick={() => {
              navigator.clipboard
                ?.writeText(code)
                .then(() => setCopied("done"))
                .catch(() => setCopied("failed"));
            }}
          >
            {copied === "done" ? "Copied" : copied === "failed" ? "Copy failed" : "Copy"}
          </button>
        )}
      </figcaption>
      <pre tabIndex={0} aria-label={label} data-language={language}>
        <code>{code}</code>
      </pre>
      <span className="visually-hidden" aria-live="polite">
        {copied === "done" ? "Copied to clipboard" : copied === "failed" ? "Copy failed" : ""}
      </span>
    </figure>
  );
}
