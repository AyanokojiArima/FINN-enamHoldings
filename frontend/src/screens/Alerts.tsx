import { useCallback, useEffect, useRef, useState } from "react";
import { BellRing, SlidersHorizontal } from "lucide-react";
import { api } from "../api";
import type { AlertsResponse, FinnEvent, Rules } from "../types";
import { EventCard } from "../components/EventCard";
import { CATEGORY_ICON, Empty, SectionTitle, Sheet, Skeleton } from "../components/ui";
import { ago, cls, dayLabel, pct, tone } from "../lib/format";

const CATS: [string, string][] = [
  ["results", "Results"], ["mna", "M&A"], ["management_change", "Management change"], ["fund_raise", "Fund raise"],
  ["litigation", "Litigation / regulatory"], ["credit_rating", "Credit rating"], ["pledging", "Promoter pledging"],
  ["insider_trading", "Insider trades"], ["capex", "Capex"], ["bonus_split", "Bonus / split"], ["dividend", "Dividend"],
  ["press_release", "Press releases & orders"], ["earnings_call", "Earnings calls"], ["investor_meetings", "Investor meetings"],
  ["agm_egm", "AGM / EGM"], ["postal_ballot", "Postal ballot"], ["unclassified", "Routine paperwork"],
];

export default function Alerts({ onSeen }: { onSeen: () => void }) {
  const [data, setData] = useState<AlertsResponse | null>(null);
  const [rules, setRules] = useState<Rules | null>(null);
  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState<FinnEvent | null>(null);
  const [notify, setNotify] = useState(typeof Notification !== "undefined" && Notification.permission === "granted");
  const saveTimer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    const a = await api.alerts(7);
    setData(a);
    setRules((r) => r ?? a.rules);
  }, []);

  useEffect(() => {
    load().then(() => api.alertsSeen().then(onSeen).catch(() => {}));
  }, [load, onSeen]);

  const update = (patch: Partial<Rules>) => {
    if (!rules) return;
    const next = { ...rules, ...patch, categories: { ...rules.categories, ...(patch.categories ?? {}) } };
    setRules(next);
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      await api.rules(next);
      await load();
    }, 450);
  };

  const enableNotify = async () => {
    if (typeof Notification === "undefined") return;
    const p = await Notification.requestPermission();
    setNotify(p === "granted");
    if (p === "granted") new Notification("FINN alerts on", { body: "I'll ping this device when something crosses your thresholds.", icon: "/icon.svg" });
  };

  return (
    <div className="pb-28">
      <section className="card mt-2 p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[13px] text-mute">{data?.stale?.asOf ? `7 days to ${dayLabel(data.stale.asOf)} · exchange feed unreachable` : "Last 7 days"}</div>
            <div className="mt-1 text-[22px] font-semibold leading-tight">
              {data ? `${data.alerts.length} alerts crossed your thresholds` : "Checking thresholds…"}
            </div>
            {rules && (
              <div className="mt-2 text-[13px] text-mute">
                Moves beyond <b className="text-white">±{rules.priceMovePct}%</b>, volume above <b className="text-white">{rules.volumeMultiple}×</b> its {rules.volumeLookback}-day average,
                and {Object.values(rules.categories).filter((v) => v === "alert").length} filing types.
              </div>
            )}
          </div>
          <button onClick={() => setEditing(true)} className="flex shrink-0 items-center gap-1.5 rounded-full bg-white/[0.06] px-3 py-2 text-[13px] font-medium hover:bg-white/10">
            <SlidersHorizontal size={15} /> Rules
          </button>
        </div>
        {typeof Notification !== "undefined" && (
          <button onClick={enableNotify} disabled={notify}
            className={cls("mt-4 flex w-full items-center justify-center gap-2 rounded-xl py-2.5 text-[13px] font-semibold",
              notify ? "bg-up/10 text-up" : "bg-fin text-ink-950")}>
            <BellRing size={15} /> {notify ? "Notifications on for this device" : "Notify me on this device"}
          </button>
        )}
      </section>

      <SectionTitle>Feed</SectionTitle>
      {!data && <div className="space-y-2"><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /></div>}
      {data && data.alerts.length === 0 && <Empty icon={<BellRing size={26} />} title="All quiet" sub="Nothing crossed your thresholds this week." />}
      <div className="card divide-y divide-white/5 overflow-hidden">
        {data?.alerts.map((a) => {
          const Icon = CATEGORY_ICON[a.category] ?? BellRing;
          return (
            <button key={a.id} onClick={() => setOpen(a)} className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-white/[0.025]">
              <div className={cls("mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
                a.category === "move" ? "bg-warn/10 text-warn" : "bg-fin/10 text-fin")}>
                <Icon size={15} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-[12px]">
                  <b>{a.symbol}</b>
                  {a.new && <span className="rounded-full bg-fin px-1.5 text-[10px] font-bold text-ink-950">NEW</span>}
                  <span className="ml-auto text-dim">{ago(a.ts)}</span>
                </div>
                <div className="clamp-2 text-[13px] leading-snug">{a.title}</div>
                <div className="mt-0.5 text-[11px] text-mute">
                  {a.trigger === "threshold" ? "Price / volume threshold" : `${a.categoryLabel} — you asked to be alerted`}
                  {a.reaction && a.category !== "move" && <span className={cls("ml-1 num", tone(a.reaction.ret))}>· stock {pct(a.reaction.ret)}</span>}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      <Sheet open={!!open} onClose={() => setOpen(null)} title={open?.symbol}>
        {open && <EventCard e={open} />}
      </Sheet>

      <Sheet open={editing} onClose={() => setEditing(false)} title="What counts as unusual?">
        {rules && (
          <div className="space-y-6">
            <Slider label="Daily price move" value={rules.priceMovePct} min={1} max={10} step={0.5} unit="%"
              hint="Alert when a holding closes up or down by at least this much." onChange={(v) => update({ priceMovePct: v })} />
            <Slider label="Volume spike" value={rules.volumeMultiple} min={1.2} max={5} step={0.1} unit="×"
              hint={`Alert when volume is this multiple of its ${rules.volumeLookback}-session average.`} onChange={(v) => update({ volumeMultiple: v })} />
            <label className="flex items-center justify-between gap-3">
              <div><div className="text-[14px] font-medium">52-week extremes</div><div className="text-[12px] text-mute">Fresh 52-week closing highs and lows.</div></div>
              <input type="checkbox" checked={rules.near52w} onChange={(e) => update({ near52w: e.target.checked })} className="h-5 w-5 accent-[var(--color-fin)]" />
            </label>
            <div>
              <div className="mb-2 text-[14px] font-medium">Filing types</div>
              <div className="space-y-1.5">
                {CATS.map(([k, label]) => (
                  <div key={k} className="flex items-center gap-2">
                    <span className="flex-1 text-[13px] text-white/85">{label}</span>
                    <div className="flex rounded-full bg-white/[0.05] p-0.5 text-[11px] font-semibold">
                      {(["alert", "digest", "mute"] as const).map((v) => (
                        <button key={v} onClick={() => update({ categories: { [k]: v } as Rules["categories"] })}
                          className={cls("rounded-full px-2.5 py-1 capitalize transition",
                            rules.categories[k] === v ? (v === "alert" ? "bg-fin text-ink-950" : v === "mute" ? "bg-white/15 text-white" : "bg-white/10 text-white") : "text-mute")}>
                          {v}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <p className="mt-3 text-[12px] text-mute">Alert = push + top of brief. Digest = in the brief at its natural priority. Mute = never shown.</p>
            </div>
          </div>
        )}
      </Sheet>
    </div>
  );
}

function Slider({ label, value, min, max, step, unit, hint, onChange }: {
  label: string; value: number; min: number; max: number; step: number; unit: string; hint: string; onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-[14px] font-medium">{label}</span>
        <span className="text-[18px] font-semibold text-fin num">{unit === "%" ? "±" : ""}{value}{unit}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="mt-2 w-full" />
      <div className="text-[12px] text-mute">{hint}</div>
    </div>
  );
}
