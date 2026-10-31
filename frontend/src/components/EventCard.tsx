import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Check, ChevronDown, ExternalLink, Info, Pin, PinOff, Sparkles, TriangleAlert } from "lucide-react";
import { api } from "../api";
import type { Enrichment, FinnEvent, PeopleCard, ResultsCard, SummaryCard } from "../types";
import { ago, cls, cr, haptic, pct, tone } from "../lib/format";
import { CategoryBadge, LinkedInBadge, Skeleton, Sparkline } from "./ui";

// ----------------------------------------------------------------------------- enrichment bodies
function SourceBadge({ s }: { s: ResultsCard["source"] }) {
  if (s.type === "nse_xbrl")
    return <span className="inline-flex items-center gap-1 text-[11px] text-fin"><Check size={12} /> Exchange XBRL</span>;
  if (s.type === "pdf_llm")
    return <span className="inline-flex items-center gap-1 text-[11px] text-warn"><Sparkles size={12} /> AI first cut · {s.grounded} figures matched in PDF</span>;
  return <span className="text-[11px] text-mute">{s.label}</span>;
}

export function ResultsBody({ r }: { r: ResultsCard }) {
  if (r.pending || !r.rows)
    return <div className="mt-3 rounded-xl bg-white/[0.03] p-3 text-sm text-mute">{r.message ?? "Numbers not available yet."}</div>;
  const m = r.margin;
  const mDelta = m?.current != null && m?.yoy != null ? (m.current - m.yoy) * 100 : null;
  return (
    <div className="mt-3">
      <div className="flex items-center justify-between text-[11px] text-mute">
        <span>{r.quarter} · {r.basis} · ₹ crore</span>
        <SourceBadge s={r.source} />
      </div>
      <div className="mt-2 overflow-hidden rounded-xl border hairline">
        <div className="grid grid-cols-[1.25fr_1.2fr_.8fr_.8fr] bg-white/[0.03] px-3 py-1.5 text-[11px] text-mute">
          <span />
          <span className="text-right">{r.periods?.current}</span>
          <span className="text-right">QoQ</span>
          <span className="text-right">YoY</span>
        </div>
        {r.rows.map((row) => (
          <div key={row.key} className="grid grid-cols-[1.25fr_1.2fr_.8fr_.8fr] items-center border-t hairline px-3 py-2 text-[13px] num">
            <span className="text-mute">{row.label}</span>
            <span className="text-right font-semibold">{cr(row.value)}</span>
            <span className={cls("text-right", tone(row.qoq))}>{pct(row.qoq, 0)}</span>
            <span className={cls("text-right font-medium", tone(row.yoy))}>{pct(row.yoy, 0)}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]">
        {m?.current != null && !r.bank && (
          <span className="text-mute">EBITDA margin <b className="text-white num">{m.current.toFixed(1)}%</b>
            {mDelta != null && <span className={cls("ml-1 num", tone(mDelta))}>{mDelta > 0 ? "+" : "−"}{Math.abs(mDelta).toFixed(0)} bps YoY</span>}
          </span>
        )}
        {r.nii?.current != null && (
          <span className="text-mute">NII <b className="text-white num">{cr(r.nii.current)}</b> <span className={tone(r.nii.yoy)}>{pct(r.nii.yoy, 0)} YoY</span></span>
        )}
      </div>
      {r.notes?.map((n) => <div key={n} className="mt-1 flex items-center gap-1 text-[11px] text-warn"><Info size={11} />{n}</div>)}
      {r.segments && r.segments.length > 1 && <Segments segs={r.segments} />}
    </div>
  );
}

function Segments({ segs }: { segs: { name: string; revenue: number }[] }) {
  const [open, setOpen] = useState(false);
  const total = segs.reduce((a, s) => a + Math.max(0, s.revenue), 0) || 1;
  const sorted = [...segs].sort((a, b) => b.revenue - a.revenue);
  return (
    <div className="mt-2">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1 text-[12px] text-mute hover:text-white">
        Segment mix <ChevronDown size={13} className={cls("transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mt-2 space-y-1.5">
          {sorted.slice(0, 6).map((s) => (
            <div key={s.name} className="text-[12px]">
              <div className="flex justify-between"><span className="truncate pr-2 text-mute">{s.name}</span><span className="num">{((s.revenue / total) * 100).toFixed(0)}%</span></div>
              <div className="mt-0.5 h-1.5 rounded-full bg-white/5"><div className="h-full rounded-full bg-fin/70" style={{ width: `${(s.revenue / total) * 100}%` }} /></div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const ACTION_STYLE: Record<string, string> = {
  appointed: "bg-up/15 text-up", "re-appointed": "bg-up/15 text-up", redesignated: "bg-info/15 text-info",
  resigned: "bg-down/15 text-down", ceased: "bg-down/15 text-down", retired: "bg-warn/15 text-warn", demise: "bg-white/10 text-mute",
};

export function PeopleBody({ p }: { p: PeopleCard }) {
  if (!p.changes?.length) return <div className="mt-3 text-sm text-mute">Couldn't read the people involved - open the filing.</div>;
  return (
    <div className="mt-3 space-y-3">
      {p.oneLine && <p className="text-[13px] text-white/90">{p.oneLine}</p>}
      {p.changes.slice(0, 3).map((c) => (
        <div key={c.name + c.action} className="rounded-xl bg-white/[0.03] p-3">
          <div className="flex items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-fin/40 to-info/30 text-[13px] font-semibold">
              {c.name.split(" ").filter(Boolean).slice(0, 2).map((x) => x[0]).join("")}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{c.name}</span>
                <span className={cls("rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide", ACTION_STYLE[c.action] ?? "bg-white/10 text-mute")}>{c.action}</span>
              </div>
              <div className="text-[12px] text-mute">{c.role}{c.effective ? ` · w.e.f. ${c.effective}` : ""}</div>
            </div>
          </div>
          {c.profile?.summary && <p className="mt-2 text-[13px] leading-snug text-white/85">{c.profile.summary}</p>}
          {!!c.profile?.prior_roles?.length && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {c.profile.prior_roles.slice(0, 4).map((r) => <span key={r} className="rounded-md bg-white/5 px-2 py-0.5 text-[11px] text-mute">{r}</span>)}
            </div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-mute">
            {!!c.profile?.education?.length && <span>🎓 {c.profile.education.slice(0, 2).join(", ")}</span>}
            {c.profile?.experience_years ? <span>{c.profile.experience_years}+ yrs experience</span> : null}
            {c.reason && <span>Reason: {c.reason}</span>}
          </div>
          <a href={c.linkedin} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-info hover:underline">
            <LinkedInBadge /> Find on LinkedIn <ArrowUpRight size={12} />
          </a>
        </div>
      ))}
      <div className="text-[11px] text-dim">Profile from the filing's SEBI-mandated "brief profile" ({p.method === "llm" ? "read by AI, names verified against filing" : "rule-based extract"}).</div>
    </div>
  );
}

export function SummaryBody({ s }: { s: SummaryCard }) {
  if (!s.summary) return null;
  const dot = s.signal === "positive" ? "bg-up" : s.signal === "negative" ? "bg-down" : "bg-mute";
  return (
    <div className="mt-3 flex gap-2 rounded-xl bg-white/[0.03] p-3">
      <span className={cls("mt-1.5 h-2 w-2 shrink-0 rounded-full", dot)} />
      <div>
        <p className="text-[13px] leading-snug">{s.summary}</p>
        <div className="mt-1 text-[11px] text-dim">{s.method === "llm" ? "AI read the filing" : "Extract from the filing"}</div>
      </div>
    </div>
  );
}

function MoveBody({ e }: { e: FinnEvent }) {
  return (
    <div className="mt-3">
      <div className="flex items-end justify-between gap-3">
        <Sparkline data={e.spark ?? []} width={190} height={46} highlightLast />
        <div className="text-right num">
          <div className={cls("text-xl font-semibold", tone(e.reaction?.ret))}>{pct(e.reaction?.ret)}</div>
          {e.reaction?.vmult != null && <div className="text-[12px] text-mute">{e.reaction.vmult.toFixed(1)}× avg volume</div>}
        </div>
      </div>
      <div className="mt-2 rounded-xl bg-white/[0.03] px-3 py-2 text-[12px]">
        {e.context ? (
          <span className="text-mute">No filing on the day. Last material filing: <a href={e.context.url ?? undefined} target="_blank" rel="noreferrer" className="text-white hover:underline">{e.context.title}</a> · {ago(e.context.ts)}</span>
        ) : (
          <span className="flex items-center gap-1.5 text-warn"><TriangleAlert size={13} /> Nothing filed in the last 6 days explains this - worth a look.</span>
        )}
      </div>
    </div>
  );
}

function Enriched({ e }: { e: FinnEvent }) {
  const [data, setData] = useState<Enrichment | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    if (!e.enrich) return;
    let alive = true;
    api.enrich(e).then((d) => alive && setData(d)).catch(() => alive && setErr(true));
    return () => { alive = false; };
  }, [e]);
  if (e.category === "move") return <MoveBody e={e} />;
  if (!e.enrich) return null;
  if (err) return <div className="mt-3 text-[12px] text-mute">Couldn't read this filing right now.</div>;
  if (!data)
    return (
      <div className="mt-3 space-y-2">
        <Skeleton className="h-3 w-1/3" />
        <Skeleton className={e.enrich === "results" ? "h-28 w-full" : "h-12 w-full"} />
      </div>
    );
  if (data.kind === "results") return <ResultsBody r={data} />;
  if (data.kind === "people") return <PeopleBody p={data} />;
  return <SummaryBody s={data} />;
}

// ----------------------------------------------------------------------------- card shell
export function EventCard({ e, onTriage, defaultOpen = true, compact = false }: {
  e: FinnEvent; onTriage?: (state: "done" | "pinned" | null) => void; defaultOpen?: boolean; compact?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [dx, setDx] = useState(0);
  const [showRelated, setShowRelated] = useState(false);
  const start = useRef<{ x: number; y: number; active: boolean } | null>(null);

  const extraWhy = e.why.slice(1).filter((w) => !/of portfolio$/.test(w));
  const weight = e.why.find((w) => /of portfolio$/.test(w));

  const onDown = (ev: React.PointerEvent) => { start.current = { x: ev.clientX, y: ev.clientY, active: false }; };
  const onMove = (ev: React.PointerEvent) => {
    const s = start.current;
    if (!s || !onTriage) return;
    const ddx = ev.clientX - s.x, ddy = ev.clientY - s.y;
    if (!s.active && Math.abs(ddx) > 12 && Math.abs(ddx) > Math.abs(ddy) * 1.4) s.active = true;
    if (s.active) setDx(Math.max(-140, Math.min(140, ddx)));
  };
  const onUp = () => {
    if (dx < -90) { haptic(14); onTriage?.("done"); }
    else if (dx > 90) { haptic(14); onTriage?.(e.triage === "pinned" ? null : "pinned"); }
    setDx(0);
    start.current = null;
  };

  return (
    <div className="relative rise">
      {onTriage && (
        <div className="absolute inset-0 flex items-center justify-between rounded-[20px] px-5 text-[13px] font-semibold">
          <span className={cls("flex items-center gap-1.5 text-warn transition", dx > 30 ? "opacity-100" : "opacity-0")}><Pin size={16} /> {e.triage === "pinned" ? "Unpin" : "Pin"}</span>
          <span className={cls("flex items-center gap-1.5 text-up transition", dx < -30 ? "opacity-100" : "opacity-0")}>Done <Check size={16} /></span>
        </div>
      )}
      <article
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
        style={{ transform: `translateX(${dx}px)`, transition: dx === 0 ? "transform .25s ease" : "none", touchAction: "pan-y" }}
        className={cls("card relative bg-ink-900 p-4", e.triage === "pinned" && "ring-1 ring-warn/40")}>
        <header className="flex items-center gap-2 text-[12px]">
          <span className="font-bold tracking-wide text-white">{e.symbol}</span>
          <span className="truncate text-mute">{e.company}</span>
          <span className="ml-auto shrink-0 text-dim">{ago(e.ts)}</span>
        </header>
        <button className="mt-1.5 block w-full text-left" onClick={() => compact && setOpen(!open)}>
          <div className="flex items-start gap-2">
            <h3 className={cls("flex-1 font-semibold leading-snug", compact ? "text-[14px]" : "text-[16px]")}>{e.title}</h3>
            {compact && <ChevronDown size={16} className={cls("mt-0.5 shrink-0 text-mute transition", open && "rotate-180")} />}
          </div>
          {e.detail && open && <p className="clamp-2 mt-1 text-[12.5px] leading-snug text-mute">{e.detail}</p>}
        </button>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <CategoryBadge category={e.category} label={e.categoryLabel} />
          {weight && <span className="rounded-full bg-white/5 px-2 py-0.5 text-[11px] text-mute">{weight}</span>}
          {extraWhy.map((w) => <span key={w} className="rounded-full bg-warn/10 px-2 py-0.5 text-[11px] text-warn">{w}</span>)}
          {e.triage === "pinned" && <span className="rounded-full bg-warn/15 px-2 py-0.5 text-[11px] text-warn">Pinned</span>}
        </div>
        {open && (
          <>
            <Enriched e={e} />
            {(e.related.length > 0 || e.url) && (
              <footer className="mt-3 flex items-center gap-3 border-t hairline pt-3 text-[12px]">
                {e.related.length > 0 && (
                  <button onClick={() => setShowRelated(!showRelated)} className="flex items-center gap-1 text-mute hover:text-white">
                    +{e.related.length} related <ChevronDown size={13} className={cls("transition", showRelated && "rotate-180")} />
                  </button>
                )}
                {e.url && (
                  <a href={e.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-mute hover:text-white">
                    Filing <ExternalLink size={12} />
                  </a>
                )}
                {onTriage && (
                  <div className="ml-auto flex items-center gap-1">
                    <button aria-label="Pin" onClick={() => { haptic(); onTriage(e.triage === "pinned" ? null : "pinned"); }}
                      className="rounded-full p-1.5 text-mute hover:bg-white/5 hover:text-warn">
                      {e.triage === "pinned" ? <PinOff size={15} /> : <Pin size={15} />}
                    </button>
                    <button aria-label="Mark done" onClick={() => { haptic(); onTriage("done"); }}
                      className="flex items-center gap-1 rounded-full bg-white/[0.06] px-2.5 py-1 text-white hover:bg-up/20 hover:text-up">
                      <Check size={14} /> Done
                    </button>
                  </div>
                )}
              </footer>
            )}
            {showRelated && (
              <ul className="mt-2 space-y-1.5">
                {e.related.map((r, i) => (
                  <li key={i} className="flex items-center gap-2 text-[12px]">
                    <span className="h-1 w-1 shrink-0 rounded-full bg-mute" />
                    {r.url ? <a href={r.url} target="_blank" rel="noreferrer" className="truncate text-mute hover:text-white">{r.title}</a>
                      : <span className="truncate text-mute">{r.title}</span>}
                    <span className="ml-auto shrink-0 text-dim">{ago(r.ts)}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </article>
    </div>
  );
}
