"use client";

import { useState } from "react";
import { BadgeCheck, Camera, FileText, Users } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import Section from "@/components/Section";
import StatCard from "@/components/StatCard";
import { getMandis, getTransparency } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { ErrorState, LoadingState } from "@/components/AsyncBoundary";
import { cx, plainPct, qtl, rupees } from "@/lib/format";
import { longDate } from "@/lib/seed";

const VERIFICATION_ICON = {
  slip_photo: Camera,
  fpo_verified: BadgeCheck,
  self_reported: FileText,
} as const;

export default function TransparencyPage() {
  const [filter, setFilter] = useState<string>("all");
  const state = useApi(getTransparency, []);
  const mandiState = useApi(() => getMandis(), []);

  const SALE_REPORTS = state.data?.reports ?? [];
  const TRANSPARENCY_SCORES = state.data?.scores ?? [];
  const MANDIS = mandiState.data ?? [];

  // Totals are derived from the reports themselves rather than stored, so the
  // headline numbers cannot drift away from the table underneath them.
  const gaps = SALE_REPORTS.map((r) => r.gapPct).sort((a, b) => a - b);
  const TRANSPARENCY_TOTALS = {
    reports: SALE_REPORTS.length,
    farmers: new Set(SALE_REPORTS.map((r) => r.farmer)).size,
    villages: new Set(SALE_REPORTS.map((r) => r.village).filter(Boolean)).size,
    medianGap: gaps.length ? gaps[Math.floor(gaps.length / 2)] : 0,
    followedAdvice: SALE_REPORTS.filter((r) => r.followedAdvice).length,
  };

  const reports = filter === "all" ? SALE_REPORTS : SALE_REPORTS.filter((r) => r.mandi === filter);

  return (
    <>
      <PageHeader
        eyebrow="Transparency"
        title="What the board says, and what he took home"
        lede="Farmers report the price they were quoted and the money that actually reached them. The gap between those two numbers scores every mandi — and no competing product has this table."
      />

      <Section>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Sale reports" value={String(TRANSPARENCY_TOTALS.reports)} hint="Last 60 days" />
          <StatCard label="Farmers reporting" value={String(TRANSPARENCY_TOTALS.farmers)} hint={`${TRANSPARENCY_TOTALS.villages} villages`} />
          <StatCard label="Median gap" value={plainPct(TRANSPARENCY_TOTALS.medianGap)} hint="Quoted vs received" tone="down" />
          <StatCard
            label="Followed our advice"
            value={plainPct(TRANSPARENCY_TOTALS.reports ? (TRANSPARENCY_TOTALS.followedAdvice / TRANSPARENCY_TOTALS.reports) * 100 : 0, 0)}
            hint={`${TRANSPARENCY_TOTALS.followedAdvice} of ${TRANSPARENCY_TOTALS.reports} reports`}
          />
        </div>
      </Section>

      <Section
        title="Transparency score by mandi"
        description="10 means the money in hand closely matches the quoted rate. Lower means more disappears between the two."
      >
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {TRANSPARENCY_SCORES.map((s) => (
            <div key={s.mandi} className="card p-6">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="h3">{s.mandi}</h3>
                  <p className="mt-1 text-[0.78rem] text-muted">{s.reports} reports</p>
                </div>
                <div className="text-right">
                  <p
                    className={cx(
                      "text-[1.9rem] font-bold leading-none tracking-[-0.02em]",
                      s.score >= 7 ? "text-up" : s.score >= 5 ? "text-ink" : "text-down"
                    )}
                  >
                    {s.score.toFixed(1)}
                  </p>
                  <p className="text-[0.68rem] uppercase tracking-wider text-muted">out of 10</p>
                </div>
              </div>

              <div className="mt-4 h-1.5 w-full overflow-hidden rounded-full bg-line">
                <div
                  className={cx(
                    "h-full rounded-full",
                    s.score >= 7 ? "bg-up" : s.score >= 5 ? "bg-ink" : "bg-down"
                  )}
                  style={{ width: `${s.score * 10}%` }}
                />
              </div>

              <p className="mt-3 text-[0.82rem] text-muted">
                Median gap <span className="font-semibold text-ink">{plainPct(s.medianGapPct)}</span>{" "}
                between the quoted rate and the money received.
              </p>
            </div>
          ))}
        </div>
      </Section>

      <Section
        title="Every report"
        description="Self-reported by farmers over WhatsApp. Slip photos and FPO checks carry more weight."
        aside={
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => setFilter("all")}
              className={cx("chip", filter === "all" && "chip-active")}
            >
              All
            </button>
            {MANDIS.map((m) => (
              <button
                key={m.id}
                onClick={() => setFilter(m.name)}
                className={cx("chip", filter === m.name && "chip-active")}
              >
                {m.name}
              </button>
            ))}
          </div>
        }
      >
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[840px]">
              <thead className="border-b border-line bg-panel/40">
                <tr>
                  <th className="th">Report</th>
                  <th className="th">Farmer</th>
                  <th className="th">Mandi</th>
                  <th className="th">Date</th>
                  <th className="th text-right">Quantity</th>
                  <th className="th text-right">Quoted</th>
                  <th className="th text-right">Received</th>
                  <th className="th text-right">Gap</th>
                  <th className="th">Verified</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((r) => {
                  const Icon = VERIFICATION_ICON[r.verification];
                  return (
                    <tr key={r.id} className="border-b border-line/70 last:border-0 hover:bg-panel/30">
                      <td className="td font-mono text-[0.78rem] text-muted">{r.id}</td>
                      <td className="td">
                        <p className="font-medium">{r.farmer}</p>
                        <p className="text-[0.75rem] text-muted">{r.village}</p>
                      </td>
                      <td className="td">{r.mandi}</td>
                      <td className="td text-muted">{longDate(r.date)}</td>
                      <td className="td text-right tabular-nums text-muted">{qtl(r.qtl)}</td>
                      <td className="td text-right tabular-nums text-muted">
                        {rupees(r.quotedPerQtl)}
                      </td>
                      <td className="td text-right font-semibold tabular-nums">
                        {rupees(r.receivedPerQtl)}
                      </td>
                      <td className="td text-right font-medium tabular-nums text-down">
                        −{plainPct(r.gapPct)}
                      </td>
                      <td className="td">
                        <span className="chip">
                          <Icon size={12} />
                          {r.verification.replace(/_/g, " ")}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </Section>

      <Section>
        <div className="panel flex flex-col items-start gap-5 rounded-2xl px-8 py-9 md:flex-row md:items-center md:justify-between">
          <div className="flex gap-4">
            <Users size={22} className="mt-0.5 shrink-0" />
            <div className="max-w-xl">
              <h2 className="h3">This table is the moat</h2>
              <p className="mt-2 text-[0.9rem] leading-relaxed text-muted">
                Anyone can scrape a price board. Only a system farmers actually talk to learns what
                they were really paid — and that is what makes the next recommendation better.
              </p>
            </div>
          </div>
          <button className="btn-primary shrink-0">Report a sale</button>
        </div>
      </Section>
    </>
  );
}
