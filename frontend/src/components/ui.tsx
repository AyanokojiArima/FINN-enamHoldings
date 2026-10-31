import { useEffect, type ReactNode } from "react";
import {
  Activity, Banknote, ChartColumn, Coins, Eye, Factory, FileText, Gavel, GitMerge, Landmark, Lock, Mic, Newspaper,
  ShieldCheck, Split, UserRound, Users, Vote, X,
} from "lucide-react";
import { cls } from "../lib/format";

export const CATEGORY_ICON: Record<string, typeof Activity> = {
  results: ChartColumn, mna: GitMerge, fund_raise: Banknote, management_change: UserRound, litigation: Gavel,
  insider_trading: Eye, earnings_call: Mic, investor_meetings: Users, credit_rating: ShieldCheck, dividend: Coins,
  bonus_split: Split, capex: Factory, press_release: Newspaper, postal_ballot: Vote, agm_egm: Landmark,
  pledging: Lock, unclassified: FileText, move: Activity,
};

export const CATEGORY_HUE: Record<string, string> = {
  results: "text-fin bg-fin/10", mna: "text-info bg-info/10", management_change: "text-violet-300 bg-violet-400/10",
  litigation: "text-down bg-down/10", fund_raise: "text-amber-300 bg-amber-300/10", credit_rating: "text-sky-300 bg-sky-300/10",
  move: "text-warn bg-warn/10", pledging: "text-down bg-down/10", capex: "text-teal-300 bg-teal-300/10",
};

export function CategoryBadge({ category, label, compact = false }: { category: string; label: string; compact?: boolean }) {
  const Icon = CATEGORY_ICON[category] ?? FileText;
  return (
    <span className={cls("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
      CATEGORY_HUE[category] ?? "text-mute bg-white/5")}>
      <Icon size={12} strokeWidth={2.2} />
      {!compact && label}
    </span>
  );
}

export function Sparkline({ data, width = 72, height = 24, stroke, fill = true, highlightLast = false }: {
  data: number[]; width?: number; height?: number; stroke?: string; fill?: boolean; highlightLast?: boolean;
}) {
  if (!data || data.length < 2) return <div style={{ width, height }} />;
  const min = Math.min(...data), max = Math.max(...data);
  const span = max - min || 1;
  const pts = data.map((v, i) => [(i / (data.length - 1)) * width, height - 2 - ((v - min) / span) * (height - 4)]);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const up = data[data.length - 1] >= data[0];
  const color = stroke ?? (up ? "var(--color-up)" : "var(--color-down)");
  const id = `g${Math.round(Math.random() * 1e9)}`;
  const last = pts[pts.length - 1];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="overflow-visible">
      {fill && (
        <>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={color} stopOpacity="0.28" />
              <stop offset="1" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${d} L${width},${height} L0,${height} Z`} fill={`url(#${id})`} />
        </>
      )}
      <path d={d} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
      {highlightLast && <circle cx={last[0]} cy={last[1]} r={3} fill={color} stroke="#07090c" strokeWidth={1.5} />}
    </svg>
  );
}

export function Bars({ data, labels, height = 64, color = "var(--color-fin)", negColor = "var(--color-down)" }: {
  data: (number | null)[]; labels?: string[]; height?: number; color?: string; negColor?: string;
}) {
  const vals = data.map((v) => v ?? 0);
  const max = Math.max(...vals.map(Math.abs), 1);
  return (
    <div className="flex items-end gap-[3px]" style={{ height }}>
      {vals.map((v, i) => (
        <div key={i} className="flex-1 flex flex-col items-center justify-end h-full" title={labels?.[i]}>
          <div className="w-full rounded-t-[3px]" style={{
            height: `${Math.max(2, (Math.abs(v) / max) * (height - 14))}px`,
            background: v < 0 ? negColor : color, opacity: 0.35 + 0.65 * ((i + 1) / vals.length),
          }} />
        </div>
      ))}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={cls("shimmer rounded-lg", className)} />;
}

export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px] fade-in" onClick={onClose} />
      <div className="relative w-full max-w-[460px] max-h-[86vh] overflow-y-auto rounded-t-3xl border-t hairline bg-ink-900 sheet-in safe-bottom">
        <div className="sticky top-0 z-10 flex items-center justify-between gap-3 bg-ink-900/95 backdrop-blur px-5 pt-3 pb-2">
          <div className="absolute left-1/2 top-1.5 h-1 w-10 -translate-x-1/2 rounded-full bg-white/15" />
          <div className="pt-2 text-[15px] font-semibold">{title}</div>
          <button onClick={onClose} className="mt-2 rounded-full p-1.5 text-mute hover:bg-white/5" aria-label="Close"><X size={18} /></button>
        </div>
        <div className="px-5 pb-6">{children}</div>
      </div>
    </div>
  );
}

export function SectionTitle({ children, right, id }: { children: ReactNode; right?: ReactNode; id?: string }) {
  return (
    <div id={id} className="flex items-center justify-between px-1 pt-6 pb-2 scroll-mt-28">
      <h2 className="text-[13px] font-semibold uppercase tracking-[0.08em] text-mute">{children}</h2>
      {right}
    </div>
  );
}

export function Pill({ active, onClick, children }: { active?: boolean; onClick?: () => void; children: ReactNode }) {
  return (
    <button onClick={onClick}
      className={cls("rounded-full px-3 py-1.5 text-[13px] font-medium transition whitespace-nowrap",
        active ? "bg-fin text-ink-950" : "bg-white/[0.05] text-mute hover:text-white")}>
      {children}
    </button>
  );
}

export function LinkedInBadge() {
  return <span className="inline-flex h-4 w-4 items-center justify-center rounded-[4px] bg-[#0a66c2] text-[10px] font-bold text-white">in</span>;
}

export function Empty({ icon, title, sub }: { icon: ReactNode; title: string; sub?: string }) {
  return (
    <div className="card flex flex-col items-center gap-2 px-6 py-8 text-center">
      <div className="text-fin">{icon}</div>
      <div className="font-semibold">{title}</div>
      {sub && <div className="text-sm text-mute">{sub}</div>}
    </div>
  );
}
