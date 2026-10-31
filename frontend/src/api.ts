import type { AlertsResponse, AskAnswer, Digest, Dossier, Enrichment, FinnEvent, Holding, Meta, Promises, Rules, Thesis } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

const enrichCache = new Map<string, Promise<Enrichment>>();

export const api = {
  meta: () => req<Meta>("/api/meta"),
  digest: (p: { hours?: number; start?: string; end?: string; fresh?: boolean }) => {
    const q = new URLSearchParams();
    if (p.start) { q.set("start", p.start); if (p.end) q.set("end", p.end); } else q.set("hours", String(p.hours ?? 24));
    if (p.fresh) q.set("fresh", "true");
    return req<Digest>(`/api/digest?${q}`);
  },
  enrich: (e: FinnEvent): Promise<Enrichment> => {
    const key = `${e.enrich}|${e.id}`;
    if (!enrichCache.has(key)) {
      const p = req<Enrichment>("/api/enrich", {
        method: "POST",
        body: JSON.stringify({
          kind: e.enrich, symbol: e.symbol, company: e.company, url: e.url, text: e.text, ts: e.ts, desc: e.desc,
          urls: [e.url, ...e.related.map((r) => r.url)].filter(Boolean),
        }),
      });
      p.catch(() => enrichCache.delete(key));
      enrichCache.set(key, p);
    }
    return enrichCache.get(key)!;
  },
  triage: (id: string, state: "done" | "pinned" | null) =>
    req("/api/triage", { method: "POST", body: JSON.stringify({ id, state }) }),
  alerts: (days = 7) => req<AlertsResponse>(`/api/alerts?days=${days}`),
  alertsSeen: () => req("/api/alerts/seen", { method: "POST" }),
  rules: (r: Partial<Rules>) => req<Rules>("/api/rules", { method: "PUT", body: JSON.stringify(r) }),
  portfolio: (items: Holding[]) => req<Holding[]>("/api/portfolio", { method: "PUT", body: JSON.stringify(items) }),
  search: (q: string) => req<{ symbol: string; name: string }[]>(`/api/search?q=${encodeURIComponent(q)}`),
  dossier: (s: string) => req<Dossier>(`/api/research/${encodeURIComponent(s)}`),
  promises: (s: string) => req<Promises>(`/api/research/${encodeURIComponent(s)}/promises`),
  thesis: (s: string) => req<Thesis>(`/api/research/${encodeURIComponent(s)}/thesis`),
  ask: (s: string, q: string) => req<AskAnswer>(`/api/research/${encodeURIComponent(s)}/ask`, { method: "POST", body: JSON.stringify({ q }) }),
};
