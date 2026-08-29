"use client";

/**
 * Soil moisture & rainfall — the second decision the product makes.
 *
 * Prices answer "when do I sell?". This answers "when do I irrigate?", from
 * the same weather pull: measured root-zone moisture, FAO-56 reference
 * evapotranspiration, and the rain that fell and is forecast.
 *
 * The page is built verdict-first on purpose. A farmer with ten minutes and a
 * small screen gets one sentence in his own language and a colour; the water
 * balance that produced it is underneath for anyone who wants to check it.
 */

import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CloudRain,
  Droplets,
  Info,
  Sprout,
  Thermometer,
  Waves,
} from "lucide-react";
import PageHeader from "@/components/PageHeader";
import Section from "@/components/Section";
import SoilMoistureChart from "@/components/SoilMoistureChart";
import { ErrorState, LoadingState } from "@/components/AsyncBoundary";
import { getCrops, getIrrigation, getMandis } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { cx } from "@/lib/format";
import { longDate } from "@/lib/seed";
import type { IrrigationAction, IrrigationAdvisory, SoilStatus } from "@/lib/types";

/** Verdict styling. Colour carries the message for a reader who skims. */
const ACTION: Record<
  IrrigationAction,
  { tone: string; ring: string; dot: string; label: string; labelMr: string }
> = {
  irrigate_now: {
    tone: "text-down",
    ring: "border-down/35 bg-down/[0.06]",
    dot: "bg-down",
    label: "Irrigate now",
    labelMr: "आताच पाणी द्या",
  },
  irrigate_soon: {
    tone: "text-[#8A5A00]",
    ring: "border-[#8A5A00]/30 bg-[#8A5A00]/[0.06]",
    dot: "bg-[#8A5A00]",
    label: "Irrigate soon",
    labelMr: "लवकरच पाणी द्या",
  },
  wait: {
    tone: "text-accent",
    ring: "border-accent/30 bg-accent/[0.05]",
    dot: "bg-accent",
    label: "Wait for the rain",
    labelMr: "पावसाची वाट पहा",
  },
  hold_off: {
    tone: "text-up",
    ring: "border-up/30 bg-up/[0.05]",
    dot: "bg-up",
    label: "No irrigation needed",
    labelMr: "पाण्याची गरज नाही",
  },
  waterlogged: {
    tone: "text-down",
    ring: "border-down/35 bg-down/[0.06]",
    dot: "bg-down",
    label: "Water-logged",
    labelMr: "पाणी साचले आहे",
  },
};

const SOIL_LABEL: Record<SoilStatus, string> = {
  saturated: "Saturated",
  wet: "Wet",
  adequate: "Adequate",
  dry: "Dry",
  critical: "Critical",
  unknown: "No reading",
};

/** What a farmer does today without this page, and with it. */
function beforeAfter(a: IrrigationAdvisory): { before: string; after: string } {
  const dry = a.action === "irrigate_now" || a.action === "irrigate_soon";
  if (a.action === "waterlogged") {
    return {
      before: "Irrigates on the weekly rota, on top of a soil that is already at saturation — roots sit in water and rot risk climbs.",
      after: `Skips this cycle. Root-zone moisture is ${a.soilMoisture?.toFixed(2)} m³/m³, above the ${a.fieldCapacity} field capacity; the crop needs air, not water.`,
    };
  }
  if (dry) {
    return {
      before: `Waters by the calendar or by eye, and in a week that ran a ${a.deficit7dMm.toFixed(0)} mm shortfall that usually means finding out too late.`,
      after: `Knows the last seven days demanded ${a.cropDemand7dMm.toFixed(0)} mm and rain supplied only ${a.rain7dMm.toFixed(0)} mm, with ${a.rainForecast7dMm.toFixed(0)} mm coming. Irrigates before the crop is stressed.`,
    };
  }
  return {
    before: `Runs the pump anyway — diesel, labour and a half-day, because there is no way to know the soil is still holding water.`,
    after: `Sees ${a.rainForecast7dMm.toFixed(0)} mm forecast against a ${a.deficit7dMm.toFixed(0)} mm shortfall and skips a cycle. That is a pump-run saved on evidence, not a guess.`,
  };
}

export default function IrrigationPage() {
  const { language } = useAuth();
  const [crop, setCrop] = useState("onion");
  const [mandi, setMandi] = useState("");

  const crops = useApi(() => getCrops(), []);
  const mandis = useApi(() => getMandis(), []);
  const advisory = useApi(
    () => getIrrigation(crop, mandi || undefined, 45),
    [crop, mandi],
  );

  // Every seeded crop has an FAO-56 coefficient in config/irrigation.yaml. If
  // one ever does not, the backend refuses it with a readable 422 and the page
  // shows that sentence — better than hiding the crop and leaving a farmer
  // wondering where it went.
  const cropOptions = useMemo(
    () => [...(crops.data ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [crops.data],
  );

  return (
    <>
      <PageHeader
        eyebrow="Soil moisture & rainfall"
        title="When to irrigate"
        lede="Root-zone soil moisture, crop water demand and the rain that is coming — turned into one decision for your crop and your market."
      />

      <Section>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_auto]">
          <div>
            <label className="label" htmlFor="crop">
              Crop
            </label>
            <select
              id="crop"
              className="select"
              value={crop}
              onChange={(e) => setCrop(e.target.value)}
            >
              {cropOptions.length === 0 && <option value="onion">Onion</option>}
              {cropOptions.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.name}
                  {c.nameMr ? ` · ${c.nameMr}` : ""}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="label" htmlFor="mandi">
              Nearest market
            </label>
            <select
              id="mandi"
              className="select"
              value={mandi}
              onChange={(e) => setMandi(e.target.value)}
            >
              <option value="">Nearest weather station</option>
              {(mandis.data ?? []).map((m) => (
                <option key={m.id} value={m.name}>
                  {m.name} · {m.district}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-end">
            <p className="text-[0.78rem] leading-relaxed text-muted">
              Weather is measured at the market&rsquo;s
              <br className="hidden lg:block" /> coordinates, not your field.
            </p>
          </div>
        </div>
      </Section>

      {advisory.loading && (
        <Section>
          <LoadingState label="Reading the soil…" rows={4} />
        </Section>
      )}

      {advisory.error && (
        <Section>
          <ErrorState error={advisory.error} onRetry={advisory.reload} />
        </Section>
      )}

      {advisory.data && <Advisory a={advisory.data} language={language} />}
    </>
  );
}

function Advisory({
  a,
  language,
}: {
  a: IrrigationAdvisory;
  language: "en" | "mr";
}) {
  const style = ACTION[a.action];
  const ba = beforeAfter(a);
  const headline = language === "mr" ? a.headlineMr : a.headline;
  const secondary = language === "mr" ? a.headline : a.headlineMr;

  return (
    <>
      {/* ── the verdict ─────────────────────────────────────────────── */}
      <Section>
        <div className={cx("card border p-6 sm:p-8", style.ring)}>
          <div className="flex flex-wrap items-center gap-3">
            <span className={cx("h-2.5 w-2.5 rounded-full", style.dot)} />
            <span className={cx("eyebrow", style.tone)}>{style.label}</span>
            <span className="chip">
              {a.crop} · {a.mandi}
            </span>
            <span className="chip">{longDate(a.asOf)}</span>
            <span
              className={cx(
                "chip",
                a.confidence === "high" && "border-up/40 text-up",
                a.confidence === "low" && "border-down/40 text-down",
              )}
            >
              {a.confidence} confidence
            </span>
          </div>

          <h2 className="h2 mt-4 max-w-3xl">{headline}</h2>
          <p className="lede mt-2 max-w-3xl">{secondary}</p>
          <p className="mt-4 max-w-3xl text-[0.95rem] leading-relaxed text-ink/80">
            {a.detail}
          </p>
        </div>
      </Section>

      {/* ── the numbers behind it ───────────────────────────────────── */}
      <Section
        title="The water balance"
        description="FAO-56: crop demand is Kc × ET₀, and irrigation has to supply whatever the rain did not."
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Metric
            icon={<Droplets size={15} />}
            label="Root-zone moisture"
            value={
              a.soilMoisture == null ? "—" : `${a.soilMoisture.toFixed(3)}`
            }
            unit="m³/m³"
            hint={`${SOIL_LABEL[a.soilStatus]} · irrigate below ${a.refillPoint}`}
          />
          <Metric
            icon={<Sprout size={15} />}
            label="Crop demand, 7 days"
            value={a.cropDemand7dMm.toFixed(0)}
            unit="mm"
            hint={`Kc ${a.kc}${a.kcIsAssumed ? " (estimated)" : ""} × ET₀ ${a.et07dMm.toFixed(0)} mm`}
          />
          <Metric
            icon={<CloudRain size={15} />}
            label="Rain, last 7 days"
            value={a.rain7dMm.toFixed(0)}
            unit="mm"
            hint={`${a.rainForecast7dMm.toFixed(0)} mm forecast in the next 7`}
          />
          <Metric
            icon={<Waves size={15} />}
            label="Shortfall"
            value={a.deficit7dMm.toFixed(0)}
            unit="mm"
            hint={
              a.deficit7dMm > 0
                ? "What irrigation would have to make up"
                : "Rain covered the crop's use"
            }
            tone={a.deficit7dMm > 25 ? "down" : undefined}
          />
        </div>

        {a.soilTempC != null && (
          <p className="mt-3 flex items-center gap-2 text-[0.82rem] text-muted">
            <Thermometer size={14} />
            Surface soil temperature {a.soilTempC.toFixed(1)}°C
          </p>
        )}
      </Section>

      {/* ── the trend ───────────────────────────────────────────────── */}
      <Section
        title="Soil moisture, and where it is heading"
        description="Measured to the dashed line, forecast beyond it. The red band is where irrigation stops being optional."
      >
        <div className="card p-4 sm:p-5">
          <SoilMoistureChart
            data={a.series}
            fieldCapacity={a.fieldCapacity}
            refillPoint={a.refillPoint}
            wiltingPoint={a.wiltingPoint}
          />
        </div>
      </Section>

      {/* ── before / after ──────────────────────────────────────────── */}
      <Section
        title="What changes"
        description="The same week, decided two ways."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <div className="card p-6">
            <p className="eyebrow text-muted">Without this page</p>
            <p className="mt-3 text-[0.95rem] leading-relaxed text-ink/80">
              {ba.before}
            </p>
          </div>
          <div className={cx("card border p-6", style.ring)}>
            <p className={cx("eyebrow", style.tone)}>With it</p>
            <p className="mt-3 text-[0.95rem] leading-relaxed text-ink/80">
              {ba.after}
            </p>
          </div>
        </div>
      </Section>

      {/* ── what this is not ────────────────────────────────────────── */}
      <Section title="What this advisory does not know">
        <div className="card space-y-3 p-6 text-[0.9rem] leading-relaxed text-ink/75">
          <p className="flex gap-3">
            <Info size={16} className="mt-0.5 shrink-0 text-muted" />
            <span>
              Soil moisture is modelled at the market&rsquo;s coordinates, not measured
              in your field. Thresholds are derived from the observed range across our
              seventeen markets, so they are good enough to say &ldquo;dry&rdquo; or
              &ldquo;wet&rdquo; — not good enough to schedule a pump to the hour.
            </span>
          </p>
          <p className="flex gap-3">
            <Info size={16} className="mt-0.5 shrink-0 text-muted" />
            <span>
              We do not know your sowing date, so the mid-season crop coefficient is
              used throughout. That <strong>over-states</strong> demand for a young
              crop — the advice errs towards irrigating.
            </span>
          </p>
          {a.kcIsAssumed && (
            <p className="flex gap-3">
              <AlertTriangle size={16} className="mt-0.5 shrink-0 text-[#8A5A00]" />
              <span>
                FAO-56 has no table row for {a.crop}. The coefficient {a.kc} is the
                midpoint of the comparable range, which is why this reads as
                medium confidence rather than high.
              </span>
            </p>
          )}
          {a.soilMoisture == null && (
            <p className="flex gap-3">
              <AlertTriangle size={16} className="mt-0.5 shrink-0 text-down" />
              <span>
                No soil reading was available for this market, so the advice rests on
                the rain-and-demand balance alone. That is weaker, and the confidence
                above says so.
              </span>
            </p>
          )}
          <p className="flex gap-3">
            <Info size={16} className="mt-0.5 shrink-0 text-muted" />
            <span>
              This stream is deliberately <strong>not</strong> wired into the price
              forecast. Soil moisture in Nashik does not move the onion price in any
              way we could defend.
            </span>
          </p>
        </div>
      </Section>
    </>
  );
}

function Metric({
  icon,
  label,
  value,
  unit,
  hint,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit: string;
  hint: string;
  tone?: "down";
}) {
  return (
    <div className="stat">
      <p className="stat-label flex items-center gap-1.5">
        <span className="text-muted">{icon}</span>
        {label}
      </p>
      <p className={cx("stat-value", tone === "down" && "text-down")}>
        {value}
        <span className="ml-1 text-[0.8rem] font-medium text-muted">{unit}</span>
      </p>
      <p className="mt-1 text-[0.74rem] leading-snug text-muted">{hint}</p>
    </div>
  );
}
