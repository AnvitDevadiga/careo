import mark from "./wordmark-paths.json";

/** The careo. wordmark, drawn with currentColor so it follows the theme. */
export function Wordmark({ height = 28, title = "careo." }: { height?: number; title?: string }) {
  return (
    <svg viewBox={`0 0 ${mark.width} 32`} height={height} role="img" aria-label={title} className="wordmark">
      <g fill="none" stroke="currentColor" strokeWidth={mark.stroke} strokeLinecap="round" strokeLinejoin="round">
        {mark.paths.map((d) => (
          <path key={d} d={d} />
        ))}
      </g>
      <circle cx={mark.dot[0]} cy={mark.dot[1]} r={mark.dot[2]} fill="currentColor" />
    </svg>
  );
}
