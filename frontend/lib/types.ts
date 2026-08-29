/** Shared shapes. These mirror the Pydantic models the API will eventually return,
 *  so swapping lib/api.ts from mock to fetch should not touch any component. */

export type Grade = "A" | "B" | "C";
export type Storage = "ambient" | "shed" | "cold_store";
export type RiskProfile = "cautious" | "balanced" | "aggressive";
export type Language = "en" | "mr";

export interface Mandi {
  id: number;
  name: string;
  nameMr: string;
  district: string;
  lat: number;
  lon: number;
  distanceKm: number;
  todayModal: number;
  changePct: number;
  arrivalQtl: number;
  liquidity: "high" | "medium" | "low";
}

export interface PricePoint {
  date: string;
  modal: number | null;
  p10?: number | null;
  p50?: number | null;
  p90?: number | null;
  isForecast: boolean;
}

export interface Tranche {
  qtl: number;
  when: string;
  dayOffset: number;
  mandi: string;
  netPerQtl: number;
  rangeLow: number;
  rangeHigh: number;
}

export interface Recommendation {
  action: "sell_now" | "hold" | "split" | "sell_to_procurement";
  headline: string;
  headlineMr: string;
  tranches: Tranche[];
  baselineNet: number;
  strategyNet: number;
  expectedGain: number;
  expectedGainPct: number;
  confidence: number;
  reasonText: string;
  reasonTextMr: string;
  constraintsApplied: string[];
  alternativesConsidered: number;
}

export interface CostLine {
  label: string;
  labelMr: string;
  amount: number;
  kind: "gross" | "deduction";
}

export interface MandiComparison {
  mandi: string;
  distanceKm: number;
  grossPerQtl: number;
  netPerQtl: number;
  rankByGross: number;
  rankByNet: number;
  rankFlipped: boolean;
  breakdown: CostLine[];
}

export interface HorizonMetric {
  horizon: number;
  naive: number;
  seasonal: number;
  ma7: number;
  model: number;
}

export interface CropUplift {
  crop: string;
  scenarios: number;
  upliftPct: number;
  winRate: number;
}

export interface HorizonCoverage {
  horizon: number;
  picp: number;
}

export interface AccuracySummary {
  mape: HorizonMetric[];
  pinball: HorizonMetric[];
  /** Real band coverage per horizon — the honesty curve. */
  coverage: HorizonCoverage[];
  /** Real backtest, per crop. Empty until scripts/backtest.py --record has run. */
  backtestPerCrop: CropUplift[];
  backtestScenarios: number;
  picp: number;
  directionalAccuracy: number;
  modelVersion: string;
  trainedAt: string;
  trainRows: number;
  upliftPct: number;
  winRate: number;
}

export interface SaleReport {
  id: string;
  farmer: string;
  village: string;
  mandi: string;
  date: string;
  qtl: number;
  quotedPerQtl: number;
  receivedPerQtl: number;
  gapPct: number;
  followedAdvice: boolean;
  verification: "self_reported" | "slip_photo" | "fpo_verified";
}

export interface TransparencyScore {
  mandi: string;
  reports: number;
  medianGapPct: number;
  score: number;
  trend: "up" | "down" | "flat";
}

export interface BacktestPoint {
  month: string;
  strategy: number;
  baseline: number;
}

export interface ChatMessage {
  id: string;
  from: "bot" | "user";
  text: string;
  time: string;
  buttons?: string[];
}

export interface SessionUser {
  name: string;
  phone: string;
  village: string;
  language: Language;
  riskProfile: RiskProfile;
}
