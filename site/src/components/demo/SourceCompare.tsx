import { repoFile } from "../../lib/data";
import type { DemoData } from "../../lib/types";
import { CodeBlock } from "../CodeBlock";
import { Tabs } from "./Tabs";

export function SourceCompare({ demo }: { demo: DemoData }) {
  const tab = (side: "reference" | "candidate", label: string) => ({
    id: side,
    label,
    content: (
      <>
        <CodeBlock code={demo.sources[side].function} label={demo.sources[side].file} language="python" />
        <p className="small">
          <a href={repoFile(demo.sources[side].file)}>View the full file on GitHub</a>
        </p>
      </>
    ),
  });
  return (
    <Tabs
      label="Cancel handler in each implementation"
      tabs={[tab("reference", "Original: cancel()"), tab("candidate", "Rewrite: handle_cancel()")]}
    />
  );
}
