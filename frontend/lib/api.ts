/**
 * The single seam between the UI and the backend — now talking to FastAPI.
 *
 * Every function here used to return seeded mock data. They now `fetch` from
 * NEXT_PUBLIC_API_BASE_URL, and **no component changed**, because they already
 * awaited these signatures and the API returns the shapes in lib/types.ts.
 *
 * Two things worth knowing:
 *
 *   - `ApiError` carries the backend's own sentence. When the API says "we have
 *     too little history for mango at Solapur", that is what the page shows —
 *     a readable reason beats a spinner that never stops.
 *   - Nothing here knows which forecaster is live. The accuracy page reads the
 *     provider name off `/accuracy`, so swapping the model changed a label and
 *     nothing else.
 */

import type {
  AccuracySummary,
  Grade,
  Mandi,
  MandiComparison,
  PricePoint,
  Recommendation,
  RiskProfile,
  IrrigationAdvisory,
  SaleReport,
  Storage,
  TransparencyScore,
} from "./types";
import type { LotInput } from "./mock/recommendation";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** Real data now. Kept so a page can still say so if it wants to. */
export const USING_MOCK_DATA = false;

const PREFIX = `${API_BASE}/api/v1`;

/** An error the UI can actually show a farmer. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly hint?: string;

  constructor(status: number, detail: string, code = "error", hint?: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.hint = hint;
  }

  /** True when the backend is unreachable rather than refusing. */
  get isOffline(): boolean {
    return this.status === 0;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${PREFIX}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      "Cannot reach the server. Check that the backend is running.",
      "offline",
      `Expected it at ${API_BASE}`,
    );
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    let code = "error";
    let hint: string | undefined;
    try {
      const body = await response.json();
      // FastAPI validation errors come back as a list under `detail`.
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail) && body.detail[0]?.msg)
        detail = body.detail[0].msg;
      code = body?.code ?? code;
      hint = body?.hint;
    } catch {
      /* keep the status-code message */
    }
    throw new ApiError(response.status, detail, code, hint);
  }

  return (await response.json()) as T;
}

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `?${query}` : "";
};

// ── reference data ─────────────────────────────────────────────────────────

// GET /api/v1/mandis
export function getMandis(cropId?: string): Promise<Mandi[]> {
  return request<Mandi[]>(`/mandis${qs({ crop: cropId, with_data: true })}`);
}

export interface ApiCrop {
  id: number;
  key: string;
  name: string;
  nameMr: string;
  group: string;
  perishabilityClass: number;
  shelfLifeDays: number;
  maxHoldDays: number;
  hasForecast: boolean;
}

// GET /api/v1/crops
export function getCrops(): Promise<ApiCrop[]> {
  return request<ApiCrop[]>("/crops");
}

export interface ApiDistrict {
  name: string;
  mandiCount: number;
  cropCount: number;
}

// GET /api/v1/districts
export function getDistricts(): Promise<ApiDistrict[]> {
  return request<ApiDistrict[]>("/districts");
}

export interface ApiVillage {
  name: string;
  nameMr: string;
  lat: number;
  lon: number;
  distanceToMarketKm: number | null;
}

export interface ApiDistrictLocation {
  name: string;
  nameMr: string;
  lat: number;
  lon: number;
  villages: ApiVillage[];
  market: string | null;
  hasData: boolean;
}

/** GET /api/v1/locations — districts and villages a farmer may register from. */
export function getLocations(): Promise<ApiDistrictLocation[]> {
  return request<ApiDistrictLocation[]>("/locations");
}

// ── prices and forecast ────────────────────────────────────────────────────

export interface TodayPrice {
  mandi: string;
  mandiId: number;
  district: string;
  crop: string;
  cropId: number;
  modal: number;
  minPrice: number | null;
  maxPrice: number | null;
  arrivalQtl: number | null;
  changePct: number;
  obsDate: string;
  /** Real observations behind this market — 0 means we cannot forecast it. */
  observations: number;
  canForecast: boolean;
}

// GET /api/v1/prices/today
export function getPricesToday(
  cropId = "onion",
  district?: string,
): Promise<TodayPrice[]> {
  return request<TodayPrice[]>(`/prices/today${qs({ crop: cropId, district })}`);
}

export interface ForecastResponse {
  crop: string;
  mandi: string;
  asOf: string;
  provider: string;
  modelVersion: string;
  series: PricePoint[];
}

/**
 * GET /api/v1/forecast — history plus the model's band, as one series.
 * Returns just the series so existing chart components need no change.
 */
export async function getForecast(
  mandiName: string,
  cropId = "onion",
): Promise<PricePoint[]> {
  const body = await request<ForecastResponse>(
    `/forecast${qs({ crop: cropId, mandi: mandiName, history_days: 90 })}`,
  );
  return body.series;
}

/** The same call, when a page wants the model version alongside the numbers. */
export function getForecastDetail(
  mandiName: string,
  cropId = "onion",
): Promise<ForecastResponse> {
  return request<ForecastResponse>(
    `/forecast${qs({ crop: cropId, mandi: mandiName, history_days: 90 })}`,
  );
}

// ── advice ─────────────────────────────────────────────────────────────────

// POST /api/v1/recommend
export function postRecommend(lot: LotInput): Promise<Recommendation> {
  return request<Recommendation>("/recommend", {
    method: "POST",
    body: JSON.stringify({
      crop: lot.cropId ?? "onion",
      qtyQtl: lot.qtyQtl,
      grade: lot.grade,
      storage: lot.storage,
      riskProfile: lot.risk,
      mandi: (lot as { mandi?: string }).mandi,
    }),
  });
}

// GET /api/v1/compare
export function getComparison(
  qtyQtl: number,
  daysHeld: number,
  grade: Grade,
  storage: Storage,
  cropId = "onion",
): Promise<MandiComparison[]> {
  return request<MandiComparison[]>(
    `/compare${qs({
      crop: cropId,
      qty_qtl: qtyQtl,
      days_held: daysHeld,
      grade,
      storage,
    })}`,
  );
}

// ── accuracy and transparency ──────────────────────────────────────────────

// GET /api/v1/accuracy
export function getAccuracy(): Promise<AccuracySummary> {
  return request<AccuracySummary>("/accuracy");
}

// GET /api/v1/transparency  +  GET /api/v1/sale-reports
export async function getTransparency(): Promise<{
  scores: TransparencyScore[];
  reports: SaleReport[];
}> {
  const [scores, reports] = await Promise.all([
    request<TransparencyScore[]>("/transparency"),
    request<SaleReport[]>("/sale-reports?limit=60"),
  ]);
  return { scores, reports };
}

// POST /api/v1/sale-reports
export async function postSaleReport(payload: {
  mandi: string;
  qtl: number;
  receivedPerQtl: number;
  quotedPerQtl?: number;
  cropId?: string;
  farmer?: string;
  village?: string;
  followedAdvice?: boolean;
}): Promise<{ ok: true; id: string }> {
  const created = await request<SaleReport>("/sale-reports", {
    method: "POST",
    body: JSON.stringify({
      farmer: payload.farmer ?? "Anonymous",
      village: payload.village ?? "",
      mandi: payload.mandi,
      crop: payload.cropId ?? "onion",
      qtl: payload.qtl,
      quotedPerQtl: payload.quotedPerQtl ?? payload.receivedPerQtl,
      receivedPerQtl: payload.receivedPerQtl,
      followedAdvice: payload.followedAdvice ?? false,
    }),
  });
  return { ok: true as const, id: created.id };
}

// ── community pooling ──────────────────────────────────────────────────────

export interface ApiPoolMember {
  id: number;
  farmer: string;
  village: string;
  qtyQtl: number;
}

export interface ApiPool {
  id: number;
  mandi: string;
  district: string;
  travelDate: string;
  capacityQtl: number;
  bookedQtl: number;
  members: ApiPoolMember[];
  distanceKm: number;
  totalCost: number;
  costPerQtlAlone: number;
  costPerQtlPooled: number;
  savingPerQtl: number;
  isFull: boolean;
}

// GET /api/v1/pools
export function getPools(mandi?: string, district?: string): Promise<ApiPool[]> {
  return request<ApiPool[]>(`/pools${qs({ mandi, district, open_only: true })}`);
}

// POST /api/v1/pools
export function createPool(input: {
  mandi: string;
  travelDate: string;
  farmer: string;
  village?: string;
  qtyQtl: number;
}): Promise<ApiPool> {
  return request<ApiPool>("/pools", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

// POST /api/v1/pools/{id}/join
export function joinPool(
  poolId: number,
  input: { farmer: string; village?: string; qtyQtl: number },
): Promise<ApiPool> {
  return request<ApiPool>(`/pools/${poolId}/join`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

// ── soil & groundwater ─────────────────────────────────────────────────────

/**
 * GET /api/v1/irrigation — irrigate or wait, for one crop at one place.
 *
 * The second decision the product makes. Same seam as everything else: the
 * water balance is the backend's, and this returns the sentence it produced
 * along with the numbers behind it.
 */
export function getIrrigation(
  cropId = "onion",
  mandiName?: string,
  days = 30,
): Promise<IrrigationAdvisory> {
  return request<IrrigationAdvisory>(
    `/irrigation${qs({ crop: cropId, mandi: mandiName, days })}`,
  );
}

// ── health ─────────────────────────────────────────────────────────────────

export interface Health {
  status: string;
  database: boolean;
  provider: string;
  modelVersion: string;
  crops: number;
  mandis: number;
  priceRows: number;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}

export type { LotInput, RiskProfile };
