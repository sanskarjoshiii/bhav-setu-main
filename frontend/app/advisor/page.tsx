"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowUpRight, Info } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import Section from "@/components/Section";
import LotForm from "@/components/LotForm";
import RecommendationCard from "@/components/RecommendationCard";
import ForecastChart from "@/components/ForecastChart";
import type { MandiComparison, PricePoint, Recommendation } from "@/lib/types";
import { ApiError, getComparison, getForecast, postRecommend, type LotInput } from "@/lib/api";
import { ErrorState } from "@/components/AsyncBoundary";
import { DEFAULT_LOT } from "@/lib/mock/recommendation";
import { cropById } from "@/lib/mock/crops";
import { useHistory } from "@/lib/history";
import { rupees } from "@/lib/format";

function AdvisorInner() {
  const params = useSearchParams();
  const cropParam = params.get("crop");
  const { add } = useHistory();

  const [lot, setLot] = useState<LotInput>({
    ...DEFAULT_LOT,
    cropId: cropParam ?? DEFAULT_LOT.cropId,
  });
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [series, setSeries] = useState<PricePoint[]>([]);
  const [rows, setRows] = useState<MandiComparison[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  async function run(next: LotInput) {
    setLoading(true);
    setError(null);
    setLot(next);

    let r: Recommendation;
    let cmp: MandiComparison[];
    try {
      [r, cmp] = await Promise.all([
        postRecommend(next),
        getComparison(next.qtyQtl, 0, next.grade, next.storage, next.cropId),
      ]);
    } catch (err) {
      // The backend refuses with a sentence — "too little history for mango at
      // Solapur" — and that sentence is more useful than a blank panel.
      setError(err instanceof ApiError ? err : new ApiError(0, "Could not build a recommendation"));
      setRec(null);
      setRows([]);
      setSeries([]);
      setLoading(false);
      return;
    }

    setRec(r);
    setRows(cmp);
    const best = r.tranches[0]?.mandi ?? cmp[0]?.mandi;
    try {
      if (best) setSeries(await getForecast(best, next.cropId));
    } catch {
      setSeries([]);   // the plan still stands without its chart
    }
    setLoading(false);

    const crop = cropById(next.cropId);
    add({
      cropId: next.cropId,
      cropName: crop.name,
      cropEmoji: crop.emoji,
      qtyQtl: next.qtyQtl,
      grade: next.grade,
      storage: next.storage,
      risk: next.risk,
      action: r.action,
      headline: r.headline,
      mandi: best,
      netPerQtl: r.tranches[0]?.netPerQtl ?? 0,
      expectedGain: r.expectedGain,
      confidence: r.confidence,
    });
  }

  useEffect(() => {
    void run({ ...DEFAULT_LOT, cropId: cropParam ?? DEFAULT_LOT.cropId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cropParam]);

  const crop = cropById(lot.cropId);

  return (
    <>
      <PageHeader
        eyebrow="Advisor"
        title="What should I do with this lot?"
        lede="Pick your crop and tell us what you have. We forecast every mandi, work out what actually reaches your hand, and score every possible plan against the others."
      />

      <Section>
        <div className="grid gap-6 lg:grid-cols-[380px_1fr] lg:items-start">
          <div className="lg:sticky lg:top-24">
            <LotForm initial={lot} onSubmit={run} loading={loading} />

            <div className="card mt-4 flex gap-3 p-5">
              <Info size={16} className="mt-0.5 shrink-0 text-muted" />
              <p className="text-[0.8rem] leading-relaxed text-muted">
                Deductions used here: APMC commission 3.0%, market cess 1.05%, hamali ₹12/qtl and
                transport ₹42/km. Spoilage uses{" "}
                {crop.name.toLowerCase()}&apos;s own rate, which is why perishable crops rarely get a
                hold recommendation.
              </p>
            </div>
          </div>

          <div className="space-y-6">
            {error ? (
              <ErrorState error={error} onRetry={() => void run(lot)} />
            ) : loading && !rec ? (
              <div className="card h-[440px] animate-pulse bg-panel/40" />
            ) : rec ? (
              <RecommendationCard rec={rec} />
            ) : null}

            {series.length > 0 && (
              <div className="card p-6">
                <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <h2 className="h3">
                      {crop.emoji} {crop.name} forecast at {rec?.tranches[0]?.mandi}
                    </h2>
                    <p className="mt-1 text-[0.84rem] text-muted">
                      Next 15 days, P10 to P90. The band is the honest part.
                    </p>
                  </div>
                  <Link href="/accuracy" className="btn-ghost">
                    How accurate is this?
                    <ArrowUpRight size={15} />
                  </Link>
                </div>
                <ForecastChart data={series.slice(-90)} />
              </div>
            )}

            {rows.length > 0 && (
              <div className="card p-6">
                <h2 className="h3">If you took it elsewhere</h2>
                <p className="mt-1 text-[0.84rem] text-muted">
                  Net in hand for this exact lot at each mandi, today.
                </p>
                <div className="mt-5 space-y-2">
                  {rows.map((r) => (
                    <div key={r.mandi} className="grid grid-cols-[140px_1fr_auto] items-center gap-4">
                      <p className="truncate text-[0.86rem]">{r.mandi}</p>
                      <div className="h-6 rounded-md bg-line/60">
                        <div
                          className="h-full rounded-md bg-ink/85"
                          style={{ width: `${(r.netPerQtl / rows[0].netPerQtl) * 100}%` }}
                        />
                      </div>
                      <p className="text-[0.86rem] font-semibold tabular-nums">
                        {rupees(r.netPerQtl)}
                      </p>
                    </div>
                  ))}
                </div>
                <div className="mt-5 flex flex-wrap gap-2">
                  <Link href="/compare" className="btn-ghost">
                    Full breakdown
                    <ArrowUpRight size={15} />
                  </Link>
                  <Link href="/community" className="btn-ghost">
                    Share a truck and cut transport
                    <ArrowUpRight size={15} />
                  </Link>
                </div>
              </div>
            )}
          </div>
        </div>
      </Section>
    </>
  );
}

export default function AdvisorPage() {
  return (
    <Suspense fallback={<div className="shell py-16 text-muted">Loading…</div>}>
      <AdvisorInner />
    </Suspense>
  );
}
