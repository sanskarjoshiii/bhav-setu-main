"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import PageHeader from "@/components/PageHeader";
import Section from "@/components/Section";
import StatCard from "@/components/StatCard";
import { getAccuracy } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { ErrorState, LoadingState } from "@/components/AsyncBoundary";
import { cx, plainPct, rupees } from "@/lib/format";

const TOOLTIP = {
  contentStyle: {
    borderRadius: 12,
    border: "1px solid #E2E2D6",
    fontSize: 12,
    boxShadow: "0 8px 30px rgba(22,22,15,0.12)",
  },
};

export default function AccuracyPage() {
  // Real metrics for whichever forecaster is live. The version label below is
  // the only thing that changed when LightGBM replaced the baseline.
  const accuracy = useApi(getAccuracy, []);

  if (accuracy.loading) {
    return (
      <>
        <PageHeader
          eyebrow="Accuracy"
          title="How wrong are we, honestly?"
          lede="Measuring the live model against four dumb baselines…"
        />
        <Section>
          <LoadingState label="Reading model_registry…" rows={4} />
        </Section>
      </>
    );
  }

  if (accuracy.error || !accuracy.data) {
    return (
      <>
        <PageHeader
          eyebrow="Accuracy"
          title="How wrong are we, honestly?"
          lede="Every model here is measured against four dumb baselines on data it never trained on."
        />
        <Section>
          <ErrorState
            error={accuracy.error!}
            onRetry={accuracy.reload}
          />
        </Section>
      </>
    );
  }

  const ACCURACY = accuracy.data;

  return (
    <>
      <PageHeader
        eyebrow="Accuracy"
        title="How wrong are we, honestly?"
        lede="Every model here is measured against four dumb baselines on data it never trained on. If we could not beat “tomorrow will be the same as today”, you deserve to know."
      />

      <Section>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Uplift vs sell-now"
            value={`${ACCURACY.upliftPct >= 0 ? "+" : ""}${ACCURACY.upliftPct.toFixed(2)}%`}
            hint="Backtested on a held-out period"
            tone={ACCURACY.upliftPct >= 0 ? "up" : "down"}
          />
          <StatCard label="Win rate" value={plainPct(ACCURACY.winRate * 100, 0)} hint="Scenarios beating baseline" />
          <StatCard label="Band coverage (PICP)" value={ACCURACY.picp.toFixed(2)} hint="Target ≈ 0.80" />
          <StatCard label="Direction at 7 days" value={plainPct(ACCURACY.directionalAccuracy * 100)} hint="Target > 60%" />
        </div>
      </Section>

      <Section
        title="Against the baselines"
        description="MAPE, lower is better. Walk-forward validation with a purge gap — never a random split."
      >
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[620px]">
              <thead className="border-b border-line bg-panel/40">
                <tr>
                  <th className="th">Horizon</th>
                  <th className="th text-right">Naive</th>
                  <th className="th text-right">Seasonal</th>
                  <th className="th text-right">MA-7</th>
                  <th className="th text-right">Bhav Setu</th>
                  <th className="th text-right">Improvement</th>
                </tr>
              </thead>
              <tbody>
                {ACCURACY.mape.map((row) => {
                  const gain = ((row.naive - row.model) / row.naive) * 100;
                  return (
                    <tr key={row.horizon} className="border-b border-line/70 last:border-0">
                      <td className="td font-medium">{row.horizon} day{row.horizon > 1 ? "s" : ""}</td>
                      <td className="td text-right tabular-nums text-muted">{row.naive.toFixed(2)}%</td>
                      <td className="td text-right tabular-nums text-muted">{row.seasonal.toFixed(2)}%</td>
                      <td className="td text-right tabular-nums text-muted">{row.ma7.toFixed(2)}%</td>
                      <td className="td text-right font-semibold tabular-nums">{row.model.toFixed(2)}%</td>
                      <td
                        className={cx(
                          "td text-right font-medium tabular-nums",
                          gain >= 0 ? "text-up" : "text-down",
                        )}
                      >
                        {gain >= 0 ? `${gain.toFixed(0)}% lower` : `${Math.abs(gain).toFixed(0)}% higher`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </Section>

      <Section
        title="Following the advice versus selling immediately"
        description={
          ACCURACY.backtestScenarios
            ? `Backtested on ${ACCURACY.backtestScenarios.toLocaleString("en-IN")} lots over a held-out period the model never trained on. Positive means the plan beat selling on day one.`
            : "Run scripts/backtest.py --record to populate this."
        }
      >
        <div className="card p-6">
          {ACCURACY.backtestPerCrop.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted">
              No backtest recorded yet.
            </p>
          ) : (
            <>
              <div className="mb-5 rounded-xl border border-line bg-panel/40 p-4 text-[0.84rem] leading-relaxed">
                <strong>Overall uplift {ACCURACY.upliftPct >= 0 ? "+" : ""}
                {ACCURACY.upliftPct.toFixed(2)}%</strong>, win rate{" "}
                {plainPct(ACCURACY.winRate * 100, 0)}.{" "}
                {ACCURACY.upliftPct <= 0 ? (
                  <>
                    We are publishing this even though it is not the number we
                    hoped for. On this held-out window the timing advice did not
                    beat selling immediately — the same is true of the naive
                    baseline, so it is the holding economics, not the model. The
                    value we can defend is the Net In-Hand calculation and the
                    market comparison, both of which are arithmetic rather than
                    prediction.
                  </>
                ) : (
                  <>Uplift is total strategy rupees over total baseline rupees, not a mean of percentages.</>
                )}
              </div>
              <div className="h-[300px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={ACCURACY.backtestPerCrop} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke="#E2E2D6" vertical={false} />
                    <XAxis dataKey="crop" tick={{ fontSize: 10, fill: "#6F6F63" }} axisLine={{ stroke: "#E2E2D6" }} tickLine={false} interval={0} angle={-30} textAnchor="end" height={64} />
                    <YAxis tickFormatter={(v) => `${v}%`} tick={{ fontSize: 11, fill: "#6F6F63" }} axisLine={false} tickLine={false} width={54} />
                    <Tooltip {...TOOLTIP} formatter={(v: unknown) => `${Number(v).toFixed(2)}%`} />
                    <Bar dataKey="upliftPct" name="Uplift vs selling now" radius={[4, 4, 0, 0]}>
                      {ACCURACY.backtestPerCrop.map((row) => (
                        <Cell key={row.crop} fill={row.upliftPct >= 0 ? "#1F3D2B" : "#B4523F"} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </>
          )}
        </div>
      </Section>

      <Section>
        <div className="card p-6">
          <h3 className="h3">Are the ranges honest?</h3>
          <p className="mt-1 text-[0.84rem] text-muted">
            We claim the real price lands between P10 and P90 about 80% of the
            time. This is how often it actually did, per horizon, on data the
            model never trained on.
          </p>
          <div className="mt-5 h-[280px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={ACCURACY.coverage} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#E2E2D6" />
                <XAxis dataKey="horizon" tickFormatter={(v) => `${v}d`} tick={{ fontSize: 11, fill: "#6F6F63" }} axisLine={{ stroke: "#E2E2D6" }} tickLine={false} />
                <YAxis domain={[0.5, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} tick={{ fontSize: 11, fill: "#6F6F63" }} axisLine={false} tickLine={false} width={48} />
                <Tooltip {...TOOLTIP} formatter={(v: unknown) => plainPct(Number(v) * 100)} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line dataKey={() => 0.8} stroke="#C3C3B4" strokeDasharray="4 4" dot={false} name="Target 80%" />
                <Line dataKey="picp" stroke="#1F3D2B" strokeWidth={2} name="Observed coverage" />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-4 text-[0.78rem] text-muted">
            Slightly below 80% means the bands are a little narrow — we are
            marginally more confident than the data warrants, and we would
            rather say so than widen them until they mean nothing.
          </p>
        </div>
      </Section>

      <Section title="The model, on the record">
        <div className="card grid gap-4 p-6 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Version", ACCURACY.modelVersion],
            ["Trained", ACCURACY.trainedAt.slice(0, 10)],
            ["Training rows", ACCURACY.trainRows.toLocaleString("en-IN")],
            ["Algorithm", "LightGBM quantile ×12"],
          ].map(([label, value]) => (
            <div key={label}>
              <p className="stat-label">{label}</p>
              <p className="mt-1.5 text-[0.95rem] font-semibold">{value}</p>
            </div>
          ))}
        </div>
      </Section>
    </>
  );
}
