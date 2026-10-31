import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CalendarDays, CheckCheck, ChevronDown, Headphones, Inbox, RefreshCw, Square } from "lucide-react";
import { api } from "../api";
import type { Digest, FinnEvent, ResultsCard, PeopleCard, SummaryCard } from "../types";
import { EventCard } from "../components/EventCard";
import { Empty, Pill, SectionTitle, Skeleton, Sparkline } from "../components/ui";
import { ago, cls, dayLabel, pct, timeLabel, tone } from "../lib/format";
import { canSpeak, speak } from "../lib/speech";

type Win = { key: string; hours?: number; start?: string; end?: string; label: string };
const WINDOWS: Win[] = [
  { key: "24h", hours: 24, label: "24H" },
  { key: "3d", hours: 72, label: "3D" },
  { key: "7d", hours: 168, label: "7D" },
];

function spokenPct(v: number | null | undefined) {
  if (v == null) return "";
  return `${v >= 0 ? "up" : "down"} ${Math.abs(v).toFixed(0)} percent`;
}

async function scriptFor(d: Digest, win: string): Promise<string[]> {
  const live = d.top.filter((e) => e.triage !== "done");
  const out = [
    `Here's your FINN brief for the ${win}. ${live.length || "Nothing"} ${live.length === 1 ? "thing needs" : "things need"} you, ` +
      `${d.watch.length} ${d.watch.length === 1 ? "is" : "are"} worth a skim, and I've hidden ${d.stats.routine} routine filings.`,
  ];
  for (const e of live.slice(0, 5)) {
    let line = `${e.company}. ${e.title}.`;
    try {
      if (e.enrich) {
        const x = await api.enrich(e);
        if (x.kind === "results" && (x as ResultsCard).rows) {
          const r = x as ResultsCard;
          const rev = r.rows![0], pat = r.rows![3];
          line += ` ${rev.label} ${spokenPct(rev.yoy)} year on year, to ${Math.round(rev.value ?? 0).toLocaleString("en-IN")} crore. Profit after tax ${spokenPct(pat.yoy)}.`;
        } else if (x.kind === "people") {
          const p = x as PeopleCard;
          line += p.oneLine ? ` ${p.oneLine}` : p.changes[0] ? ` ${p.changes[0].name}, ${p.changes[0].role ?? ""}, ${p.changes[0].action}.` : "";
        } else if (x.kind === "summary" && (x as SummaryCard).summary) {
          line += ` ${(x as SummaryCard).summary}`;
        }
      }
    } catch { /* speak what we have */ }
    if (e.reaction && Math.abs(e.reaction.ret) >= 1 && e.category !== "move") line += ` The stock moved ${spokenPct(e.reaction.ret)}.`;
    out.push(line);
  }
  if (d.upcoming.length) {
    const u = d.upcoming.slice(0, 3).map((x) => `${x.symbol} ${x.label.toLowerCase()} on ${new Date(x.date).toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}`);
    out.push(`Coming up: ${u.join("; ")}.`);
  }
  out.push("That's everything that matters. The rest is routine.");
  return out;
}

export default function Brief({ onOpenResearch }: { onOpenResearch: (s: string) => void }) {
  const [win, setWin] = useState<Win>(WINDOWS[0]);
  const [d, setD] = useState<Digest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [custom, setCustom] = useState(false);
  const [routineOpen, setRoutineOpen] = useState<string | null>(null);
  const [showDone, setShowDone] = useState(false);
  const [speaking, setSpeaking] = useState<{ i: number; lines: string[] } | null>(null);
  const stopRef = useRef<() => void>(() => {});

  const load = useCallback(async (w: Win, fresh = false) => {
    setLoading(true);
    setError(null);
    try {
      setD(await api.digest({ hours: w.hours, start: w.start, end: w.end, fresh }));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(win); }, [win, load]);
  useEffect(() => () => stopRef.current(), []);

  const triage = (e: FinnEvent, state: "done" | "pinned" | null) => {
    setD((cur) => cur && {
      ...cur,
      top: cur.top.map((x) => (x.id === e.id ? { ...x, triage: state } : x)),
      watch: cur.watch.map((x) => (x.id === e.id ? { ...x, triage: state } : x)),
    });
    api.triage(e.id, state).catch(() => {});
  };

  const top = useMemo(() => {
    const t = d?.top ?? [];
    return [...t.filter((e) => e.triage === "pinned"), ...t.filter((e) => !e.triage)];
  }, [d]);
  const done = useMemo(() => [...(d?.top ?? []), ...(d?.watch ?? [])].filter((e) => e.triage === "done"), [d]);
  const watch = useMemo(() => (d?.watch ?? []).filter((e) => e.triage !== "done"), [d]);
  const pulse = useMemo(() => [...(d?.pulse ?? [])].sort((a, b) => Math.abs(b.chg ?? 0) - Math.abs(a.chg ?? 0)), [d]);

  const listen = async () => {
    if (speaking) { stopRef.current(); setSpeaking(null); return; }
    if (!d) return;
    const lines = await scriptFor(d, win.key === "24h" ? "last 24 hours" : win.key === "3d" ? "last 3 days" : win.key === "7d" ? "last week" : "selected period");
    setSpeaking({ i: 0, lines });
    stopRef.current = speak(lines, (i) => setSpeaking({ i, lines }), () => setSpeaking(null));
  };

  return (
    <div className="pb-28">
      {/* window selector */}
      <div className="sticky top-[52px] z-20 -mx-4 bg-ink-950/85 px-4 py-2 backdrop-blur">
        <div className="flex items-center gap-2 overflow-x-auto no-scrollbar">
          {WINDOWS.map((w) => <Pill key={w.key} active={win.key === w.key} onClick={() => { setCustom(false); setWin(w); }}>{w.label}</Pill>)}
          <Pill active={win.key === "custom"} onClick={() => setCustom(!custom)}><span className="flex items-center gap-1"><CalendarDays size={13} /> Custom</span></Pill>
          <button onClick={() => load(win, true)} className="ml-auto rounded-full p-2 text-mute hover:text-white" aria-label="Refresh">
            <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
        {custom && <CustomRange onApply={(s, e) => { setWin({ key: "custom", start: s, end: e, label: "Custom" }); setCustom(false); }} />}
      </div>

      {/* hero */}
      <section className="card mt-2 overflow-hidden p-5">
        {loading && !d ? (
          <div className="space-y-3"><Skeleton className="h-8 w-40" /><Skeleton className="h-4 w-56" /></div>
        ) : d ? (
          <>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-[13px] text-mute">
                  {d.stale?.asOf
                    ? `${win.key === "custom" ? "Window" : win.label.replace("H", " hours").replace("D", " days")} to ${dayLabel(d.stale.asOf)}, ${timeLabel(d.stale.asOf)}`
                    : win.key === "custom" ? "Selected window" : `Last ${win.label.replace("H", " hours").replace("D", " days")}`}
                </div>
                <div className="mt-1 flex items-baseline gap-2">
                  <span className="text-[44px] font-semibold leading-none tracking-tight num">{top.length}</span>
                  <span className="text-[17px] font-medium text-white/90">{top.length === 1 ? "thing needs you" : "things need you"}</span>
                </div>
                <div className="mt-2 text-[13px] text-mute">
                  {watch.length} worth a skim · <span className="text-white/70">{d.stats.routine} routine filings hidden</span>
                </div>
              </div>
              {canSpeak() && (
                <button onClick={listen} className={cls("flex shrink-0 items-center gap-1.5 rounded-full px-3.5 py-2 text-[13px] font-semibold transition",
                  speaking ? "bg-white text-ink-950" : "bg-fin text-ink-950 hover:brightness-110")}>
                  {speaking ? <><Square size={14} /> Stop</> : <><Headphones size={15} /> Listen</>}
                </button>
              )}
            </div>
            {d.stale && (
              <div className="mt-3 rounded-xl bg-warn/10 px-3 py-2 text-[12px] leading-snug text-warn">
                {d.stale.reason}{d.stale.asOf ? ` Showing the latest filings FINN holds, to ${dayLabel(d.stale.asOf)} ${timeLabel(d.stale.asOf)}.` : ""}
              </div>
            )}
            <div className="mt-4 flex items-end justify-between gap-3 border-t hairline pt-3 text-[12px] text-mute">
              <div>
                <div className="text-[11px] uppercase tracking-wider">{d.stale ? "Portfolio, last close" : "Portfolio today"}</div>
                <div className={cls("text-[17px] font-semibold num", tone(d.portfolioDayChange))}>{pct(d.portfolioDayChange, 2)}</div>
              </div>
              <div className="text-right text-[11.5px] leading-relaxed">
                <div>{d.stats.filings} filings scanned</div>
                <div className="text-dim">updated {ago(d.generatedAt)}</div>
              </div>
            </div>
          </>
        ) : (
          <div className="text-sm text-down">Couldn't load the brief: {error}</div>
        )}
      </section>

      {speaking && (
        <div className="card mt-3 border-fin/30 bg-fin/[0.06] p-3 text-[13px] fade-in">
          <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-fin">
            <span className="h-1.5 w-1.5 rounded-full bg-fin pulse-dot" /> Reading {speaking.i + 1}/{speaking.lines.length}
          </div>
          {speaking.lines[speaking.i]}
        </div>
      )}

      {/* pulse */}
      {d && (
        <div className="-mx-4 mt-4 flex gap-2 overflow-x-auto px-4 pb-1 no-scrollbar">
          {pulse.map((p) => (
            <button key={p.symbol} onClick={() => onOpenResearch(p.symbol)}
              className="card relative min-w-[118px] shrink-0 px-3 py-2.5 text-left hover:border-white/15">
              {p.events > 0 && <span className="absolute right-2.5 top-2.5 h-1.5 w-1.5 rounded-full bg-warn" />}
              <div className="text-[12px] font-bold tracking-wide">{p.symbol}</div>
              <div className={cls("text-[13px] font-semibold num", tone(p.chg))}>{pct(p.chg, 1)}</div>
              <div className="mt-1"><Sparkline data={p.spark} width={92} height={22} stroke="rgba(232,237,242,.45)" fill={false} /></div>
              {p.vmult != null && p.vmult >= 1.8 && <div className="mt-0.5 text-[10px] text-warn num">{p.vmult.toFixed(1)}× vol</div>}
            </button>
          ))}
        </div>
      )}

      {/* needs you */}
      <SectionTitle right={done.length > 0 && (
        <button onClick={() => setShowDone(!showDone)} className="flex items-center gap-1 text-[12px] text-mute hover:text-white">
          <CheckCheck size={13} /> {done.length} cleared
        </button>
      )}>Needs you</SectionTitle>
      {loading && !d && <div className="space-y-3"><Skeleton className="h-40 w-full rounded-[20px]" /><Skeleton className="h-40 w-full rounded-[20px]" /></div>}
      {d && top.length === 0 && (
        <Empty icon={<Inbox size={28} />} title={done.length ? "You're caught up" : "Nothing material"}
          sub={done.length ? "Everything important in this window is triaged." : "No filing or price move in your portfolio crossed the bar. Try a wider window."} />
      )}
      <div className="space-y-3">
        {top.map((e) => <EventCard key={e.id} e={e} onTriage={(s) => triage(e, s)} />)}
      </div>
      {d && top.length > 0 && <p className="mt-2 text-center text-[11px] text-dim">Swipe left when done · right to pin</p>}
      {showDone && done.length > 0 && (
        <div className="mt-3 space-y-2 opacity-60">
          {done.map((e) => <EventCard key={e.id} e={e} compact defaultOpen={false} onTriage={(s) => triage(e, s)} />)}
        </div>
      )}

      {/* worth a skim */}
      {d && watch.length > 0 && (
        <>
          <SectionTitle>Worth a skim</SectionTitle>
          <div className="space-y-2">
            {watch.map((e) => <EventCard key={e.id} e={e} compact defaultOpen={false} onTriage={(s) => triage(e, s)} />)}
          </div>
        </>
      )}

      {/* coming up */}
      {d && d.upcoming.length > 0 && (
        <>
          <SectionTitle>Coming up</SectionTitle>
          <div className="card divide-y divide-white/5">
            {d.upcoming.map((u, i) => (
              <a key={i} href={u.url ?? undefined} target="_blank" rel="noreferrer" className="flex items-center gap-3 px-4 py-3 hover:bg-white/[0.02]">
                <div className="w-12 shrink-0 text-center">
                  <div className="text-[10px] uppercase text-mute">{new Date(u.date).toLocaleDateString("en-IN", { month: "short" })}</div>
                  <div className="text-lg font-semibold leading-none num">{new Date(u.date).getDate()}</div>
                </div>
                <div className="min-w-0">
                  <div className="text-[13px]"><b>{u.symbol}</b> · {u.label}</div>
                  <div className="truncate text-[12px] text-mute">{u.detail}</div>
                </div>
              </a>
            ))}
          </div>
        </>
      )}

      {/* routine */}
      {d && d.routine.length > 0 && (
        <>
          <SectionTitle>Hidden as routine · {d.stats.routine}</SectionTitle>
          <div className="card divide-y divide-white/5">
            {d.routine.map((g) => (
              <div key={g.label}>
                <button onClick={() => setRoutineOpen(routineOpen === g.label ? null : g.label)} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-white/[0.02]">
                  <span className="text-[13px]">{g.label}</span>
                  <span className="rounded-full bg-white/5 px-2 py-0.5 text-[11px] text-mute num">{g.count}</span>
                  <ChevronDown size={15} className={cls("ml-auto text-mute transition", routineOpen === g.label && "rotate-180")} />
                </button>
                {routineOpen === g.label && (
                  <ul className="space-y-2 px-4 pb-3">
                    {g.items.map((it) => (
                      <li key={it.id} className="flex items-baseline gap-2 text-[12px]">
                        <b className="shrink-0 text-white/80">{it.symbol}</b>
                        {it.url ? <a href={it.url} target="_blank" rel="noreferrer" className="truncate text-mute hover:text-white">{it.title}</a> : <span className="truncate text-mute">{it.title}</span>}
                        <span className="ml-auto shrink-0 text-dim">{dayLabel(it.ts)} {timeLabel(it.ts)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function CustomRange({ onApply }: { onApply: (start: string, end: string) => void }) {
  const fmt = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const [s, setS] = useState(fmt(new Date(Date.now() - 2 * 86400000)));
  const [e, setE] = useState(fmt(new Date()));
  return (
    <div className="card mt-2 flex flex-wrap items-end gap-2 p-3 fade-in">
      <label className="flex-1 text-[11px] text-mute">From<input type="datetime-local" value={s} onChange={(x) => setS(x.target.value)} className="mt-1 w-full rounded-lg bg-ink-800 px-2 py-1.5 text-[13px] text-white" /></label>
      <label className="flex-1 text-[11px] text-mute">To<input type="datetime-local" value={e} onChange={(x) => setE(x.target.value)} className="mt-1 w-full rounded-lg bg-ink-800 px-2 py-1.5 text-[13px] text-white" /></label>
      <button onClick={() => onApply(s, e)} className="rounded-lg bg-fin px-3 py-1.5 text-[13px] font-semibold text-ink-950">Apply</button>
    </div>
  );
}
