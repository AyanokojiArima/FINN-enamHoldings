export type Tier = "top" | "watch" | "routine";

export interface Reaction { d: string; ret: number; vmult: number | null }
export interface Related { title: string; subtype: string; ts: string; url: string | null }

export interface FinnEvent {
  id: string;
  symbol: string;
  company: string;
  sector: string;
  category: string;
  categoryLabel: string;
  subtype: string;
  title: string;
  detail?: string | null;
  headline: string | null;
  rule: string;
  ts: string;
  url: string | null;
  desc?: string;
  text?: string;
  score: number;
  tier: Tier;
  why: string[];
  enrich: "results" | "people" | "summary" | null;
  reaction: Reaction | null;
  related: Related[];
  context?: { title: string; ts: string; url: string | null; category: string } | null;
  spark?: number[];
  sparkDates?: string[];
  triage?: "done" | "pinned" | null;
  trigger?: string;
  new?: boolean;
}

export interface PulseItem {
  symbol: string; name: string | null; weight: number; sector: string;
  price: number | null; chg: number | null; vmult: number | null; asOf: string | null; spark: number[]; events: number;
}

export interface Digest {
  window: { start: string; end: string };
  generatedAt: string;
  stats: { filings: number; events: number; top: number; watch: number; routine: number; muted: number };
  portfolioDayChange: number;
  top: FinnEvent[];
  watch: FinnEvent[];
  routine: { label: string; count: number; items: { id: string; symbol: string; title: string; ts: string; url: string | null }[] }[];
  pulse: PulseItem[];
  upcoming: { date: string; symbol: string; label: string; detail: string; url: string | null }[];
  stale?: Stale | null;
}

export interface ResultsRow { key: string; label: string; value: number | null; prev: number | null; yoyValue: number | null; qoq: number | null; yoy: number | null }
export interface ResultsCard {
  kind: "results"; symbol: string; quarter: string; qe: string; pending?: boolean; message?: string;
  periods?: { current: string; prev: string; yoy: string };
  basis?: string; bank?: boolean; entity?: string; rows?: ResultsRow[];
  margin?: { current: number | null; prev: number | null; yoy: number | null };
  nii?: { current: number | null; yoy: number | null } | null;
  verdict?: string; tone?: "strong" | "steady" | "weak" | null;
  source: { type?: string; label?: string; url?: string | null; verified?: boolean; grounded?: string };
  notes?: string[]; segments?: { name: string; revenue: number }[];
}
export interface PersonChange {
  name: string; role: string | null; action: string; effective: string | null; reason: string | null; key_role: boolean;
  profile: { summary: string | null; prior_roles: string[]; education: string[]; experience_years: number | null } | null;
  linkedin: string;
}
export interface PeopleCard { kind: "people"; symbol: string; changes: PersonChange[]; oneLine: string | null; method: string; source: { url: string | null } }
export interface SummaryCard { kind: "summary"; summary: string | null; signal: string; materiality: number | null; method: string }
export type Enrichment = ResultsCard | PeopleCard | SummaryCard;

export interface Stale { asOf: string | null; reason: string }
export interface AlertsResponse { alerts: FinnEvent[]; unseen: number; rules: Rules; generatedAt: string; stale?: Stale | null }
export interface Rules {
  priceMovePct: number; volumeMultiple: number; volumeLookback: number; near52w: boolean;
  categories: Record<string, "alert" | "digest" | "mute">; overrides: Record<string, { priceMovePct?: number }>;
}
export interface Holding { symbol: string; weight: number; sector: string }
export interface LlmStatus {
  enabled: boolean; provider: string; models: { fast: string; smart: string } | null; lastError: string | null;
  budget?: { limit: number | null; used: number };
  usage: { calls?: number; cached?: number; input_tokens?: number; output_tokens?: number;
    by_purpose?: Record<string, { calls: number; cached: number; tokens: number; tier: string }> };
}
export interface Meta { now: string; llm: LlmStatus; portfolio: Holding[]; researchPicks: string[]; rules: Rules; offline: boolean }

export interface Dossier {
  symbol: string; name: string; basis: string; about: string; website: string | null;
  quote: { price: number | null; chg: number | null; spark: number[]; ret1y: number | null; asOf: string | null };
  snapshot: Record<string, number | boolean | null>;
  segments: { quarter?: string; basis?: string; items?: { name: string; revenue: number; share: number; yoy: number | null }[]; source?: { label: string; url: string } };
  trajectory: { years: string[]; sales: (number | null)[]; opProfit: (number | null)[]; opm: (number | null)[]; pat: (number | null)[];
    cagr: Record<string, number | null>; quarters: { p: string; sales: number | null; opm: number | null; pat: number | null; salesYoY: number | null; patYoY: number | null }[]; signals: string[] };
  balance: { years: string[]; borrowings: (number | null)[]; equity: number[]; debtToEquity: (number | null)[]; signals: string[];
    workingCapital: Record<string, (number | null)[] | string[]> };
  cash: { years: string[]; cfo: (number | null)[]; fcf: (number | null)[]; pat: (number | null)[]; conversion: (number | null)[]; cumulative5?: number; signals: string[] };
  holding: { periods: string[]; promoters: (number | null)[]; fii: (number | null)[]; dii: (number | null)[]; signals: string[] };
  documents: { concalls: { period: string; transcript: string | null; ppt: string | null; date: string | null }[]; annualReports: { title: string; year: number | null; url: string }[] };
  sources: { id: string; label: string; url: string }[];
}

export interface Evidence { id: string; statement: string; quote: string; page: number; url: string; callQuarter: string }
export interface Guidance {
  id: string; metric: string; scope?: string | null; statement: string; quote: string; page: number; horizon: string | null; target: string | null;
  call: string; callQuarter: string; callPeriod: string; url: string;
  status?: "closed" | "open"; horizonEnd?: string | null; saidOn?: string[]; repeatOf?: string; evidence?: Evidence[];
  verdict: "delivered" | "partly" | "missed" | "on_track" | "behind" | "pending" | "unverifiable" | "unjudged"; actual: string | null; reason: string | null;
}
export interface Claim { id: string; claim: string; quote: string; page: number; call: string; callQuarter: string; url: string }
export interface Promises {
  symbol: string; name?: string; error?: string; message?: string;
  calls: { id: string; period: string; quarter: string; url: string; pages: number; tone: string | null; toneReason: string | null; method: string; guidance: number; claims: number; dropped: number }[];
  items: Guidance[]; claims: Claim[];
  score?: { credibility: number | null; counts: Record<string, number>; judged: number; promises?: number; statements?: number };
  pattern?: string | null; summary?: string | null; evidenceTo?: string | null; replay?: boolean; replayedFrom?: string; replayReason?: string;
  method?: { extract: string | null; judge: string | null; summary?: string | null; models: { fast: string; smart: string } | null };
}
export interface Thesis {
  symbol: string; error?: string; method: string; oneLiner: string | null; replay?: boolean; replayedFrom?: string; replayReason?: string;
  facts: { id: string; text: string }[];
  claims: { id: string; text: string; quote: string; page: number; call: string; url: string }[];
  narrative: { claim: string; numbers: string; verdict: string; refs: string[] }[];
  bull: { point: string; refs: string[] }[];
  bear: { point: string; refs: string[] }[];
  watch: string[];
}
export interface AskAnswer { answer: string; method: string; sources: { id: string; label: string; url: string | null; text: string }[] }
