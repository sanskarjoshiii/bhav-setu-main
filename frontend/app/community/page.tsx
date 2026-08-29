"use client";

import { useState } from "react";
import {
  BadgeCheck,
  CalendarDays,
  Clock,
  MapPin,
  MessageCircle,
  Phone,
  Plus,
  Truck,
  Users,
} from "lucide-react";
import PageHeader from "@/components/PageHeader";
import Section from "@/components/Section";
import StatCard from "@/components/StatCard";
import { getPools, joinPool, createPool, type ApiPool } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import { ErrorState, LoadingState } from "@/components/AsyncBoundary";
import { adaptPool, poolEconomicsOf, type AdaptedPool } from "@/lib/poolAdapter";
import { cx, qtl, rupees } from "@/lib/format";

const STATUS = {
  forming: { label: "Space available", cls: "bg-up/10 text-up" },
  confirmed: { label: "Confirmed", cls: "bg-ink/10 text-ink" },
  full: { label: "Truck full", cls: "bg-muted/15 text-muted" },
} as const;

export default function CommunityPage() {
  const { user } = useAuth();
  // A signed-in farmer sees trucks leaving from HIS district. Showing him a
  // pool 200 km away is noise — the whole point is neighbours going the same
  // morning. Signed out, he sees everything.
  const [showAll, setShowAll] = useState(false);
  const district = showAll ? undefined : user?.district || undefined;

  const [joined, setJoined] = useState<string[]>([]);
  const state = useApi(() => getPools(undefined, district), [district]);

  const apiPools = state.data ?? [];
  const pools = apiPools.map(adaptPool);

  const COMMUNITY_TOTALS = {
    activePools: apiPools.length,
    farmers: new Set(apiPools.flatMap((p) => p.members.map((m) => m.farmer))).size,
    villages: new Set(
      apiPools.flatMap((p) => p.members.map((m) => m.village).filter(Boolean)),
    ).size,
    // Rupees actually saved: each pool's per-quintal saving times the quintals
    // riding on it. Not a headline number invented for the page.
    savedThisMonth: apiPools.reduce(
      (sum, p) => sum + p.savingPerQtl * p.bookedQtl,
      0,
    ),
  };

  const [joinError, setJoinError] = useState<string | null>(null);

  async function handleJoin(pool: AdaptedPool) {
    setJoinError(null);
    try {
      await joinPool(pool.apiId, {
        farmer: user?.name || "You",
        village: user?.village || "",
        qtyQtl: 12,
      });
      setJoined((prev) => [...prev, pool.id]);
      state.reload();
    } catch (err) {
      // Usually "only N qtl of space left". Say which pool, so the message is
      // attached to the card the farmer just tapped.
      setJoinError(
        `${pool.mandi}: ${err instanceof Error ? err.message : "could not join"}`,
      );
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Community"
        title="Share a truck, split the diesel"
        lede="One farmer with 25 quintals still hires a whole truck. Four farmers going to the same mandi on the same morning split one — and each pays a quarter."
      >
        <button className="btn-primary">
          <Plus size={16} />
          Start a pool
        </button>
      </PageHeader>

      <Section>
        <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Active pools" value={String(COMMUNITY_TOTALS.activePools)} hint="Open for joining" />
          <StatCard label="Farmers taking part" value={String(COMMUNITY_TOTALS.farmers)} hint={`Across ${COMMUNITY_TOTALS.villages} villages`} />
          <StatCard
            label="Saved this month"
            value={rupees(COMMUNITY_TOTALS.savedThisMonth)}
            hint="Transport, versus going alone"
            tone="up"
          />
          <StatCard label="Typical saving" value="50–75%" hint="Depends on how many join" tone="up" />
        </div>

        {user?.district && (
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <span className="text-[0.82rem] text-muted">Showing trucks from</span>
            <button
              onClick={() => setShowAll(false)}
              className={cx("chip", !showAll && "chip-active")}
            >
              <MapPin size={12} />
              {user.district}
            </button>
            <button
              onClick={() => setShowAll(true)}
              className={cx("chip", showAll && "chip-active")}
            >
              All districts
            </button>
          </div>
        )}

        {joinError && (
          <p role="alert" className="mb-4 rounded-lg bg-down/10 px-3 py-2 text-[0.84rem] text-down">
            {joinError}
          </p>
        )}

        {state.loading ? (
          <LoadingState label="Finding trucks going your way…" rows={4} />
        ) : state.error ? (
          <ErrorState error={state.error} onRetry={state.reload} />
        ) : pools.length === 0 ? (
          <div className="card p-8 text-center text-muted">
            {user?.district && !showAll
              ? `No trucks are forming in ${user.district} right now. Start one and neighbours can join.`
              : "No pools are forming right now. Start one and neighbours can join."}
          </div>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {pools.map((pool, i) => (
              <PoolCard
                key={pool.id}
                pool={pool}
                api={apiPools[i]}
                joined={joined.includes(pool.id)}
                onJoin={() => void handleJoin(pool)}
              />
            ))}
          </div>
        )}
      </Section>

      <Section>
        <div className="panel flex flex-col items-start gap-5 rounded-2xl px-8 py-9 md:flex-row md:items-center md:justify-between">
          <div className="flex gap-4">
            <Truck size={22} className="mt-0.5 shrink-0" />
            <div className="max-w-2xl">
              <h2 className="h3">Why this matters more than the price</h2>
              <p className="mt-2 text-[0.9rem] leading-relaxed text-muted">
                Transport is the one cost a smallholder can actually control. At ₹42 a kilometre, a
                62 km trip to Nashik costs ₹2,604 whether you fill the truck or not — that is ₹260 a
                quintal on a 10-quintal lot, and it is exactly why the nearest mandi so often wins.
                Split it four ways and the far mandi becomes worth considering again.
              </p>
            </div>
          </div>
        </div>
      </Section>
    </>
  );
}

function PoolCard({
  pool,
  api,
  joined,
  onJoin,
}: {
  pool: AdaptedPool;
  api: ApiPool;
  joined: boolean;
  onJoin: () => void;
}) {
  const econ = poolEconomicsOf(api);
  const status = STATUS[pool.status];

  return (
    <div className="card flex flex-col overflow-hidden">
      <div className="border-b border-line p-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <MapPin size={15} className="text-muted" />
              <h3 className="h3">{pool.mandi}</h3>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.82rem] text-muted">
              <span className="flex items-center gap-1.5">
                <CalendarDays size={13} />
                {pool.dateLabel}
              </span>
              <span className="flex items-center gap-1.5">
                <Clock size={13} />
                {pool.departTime}
              </span>
              <span>{pool.distanceKm} km</span>
            </div>
          </div>
          <span className={cx("shrink-0 rounded-full px-3 py-1 text-[0.7rem] font-semibold", status.cls)}>
            {status.label}
          </span>
        </div>

        <div className="mt-5 grid grid-cols-3 gap-3">
          <div className="stat">
            <p className="stat-label">Alone</p>
            <p className="mt-1 text-[1.05rem] font-bold tabular-nums text-muted line-through">
              {rupees(econ.soloCost)}
            </p>
          </div>
          <div className="stat">
            <p className="stat-label">Pooled, each</p>
            <p className="mt-1 text-[1.05rem] font-bold tabular-nums">{rupees(econ.pooledCostEach)}</p>
          </div>
          <div className="stat bg-up/10">
            <p className="stat-label">You save</p>
            <p className="mt-1 text-[1.05rem] font-bold tabular-nums text-up">
              {rupees(econ.savingEach)}
            </p>
          </div>
        </div>

        <div className="mt-4">
          <div className="flex items-baseline justify-between text-[0.76rem] text-muted">
            <span>
              {qtl(econ.totalQtl)} of {qtl(pool.truckCapacityQtl)} truck capacity
            </span>
            <span>{Math.round(econ.capacityUsedPct)}% full</span>
          </div>
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-line">
            <div
              className={cx("h-full rounded-full", econ.capacityUsedPct > 92 ? "bg-down" : "bg-ink")}
              style={{ width: `${econ.capacityUsedPct}%` }}
            />
          </div>
        </div>
      </div>

      <div className="flex-1 p-6">
        <p className="eyebrow mb-3.5">
          {pool.members.length} farmers · organised by {pool.organiser}
        </p>

        <ul className="space-y-2.5">
          {pool.members.map((m) => (
            <li
              key={m.name}
              className={cx(
                "flex items-center gap-3 rounded-xl px-3 py-2.5",
                m.isYou ? "bg-ink text-cream" : "bg-panel/45"
              )}
            >
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-card/90 text-[0.85rem]">
                {m.cropEmoji}
              </span>

              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-1.5 truncate text-[0.87rem] font-medium">
                  {m.isYou ? "You" : m.name}
                  {m.verified && !m.isYou && <BadgeCheck size={13} className="text-up" />}
                </p>
                <p className={cx("truncate text-[0.74rem]", m.isYou ? "text-cream/65" : "text-muted")}>
                  {m.village} · {m.cropName} · {qtl(m.qtyQtl)}
                </p>
              </div>

              {!m.isYou && (
                <div className="flex shrink-0 gap-1.5">
                  <a
                    href={`tel:${m.phone}`}
                    aria-label={`Call ${m.name}`}
                    title={`Call ${m.name}`}
                    className="grid h-8 w-8 place-items-center rounded-full border border-line bg-card text-ink transition hover:bg-ink hover:text-cream"
                  >
                    <Phone size={14} />
                  </a>
                  <a
                    href={`https://wa.me/${m.phone.replace(/[^0-9]/g, "")}?text=${encodeURIComponent(
                      `Namaskar ${m.name}, I would like to join the truck to ${pool.mandi} on ${pool.dateLabel}.`
                    )}`}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`Message ${m.name}`}
                    title={`Message ${m.name}`}
                    className="grid h-8 w-8 place-items-center rounded-full border border-line bg-card text-ink transition hover:bg-[#1F3D2B] hover:text-white"
                  >
                    <MessageCircle size={14} />
                  </a>
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div className="flex flex-wrap gap-2 border-t border-line p-5">
        <button
          onClick={onJoin}
          disabled={pool.status === "full" && !joined}
          className={cx(joined ? "btn-ghost" : "btn-primary", "flex-1")}
        >
          <Users size={15} />
          {joined ? "You are in this pool" : pool.status === "full" ? "Truck is full" : "Join this pool"}
        </button>
        <a href={`tel:${pool.organiserPhone}`} className="btn-ghost">
          <Phone size={15} />
          Call organiser
        </a>
      </div>
    </div>
  );
}
