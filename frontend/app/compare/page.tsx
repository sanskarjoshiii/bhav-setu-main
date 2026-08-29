"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import PageHeader from "@/components/PageHeader";
import Section from "@/components/Section";
import NetComparisonTable from "@/components/NetComparisonTable";
import StatCard from "@/components/StatCard";
import type { Grade, MandiComparison, Storage } from "@/lib/types";
import { getComparison, getMandis } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { CROPS } from "@/lib/mock/crops";
import { cx, rupees } from "@/lib/format";

const MandiMap = dynamic(() => import("@/components/MandiMap"), {
  ssr: false,
  loading: () => <div className="h-[420px] animate-pulse rounded-2xl bg-panel" />,
});

const QUANTITIES = [10, 25, 50, 80, 150];
const HOLDS = [0, 3, 7, 15];

export default function ComparePage() {
  const [qty, setQty] = useState(80);
  const [days, setDays] = useState(0);
  const [grade, setGrade] = useState<Grade>("B");
  const [storage, setStorage] = useState<Storage>("shed");
  const [cropId, setCropId] = useState("onion");
  const [rows, setRows] = useState<MandiComparison[]>([]);

  const { user } = useAuth();
  // Coordinates for the map. The comparison rows carry names and distances but
  // not lat/lon, so this is the one extra call the page makes.
  const markets = useApi(() => getMandis(cropId), [cropId]);
  const mapMarkets = useMemo(
    () =>
      (markets.data ?? [])
        .filter((m) => Number.isFinite(m.lat) && Number.isFinite(m.lon))
        .map((m) => ({
          id: m.id, name: m.name, district: m.district,
          lat: m.lat, lon: m.lon, todayModal: m.todayModal,
          distanceKm: m.distanceKm, arrivalQtl: m.arrivalQtl,
        })),
    [markets.data],
  );

  useEffect(() => {
    void getComparison(qty, days, grade, storage, cropId).then(setRows);
  }, [qty, days, grade, storage, cropId]);

  const bestNet = rows[0];
  const bestGross = [...rows].sort((a, b) => b.grossPerQtl - a.grossPerQtl)[0];
  const flipped = bestNet && bestGross && bestNet.mandi !== bestGross.mandi;

  return (
    <>
      <PageHeader
        eyebrow="Mandi Compare"
        title="The highest price is not the best market"
        lede="Same lot, five mandis. Rank them by the board price, then by what actually reaches your hand, and watch the order change."
      />

      <Section>
        <div className="card mb-6 flex flex-wrap items-end gap-6 p-5">
          <div className="min-w-[240px]">
            <p className="label">Crop</p>
            <div className="flex max-h-[76px] flex-wrap gap-1.5 overflow-y-auto pr-1">
              {CROPS.map((c) => (
                <button
                  key={c.id}
                  onClick={() => setCropId(c.id)}
                  className={cx("chip", cropId === c.id && "chip-active")}
                >
                  {c.emoji} {c.name}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="label">Quantity</p>
            <div className="flex gap-1.5">
              {QUANTITIES.map((q) => (
                <button
                  key={q}
                  onClick={() => setQty(q)}
                  className={cx("chip", qty === q && "chip-active")}
                >
                  {q} qtl
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="label">Days held</p>
            <div className="flex gap-1.5">
              {HOLDS.map((d) => (
                <button
                  key={d}
                  onClick={() => setDays(d)}
                  className={cx("chip", days === d && "chip-active")}
                >
                  {d === 0 ? "Today" : `${d} days`}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="label">Grade</p>
            <div className="flex gap-1.5">
              {(["A", "B", "C"] as Grade[]).map((g) => (
                <button
                  key={g}
                  onClick={() => setGrade(g)}
                  className={cx("chip", grade === g && "chip-active")}
                >
                  {g}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="label">Storage</p>
            <div className="flex gap-1.5">
              {(["ambient", "shed", "cold_store"] as Storage[]).map((s) => (
                <button
                  key={s}
                  onClick={() => setStorage(s)}
                  className={cx("chip capitalize", storage === s && "chip-active")}
                >
                  {s.replace("_", " ")}
                </button>
              ))}
            </div>
          </div>
        </div>

        {rows.length > 0 && (
          <div className="mb-6 grid gap-3 sm:grid-cols-3">
            <StatCard
              label="Highest board price"
              value={bestGross.mandi}
              hint={`${rupees(bestGross.grossPerQtl)}/qtl gross`}
            />
            <StatCard
              label="Most money in hand"
              value={bestNet.mandi}
              hint={`${rupees(bestNet.netPerQtl)}/qtl net`}
              tone="up"
            />
            <StatCard
              label="Difference on this lot"
              value={rupees((bestNet.netPerQtl - rows[rows.length - 1].netPerQtl) * qty)}
              hint="Best versus worst choice"
            />
          </div>
        )}

        {flipped && (
          <div className="mb-6 rounded-2xl border border-ink bg-ink px-6 py-5 text-cream">
            <p className="text-[0.66rem] font-semibold uppercase tracking-[0.16em] text-cream/60">
              This is the point
            </p>
            <p className="mt-2 text-[1.05rem] leading-relaxed">
              <span className="font-bold">{bestGross.mandi}</span> shows the higher price, but{" "}
              <span className="font-bold">{bestNet.mandi}</span> puts more money in your hand — it is{" "}
              {bestGross.distanceKm - bestNet.distanceKm} km closer, and the transport and handling
              costs more than eat the difference.
            </p>
          </div>
        )}

        {rows.length > 0 ? (
          <NetComparisonTable rows={rows} />
        ) : (
          <div className="card h-64 animate-pulse bg-panel/40" />
        )}
      </Section>

      <Section title="Distance is the hidden cost" description="Your village is the white marker.">
        <MandiMap
          markets={mapMarkets}
          origin={
            user?.lat != null && user?.lon != null
              ? { name: user.village || user.district, lat: user.lat, lon: user.lon }
              : null
          }
          highlightDistrict={user?.district ?? null}
        />
      </Section>
    </>
  );
}
