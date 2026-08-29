"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, CalendarDays, MapPin, Search, TrendingDown, TrendingUp } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import Section from "@/components/Section";
import StatCard from "@/components/StatCard";
import ForecastChart from "@/components/ForecastChart";
import { ErrorState, LoadingState } from "@/components/AsyncBoundary";
import { CROPS, type CropCategory } from "@/lib/mock/crops";
import {
  getCrops,
  getDistricts,
  getForecast,
  getPricesToday,
  type ApiCrop,
  type TodayPrice,
} from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { cx, pct, rupees } from "@/lib/format";
import { longDate } from "@/lib/seed";

type Tab = "all" | CropCategory;

const TABS: { id: Tab; label: string }[] = [
  { id: "all", label: "Today — all produce" },
  { id: "vegetable", label: "Vegetables" },
  { id: "fruit", label: "Fruits" },
];

/**
 * Display metadata only — emoji, Marathi name, category. Every NUMBER on this
 * page now comes from the API. The crop catalogue also carries shelf-life and
 * spoilage constants, but those are the backend's job now, so we read them off
 * the API response instead of trusting a second copy that can drift.
 */
const META = new Map(CROPS.map((c) => [c.id, c]));

/** Fruit or vegetable, preferring the backend's own grouping. */
function categoryOf(crop: ApiCrop): CropCategory {
  if (crop.group === "fruit") return "fruit";
  if (crop.group === "vegetable" || crop.group === "spice") return "vegetable";
  return (META.get(crop.key)?.category ?? "vegetable") as CropCategory;
}

interface Row {
  key: string;
  name: string;
  nameMr: string;
  emoji: string;
  category: CropCategory;
  shelfLifeDays: number;
  maxHoldDays: number;
  avg: number;
  best: string;
  bestPrice: number;
  /** Best market we can actually forecast — usually, but not always, `best`. */
  chartMandi: string;
  /** False when no market in this district has enough history for this crop. */
  canForecast: boolean;
  change: number;
  arrivals: number;
  obsDate: string;
}

export default function DashboardPage() {
  const [district, setDistrict] = useState<string>("");
  const [tab, setTab] = useState<Tab>("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const districtState = useApi(getDistricts, []);

  // One request per crop would be a waterfall of a dozen round trips, so we
  // fetch every crop's prices in parallel once and group them client-side.
  const feed = useApi(async () => {
    const crops = await getCrops();
    const prices = await Promise.all(crops.map((c) => getPricesToday(c.key)));
    return crops.map((crop, i) => ({ crop, prices: prices[i] }));
  }, []);

  const districts = useMemo(
    () => (districtState.data ?? []).map((d) => d.name),
    [districtState.data],
  );
  const activeDistrict = district || districts[0] || "";

  const allRows: Row[] = useMemo(() => {
    return (feed.data ?? [])
      .map(({ crop, prices }) => {
        const local = prices.filter(
          (p: TodayPrice) => !activeDistrict || p.district === activeDistrict,
        );
        if (local.length === 0) return null;
        const values = local.map((p) => p.modal);
        const bestPrice = Math.max(...values);
        const best = local[values.indexOf(bestPrice)];
        // The dearest market is not always one we have enough history for.
        // Chart the best-covered one instead of showing an honest refusal as
        // the first thing a visitor sees.
        const forecastable = local
          .filter((p) => p.canForecast)
          .sort((a, b) => b.observations - a.observations)[0];
        return {
          key: crop.key,
          name: crop.name,
          nameMr: crop.nameMr || crop.name,
          emoji: META.get(crop.key)?.emoji ?? "\u{1F33F}",
          category: categoryOf(crop),
          shelfLifeDays: crop.shelfLifeDays,
          maxHoldDays: crop.maxHoldDays,
          avg: values.reduce((a, b) => a + b, 0) / values.length,
          best: best.mandi,
          bestPrice,
          chartMandi: (forecastable ?? best).mandi,
          canForecast: Boolean(forecastable),
          change: local[0].changePct,
          arrivals: local.reduce((sum, p) => sum + (p.arrivalQtl ?? 0), 0),
          obsDate: local[0].obsDate,
        } satisfies Row;
      })
      .filter((r): r is Row => r !== null);
  }, [feed.data, activeDistrict]);

  const counts = useMemo(
    () => ({
      all: allRows.length,
      vegetable: allRows.filter((r) => r.category === "vegetable").length,
      fruit: allRows.filter((r) => r.category === "fruit").length,
    }),
    [allRows],
  );

  const rows = useMemo(() => {
    const base = tab === "all" ? allRows : allRows.filter((r) => r.category === tab);
    const q = query.trim().toLowerCase();
    return q
      ? base.filter((r) => r.name.toLowerCase().includes(q) || r.nameMr.includes(q))
      : base;
  }, [allRows, tab, query]);

  const gainers = [...rows].sort((a, b) => b.change - a.change).slice(0, 3);
  const losers = [...rows].sort((a, b) => a.change - b.change).slice(0, 3);

  // Default to the best-covered crop, not the alphabetically first one.
  // /crops comes back sorted by name, which opened the page on banana — a crop
  // thin enough that the forecast honestly refuses, so the first thing a
  // visitor saw was an error rather than the product.
  useEffect(() => {
    if (selected || allRows.length === 0) return;
    // Prefer a crop this district can actually forecast. CEDA is district-level,
    // so a district has exactly one market — if that market is thin for a crop,
    // there is no better one to fall back to and the forecast rightly refuses.
    // Opening on such a crop showed an honest error as the first impression.
    const rank = (r: Row) =>
      (r.canForecast ? 1_000_000 : 0) + r.arrivals;
    setSelected([...allRows].sort((a, b) => rank(b) - rank(a))[0].key);
  }, [allRows, selected]);

  // Only after the effect above has chosen a default. On the very first paint
  // `rows[0]` is whatever sorts first alphabetically, and firing the chart at it
  // produced one refused request per page load before the good default landed.
  const selectedRow = selected ? rows.find((r) => r.key === selected) ?? null : null;

  const chart = useApi(
    () =>
      selectedRow?.canForecast
        ? getForecast(selectedRow.chartMandi, selectedRow.key)
        : Promise.resolve([]),
    [selectedRow?.key, selectedRow?.chartMandi, selectedRow?.canForecast],
  );

  const marketCount = useMemo(() => {
    const seen = new Set<string>();
    for (const { prices } of feed.data ?? []) {
      for (const p of prices) {
        if (!activeDistrict || p.district === activeDistrict) seen.add(p.mandi);
      }
    }
    return seen.size;
  }, [feed.data, activeDistrict]);

  const asOf = allRows[0]?.obsDate;
  const failure = districtState.error ?? feed.error;

  return (
    <>
      <PageHeader
        eyebrow="Dashboard"
        title={`Today's mandi prices${activeDistrict ? ` — ${activeDistrict}` : ""}`}
        lede="Every crop trading in your district right now, which mandi is paying the most for it, and how it moved since the previous session."
      >
        <div className="flex items-center gap-2 rounded-full border border-line bg-card px-4 py-2.5">
          <CalendarDays size={15} className="text-muted" />
          <span className="text-[0.86rem] font-medium">
            {asOf ? longDate(asOf) : "—"}
          </span>
        </div>
      </PageHeader>

      <Section>
        {failure && (
          <div className="mb-6">
            <ErrorState error={failure} onRetry={() => { districtState.reload(); feed.reload(); }} />
          </div>
        )}

        {/* District + search */}
        <div className="card mb-6 flex flex-wrap items-end gap-6 p-5">
          <div className="min-w-[220px]">
            <p className="label">District</p>
            <div className="flex flex-wrap gap-1.5">
              {districts.map((d) => (
                <button
                  key={d}
                  onClick={() => { setDistrict(d); setSelected(null); }}
                  className={cx("chip", activeDistrict === d && "chip-active")}
                >
                  <MapPin size={12} />
                  {d}
                </button>
              ))}
            </div>
          </div>

          <div className="min-w-[240px] flex-1">
            <p className="label">Find a crop</p>
            <label className="relative flex items-center">
              <Search size={15} className="pointer-events-none absolute left-3.5 text-muted" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Tomato, कांदा, pomegranate…"
                className="input pl-10"
              />
            </label>
          </div>
        </div>

        <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Mandis reporting" value={String(marketCount)} hint={`${activeDistrict || "all"} district`} />
          <StatCard
            label="Crops trading"
            value={String(rows.length)}
            hint={tab === "all" ? "Vegetables and fruits" : tab === "vegetable" ? "Vegetables only" : "Fruits only"}
          />
          <StatCard label="Biggest gainer" value={gainers[0]?.name ?? "—"} hint={gainers[0] ? pct(gainers[0].change) : ""} tone="up" />
          <StatCard label="Biggest faller" value={losers[0]?.name ?? "—"} hint={losers[0] ? pct(losers[0].change) : ""} tone="down" />
        </div>

        {/* Category tabs */}
        <div className="mb-4 flex flex-wrap gap-1.5">
          {TABS.map((tb) => (
            <button
              key={tb.id}
              onClick={() => setTab(tb.id)}
              className={cx(
                "rounded-full border px-4 py-2 text-[0.86rem] font-medium transition",
                tab === tb.id
                  ? "border-ink bg-ink text-cream"
                  : "border-line bg-card text-ink/70 hover:border-ink/30"
              )}
            >
              {tb.label}
              <span className={cx("ml-2 text-[0.75rem]", tab === tb.id ? "text-cream/60" : "text-muted")}>
                {counts[tb.id]}
              </span>
            </button>
          ))}
        </div>

        <div className="card overflow-hidden">
          {feed.loading ? (
            <div className="p-6">
              <LoadingState label="Loading today's prices…" rows={6} />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px]">
                <thead className="border-b border-line bg-panel/40">
                  <tr>
                    <th className="th">Crop</th>
                    <th className="th">Category</th>
                    <th className="th text-right">District avg ₹/qtl</th>
                    <th className="th text-right">Change</th>
                    <th className="th">Best mandi today</th>
                    <th className="th text-right">Arrivals</th>
                    <th className="th" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr
                      key={r.key}
                      onClick={() => setSelected(r.key)}
                      className={cx(
                        "cursor-pointer border-b border-line/70 last:border-0 transition",
                        selected === r.key ? "bg-panel/55" : "hover:bg-panel/30"
                      )}
                    >
                      <td className="td">
                        <div className="flex items-center gap-3">
                          <span className="text-[1.15rem]">{r.emoji}</span>
                          <div>
                            <p className="font-medium">{r.name}</p>
                            <p className="text-[0.75rem] text-muted">{r.nameMr}</p>
                          </div>
                        </div>
                      </td>
                      <td className="td">
                        <span className="chip capitalize">{r.category}</span>
                      </td>
                      <td className="td text-right text-[0.98rem] font-semibold tabular-nums">
                        {rupees(r.avg)}
                      </td>
                      <td className={cx("td text-right tabular-nums font-medium", r.change >= 0 ? "text-up" : "text-down")}>
                        <span className="inline-flex items-center gap-1">
                          {r.change >= 0 ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
                          {pct(r.change)}
                        </span>
                      </td>
                      <td className="td">
                        <p className="font-medium">{r.best}</p>
                        <p className="text-[0.75rem] tabular-nums text-muted">{rupees(r.bestPrice)}/qtl</p>
                      </td>
                      <td className="td text-right tabular-nums text-muted">
                        {Math.round(r.arrivals).toLocaleString("en-IN")} qtl
                      </td>
                      <td className="td text-right">
                        <Link
                          href={`/advisor?crop=${r.key}`}
                          className="inline-flex items-center gap-1 whitespace-nowrap text-[0.8rem] text-muted transition hover:text-ink"
                          onClick={(e) => e.stopPropagation()}
                        >
                          Get advice
                          <ArrowUpRight size={13} />
                        </Link>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={7} className="td py-12 text-center text-muted">
                        {query ? `Nothing matches “${query}”.` : "No crops trading here yet."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Section>

      {selectedRow && (
        <Section
          title={`${selectedRow.name} at ${selectedRow.chartMandi}`}
          description="Click any row above to change the crop. Dashed line and shaded band are the 15-day forecast."
          aside={
            <Link href={`/advisor?crop=${selectedRow.key}`} className="btn-ghost">
              Advice for {selectedRow.name.toLowerCase()}
              <ArrowUpRight size={15} />
            </Link>
          }
        >
          <div className="card p-6">
            <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
              <div>
                <p className="text-[2.1rem] font-bold leading-none tracking-[-0.02em]">
                  {rupees(selectedRow.bestPrice)}
                  <span className="text-[0.85rem] font-medium text-muted">/quintal</span>
                </p>
                <p className="mt-2 text-[0.82rem] text-muted">
                  shelf life {selectedRow.shelfLifeDays} days · hold at most{" "}
                  {selectedRow.maxHoldDays} days
                </p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {allRows
                  .filter((r) => r.key === selectedRow.key)
                  .map((r) => (
                    <span key={r.key} className="chip">
                      {r.best} · {rupees(r.bestPrice)}
                    </span>
                  ))}
              </div>
            </div>

            {!selectedRow.canForecast ? (
              <div className="rounded-xl border border-dashed border-line p-8 text-center text-sm text-muted">
                We have only {selectedRow.shelfLifeDays > 0 ? "" : ""}a few weeks of
                history for {selectedRow.name.toLowerCase()} in this district — not
                enough to forecast honestly. The price above is real; the forecast
                is withheld rather than guessed.
              </div>
            ) : chart.loading ? (
              <LoadingState label="Loading the forecast…" rows={4} />
            ) : chart.error ? (
              <ErrorState error={chart.error} onRetry={chart.reload} />
            ) : (
              <ForecastChart data={(chart.data ?? []).slice(-80)} />
            )}
          </div>
        </Section>
      )}
    </>
  );
}
