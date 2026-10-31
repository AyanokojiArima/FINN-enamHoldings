import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, Inbox, Search, Settings2 } from "lucide-react";
import { api } from "./api";
import type { Meta } from "./types";
import Brief from "./screens/Brief";
import Alerts from "./screens/Alerts";
import Research from "./screens/Research";
import Settings from "./screens/Settings";
import { cls } from "./lib/format";

type Tab = "brief" | "alerts" | "research" | "settings";

function parseHash(): { tab: Tab; symbol: string | null } {
  const [, tab, sym] = window.location.hash.replace(/^#/, "").split("/");
  const t = (["brief", "alerts", "research", "settings"].includes(tab) ? tab : "brief") as Tab;
  return { tab: t, symbol: t === "research" && sym ? decodeURIComponent(sym).toUpperCase() : null };
}

export default function App() {
  const [route, setRoute] = useState(parseHash);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [unseen, setUnseen] = useState(0);
  const lastUnseen = useRef(0);

  const go = (tab: Tab, symbol: string | null = null) => {
    window.location.hash = `/${tab}${symbol ? `/${encodeURIComponent(symbol)}` : ""}`;
  };

  useEffect(() => {
    const onHash = () => { setRoute(parseHash()); window.scrollTo({ top: 0 }); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const reloadMeta = useCallback(() => { api.meta().then(setMeta).catch(() => {}); }, []);
  useEffect(reloadMeta, [reloadMeta]);

  // Poll for new alerts; ping the device when something new crosses a threshold.
  useEffect(() => {
    const check = () => api.alerts(7).then((a) => {
      setUnseen(a.unseen);
      if (a.unseen > lastUnseen.current && lastUnseen.current !== 0 && typeof Notification !== "undefined" && Notification.permission === "granted") {
        const n = a.alerts.find((x) => x.new);
        if (n) new Notification(`${n.symbol}: ${n.title}`, { body: n.categoryLabel, icon: "/icon.svg", tag: n.id });
      }
      lastUnseen.current = a.unseen || -1;
    }).catch(() => {});
    check();
    const id = window.setInterval(check, 5 * 60 * 1000);
    return () => window.clearInterval(id);
  }, []);

  const onSeen = useCallback(() => setUnseen(0), []);
  const titles: Record<Tab, string> = { brief: "Brief", alerts: "Alerts", research: "Research", settings: "Settings" };

  return (
    <div className="mx-auto min-h-full max-w-[460px] px-4">
      <header className="sticky top-0 z-30 -mx-4 flex h-[52px] items-center gap-2 bg-ink-950/85 px-4 backdrop-blur">
        <img src="/icon.svg" alt="" className="h-7 w-7" />
        <span className="text-[17px] font-bold tracking-tight">FINN</span>
        <span className="text-[15px] text-dim">/ {titles[route.tab]}</span>
        <span className={cls("ml-auto flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium",
          meta?.llm.enabled ? "bg-fin/10 text-fin" : "bg-white/5 text-mute")}>
          <span className={cls("h-1.5 w-1.5 rounded-full", meta?.llm.enabled ? "bg-fin" : "bg-mute")} />
          {meta ? (meta.llm.enabled ? "AI on" : "Rules mode") : "…"}
        </span>
      </header>

      <main>
        {route.tab === "brief" && <Brief onOpenResearch={(s) => go("research", s)} />}
        {route.tab === "alerts" && <Alerts onSeen={onSeen} />}
        {route.tab === "research" && (
          <Research symbol={route.symbol} setSymbol={(s) => go("research", s)}
            picks={meta?.researchPicks ?? []} portfolio={(meta?.portfolio ?? []).map((h) => h.symbol)} />
        )}
        {route.tab === "settings" && <Settings meta={meta} reloadMeta={reloadMeta} />}
      </main>

      <nav className="fixed bottom-0 left-1/2 z-40 w-full max-w-[460px] -translate-x-1/2 border-t hairline bg-ink-950/90 backdrop-blur safe-bottom">
        <div className="grid grid-cols-4 pt-1.5">
          {([["brief", "Brief", Inbox], ["alerts", "Alerts", Bell], ["research", "Research", Search], ["settings", "Settings", Settings2]] as const).map(([t, label, Icon]) => (
            <button key={t} onClick={() => go(t)} className={cls("relative flex flex-col items-center gap-0.5 py-1.5 text-[11px] font-medium transition",
              route.tab === t ? "text-fin" : "text-mute hover:text-white")}>
              <Icon size={21} strokeWidth={route.tab === t ? 2.4 : 1.9} />
              {label}
              {t === "alerts" && unseen > 0 && (
                <span className="absolute right-[calc(50%-20px)] top-0.5 min-w-[17px] rounded-full bg-down px-1 text-[10px] font-bold leading-[17px] text-white num">{unseen > 9 ? "9+" : unseen}</span>
              )}
            </button>
          ))}
        </div>
      </nav>
    </div>
  );
}
