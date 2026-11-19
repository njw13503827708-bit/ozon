import { useEffect, useState } from "react";

// Keep page state in the existing URL; preserve workbench handoff parameters.
export function usePageParams(page: string) {
  const read = () => window.location.hash.split("?")[1] || "";
  const [query, setQuery] = useState(read);
  useEffect(() => {
    const restore = () => setQuery(read());
    window.addEventListener("hashchange", restore);
    window.addEventListener("popstate", restore);
    return () => { window.removeEventListener("hashchange", restore); window.removeEventListener("popstate", restore); };
  }, []);
  function update(values: Record<string, string | number>, replace = false) {
    const params = new URLSearchParams();
    Object.entries(values).forEach(([key, value]) => { if (String(value)) params.set(key, String(value)); });
    const next = params.toString();
    const url = `${window.location.pathname}${window.location.search}#${page}${next ? `?${next}` : ""}`;
    if (url !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history[replace ? "replaceState" : "pushState"](null, "", url);
    }
    setQuery(next);
  }
  return [query, update] as const;
}

export function positivePage(value: string | null) {
  return value && /^[1-9]\d{0,3}$/.test(value) ? Number(value) : 1;
}
export function option(value: string | null, allowed: string[]) { return value && allowed.includes(value) ? value : ""; }
export function identifier(value: string | null) { return value && /^[\w:-]{1,128}$/.test(value) ? value : ""; }
export function searchText(value: string | null) { return (value || "").replace(/[\x00-\x1f\x7f]/g, "").slice(0, 200); }
export function validDay(value: string | null, max: string) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value > max) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}
