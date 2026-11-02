import { useEffect, useState } from "react";

export type Load<T> = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; data: T };

/** Fetch a static JSON file shipped with the site (under the GitHub Pages base path). */
export function useStaticJson<T>(name: string): Load<T> {
  const [state, setState] = useState<Load<T>>({ status: "loading" });
  useEffect(() => {
    let live = true;
    fetch(`${import.meta.env.BASE_URL}data/${name}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<T>;
      })
      .then((data) => live && setState({ status: "ready", data }))
      .catch((e: unknown) => live && setState({ status: "error", message: String(e) }));
    return () => {
      live = false;
    };
  }, [name]);
  return state;
}

export const REPO_URL = "https://github.com/AnvitDevadiga/careo";

export function repoFile(path: string): string {
  return `${REPO_URL}/blob/main/${path}`;
}

export function download(name: string): string {
  return `${import.meta.env.BASE_URL}downloads/${name}`;
}
