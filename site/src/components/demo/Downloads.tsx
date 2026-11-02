import { download } from "../../lib/data";

const FILES = [
  ["careo-demo-report.json", "Full scenario report (JSON)"],
  ["careo-regression.json", "Minimized scenario (replayable)"],
  ["test_careo_regression.py", "Generated pytest regression test"],
] as const;

export function Downloads() {
  return (
    <ul className="downloads">
      {FILES.map(([file, label]) => (
        <li key={file}>
          <a href={download(file)} download={file}>
            {label}
          </a>{" "}
          <span className="muted">{file}</span>
        </li>
      ))}
    </ul>
  );
}
