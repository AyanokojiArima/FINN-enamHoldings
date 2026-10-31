import { useEffect, useState } from "react";
import { Cpu, Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import type { Holding, Meta } from "../types";
import { SectionTitle } from "../components/ui";

const PURPOSE: Record<string, string> = {
  results_pdf_extract: "Results PDF → numbers (fallback)",
  mgmt_change_extract: "Management-change bio",
  filing_summary: "One-line filing summary",
  transcript_extract: "Transcript → guidance & claims",
  promise_vs_delivery: "Promise vs delivery judgement",
  promise_summary: "Track-record summary",
  thesis: "Narrative vs numbers, bull/bear",
  ask: "Ask the filings",
};

export default function Settings({ meta, reloadMeta }: { meta: Meta | null; reloadMeta: () => void }) {
  const [items, setItems] = useState<Holding[]>([]);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ symbol: string; name: string }[]>([]);
  const [saved, setSaved] = useState(false);

  useEffect(() => { if (meta) setItems(meta.portfolio); }, [meta]);
  useEffect(() => {
    if (!q.trim()) { setHits([]); return; }
    const t = setTimeout(() => api.search(q).then(setHits).catch(() => {}), 180);
    return () => clearTimeout(t);
  }, [q]);

  const save = async (next: Holding[]) => {
    setItems(next);
    await api.portfolio(next);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
    reloadMeta();
  };
  const total = items.reduce((a, h) => a + (Number(h.weight) || 0), 0);
  const u = meta?.llm.usage ?? {};

  return (
    <div className="pb-28">
      <SectionTitle right={<span className="text-[12px] text-mute">{items.length} stocks · {total.toFixed(0)}% {saved && <span className="text-up">· saved</span>}</span>}>Portfolio</SectionTitle>
      <div className="card divide-y divide-white/5">
        {items.map((h, i) => (
          <div key={h.symbol} className="flex items-center gap-3 px-4 py-2.5">
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold">{h.symbol}</div>
              <div className="truncate text-[11px] text-mute">{h.sector}</div>
            </div>
            <input type="number" value={h.weight} min={0} max={100}
              onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, weight: Number(e.target.value) } : x)))}
              onBlur={() => save(items)}
              className="w-16 rounded-lg bg-ink-800 px-2 py-1 text-right text-[13px] num" />
            <span className="text-[12px] text-mute">%</span>
            <button onClick={() => save(items.filter((_, j) => j !== i))} className="p-1 text-dim hover:text-down" aria-label={`Remove ${h.symbol}`}><Trash2 size={15} /></button>
          </div>
        ))}
        <div className="relative px-4 py-2.5">
          <div className="flex items-center gap-2">
            <Plus size={15} className="text-fin" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Add any NSE stock…" className="flex-1 bg-transparent text-[13px] outline-none placeholder:text-dim" />
          </div>
          {hits.length > 0 && (
            <div className="mt-2 space-y-0.5">
              {hits.slice(0, 6).map((h) => (
                <button key={h.symbol} onClick={() => { save([...items, { symbol: h.symbol, weight: 3, sector: "" }]); setQ(""); }}
                  className="flex w-full items-baseline gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-white/[0.04]">
                  <b className="text-[13px]">{h.symbol}</b><span className="truncate text-[12px] text-mute">{h.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <SectionTitle>AI routing & cost</SectionTitle>
      <div className="card p-4 text-[13px]">
        <div className="flex items-center gap-2">
          <Cpu size={16} className={meta?.llm.enabled ? "text-fin" : "text-mute"} />
          <span className="font-semibold">{meta?.llm.enabled ? `On · ${meta.llm.provider}` : "Rules-only mode"}</span>
        </div>
        {meta?.llm.enabled && meta.llm.models && (
          <div className="mt-3 grid grid-cols-2 gap-2">
            <div className="rounded-xl bg-white/[0.03] p-3"><div className="text-[10.5px] uppercase tracking-wider text-mute">Fast tier · bulk reading</div><div className="mt-0.5 font-semibold">{meta.llm.models.fast}</div></div>
            <div className="rounded-xl bg-white/[0.03] p-3"><div className="text-[10.5px] uppercase tracking-wider text-mute">Smart tier · judgement</div><div className="mt-0.5 font-semibold">{meta.llm.models.smart}</div></div>
          </div>
        )}
        {!meta?.llm.enabled && <p className="mt-2 text-mute">Add a free Gemini key to <code className="text-white">backend/.env</code> to switch on transcript reading, bios and verdicts. Everything else already works without it.</p>}
        {meta?.llm.lastError && <p className="mt-2 text-[12px] text-warn">{meta.llm.lastError}</p>}
        {meta?.llm.budget?.limit && (
          <p className="mt-2 text-[12px] text-mute">Daily AI budget on this server: {meta.llm.budget.used} of {meta.llm.budget.limit} fresh model calls used today. Cached answers are free.</p>
        )}
        <div className="mt-3 grid grid-cols-3 gap-2 text-center">
          <div><div className="text-[18px] font-semibold num">{u.calls ?? 0}</div><div className="text-[11px] text-mute">model calls</div></div>
          <div><div className="text-[18px] font-semibold num">{u.cached ?? 0}</div><div className="text-[11px] text-mute">served from cache</div></div>
          <div><div className="text-[18px] font-semibold num">{(((u.input_tokens ?? 0) + (u.output_tokens ?? 0)) / 1000).toFixed(1)}k</div><div className="text-[11px] text-mute">tokens</div></div>
        </div>
        {u.by_purpose && Object.keys(u.by_purpose).length > 0 && (
          <div className="mt-3 space-y-1 border-t hairline pt-3">
            {Object.entries(u.by_purpose).map(([k, v]) => (
              <div key={k} className="flex items-center gap-2 text-[12px]">
                <span className={v.tier === "smart" ? "text-info" : "text-fin"}>●</span>
                <span className="flex-1 text-white/85">{PURPOSE[k] ?? k}</span>
                <span className="text-mute num">{v.calls} calls · {v.cached} cached · {(v.tokens / 1000).toFixed(1)}k tok</span>
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 text-[12px] leading-relaxed text-mute">
          Classification, scoring, results numbers (exchange XBRL), price signals and all ratios are computed with rules and code: free, instant, exact.
          The fast tier (lite models) only reads text that matters: filings with vague headlines, transcripts. The smart tier (full models) only judges
          small, pre-computed fact packs, and code re-checks its verdicts: dates, citations, segment vs company, numbers vs targets.
          Every answer is cached; when a model is rate-limited or down, the next one in the chain takes over.
        </p>
      </div>

      <SectionTitle>How FINN decides what matters</SectionTitle>
      <div className="card space-y-2 p-4 text-[13px] leading-relaxed text-white/85">
        <p><b>1. Collapse.</b> A results day produces 5-8 filings (outcome, press release, deck, newspaper ad, call invite). FINN folds them into one story.</p>
        <p><b>2. Score.</b> Category weight × your position size × how the stock actually reacted × your alert preferences.</p>
        <p><b>3. Explain.</b> Every card says why it's there. Price moves with no filing behind them get flagged - those are the ones to chase.</p>
        <p><b>4. Hide.</b> Routine paperwork (ESOP allotments, trading-window notices, compliance certificates) is counted, not shown.</p>
      </div>
      <p className="mt-6 text-center text-[11px] text-dim">FINN · data: NSE filings & XBRL, Yahoo EOD prices, screener.in documents · not investment advice</p>
    </div>
  );
}
