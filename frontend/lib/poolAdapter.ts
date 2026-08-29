/**
 * Real API pools, in the shape the community card was written against.
 *
 * The card predates the backend, so rather than rewrite its markup we adapt the
 * response. Every number here is the **backend's** — computed with the same
 * `transport_per_km` and truck capacity the cost waterfall uses, so the
 * community page and the advisor cannot disagree about what a truck costs.
 *
 * Nothing is recomputed in the browser. That was the whole failure mode of the
 * mock: two copies of the same arithmetic, drifting apart quietly.
 */

import type { ApiPool } from "./api";
import type { TransportPool } from "./mock/community";

/** Emoji stand-ins for member rows; the API does not carry a crop per member. */
const MEMBER_EMOJI = ["🧅", "🍅", "🥔", "🍆", "🥬", "🧄"];

export type AdaptedPool = TransportPool & { apiId: number };

export function adaptPool(pool: ApiPool): AdaptedPool {
  return {
    apiId: pool.id,
    id: `POOL-${pool.id}`,
    mandi: pool.mandi,
    district: pool.district,
    distanceKm: pool.distanceKm,
    date: pool.travelDate,
    dateLabel: new Date(pool.travelDate).toLocaleDateString("en-IN", {
      weekday: "short",
      day: "numeric",
      month: "short",
    }),
    departTime: "6:00 am",
    truckCapacityQtl: pool.capacityQtl,
    status: pool.isFull ? "full" : pool.members.length > 1 ? "confirmed" : "forming",
    organiser: pool.members[0]?.farmer ?? "—",
    organiserPhone: "",
    members: pool.members.map((m, i) => ({
      name: m.farmer,
      // The API carries a name, a village and a quantity — that is all a pool
      // needs. The remaining fields exist because the card was written against
      // a richer mock; they are left blank rather than invented, and the card
      // renders them as empty.
      nameMr: "",
      village: m.village,
      phone: "",
      cropId: "",
      cropName: "",
      cropEmoji: MEMBER_EMOJI[i % MEMBER_EMOJI.length],
      qtyQtl: m.qtyQtl,
      isYou: m.farmer === "You",
      verified: false,
    })),
  };
}

export interface PoolEconomics {
  soloCost: number;
  pooledCostEach: number;
  savingEach: number;
  savingPct: number;
  totalQtl: number;
  capacityUsedPct: number;
}

/** Straight off the API — see the note at the top about not recomputing. */
export function poolEconomicsOf(pool: ApiPool): PoolEconomics {
  return {
    soloCost: pool.costPerQtlAlone,
    pooledCostEach: pool.costPerQtlPooled,
    savingEach: pool.savingPerQtl,
    savingPct: pool.costPerQtlAlone
      ? (pool.savingPerQtl / pool.costPerQtlAlone) * 100
      : 0,
    totalQtl: pool.bookedQtl,
    capacityUsedPct: Math.min(100, (pool.bookedQtl / pool.capacityQtl) * 100),
  };
}
