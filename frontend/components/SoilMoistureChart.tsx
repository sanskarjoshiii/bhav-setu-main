"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { SoilPoint } from "@/lib/types";
import { shortDate } from "@/lib/seed";

/**
 * Root-zone soil moisture against the two lines that decide the advice.
 *
 * The refill point is the whole story: while the line is above it the crop is
 * drinking freely, and when it crosses below, irrigation is overdue. Drawing
 * the thresholds rather than printing a number is the difference between a
 * farmer seeing "0.27 m³/m³" and seeing a line heading for the floor.
 *
 * Rainfall is drawn underneath as a filled area on its own scale, because
 * "the line stopped falling because it rained" is the question anyone asks
 * first.
 */
export default function SoilMoistureChart({
  data,
  fieldCapacity,
  refillPoint,
  wiltingPoint,
  height = 300,
}: {
  data: SoilPoint[];
  fieldCapacity: number;
  refillPoint: number;
  wiltingPoint: number;
  height?: number;
}) {
  const rows = data.map((d) => ({
    date: d.date,
    soil: d.soilMoistureRoot,
    rain: d.rainfallMm ?? 0,
  }));

  const firstForecast = data.find((d) => d.isForecast)?.date;
  const values = data
    .map((d) => d.soilMoistureRoot)
    .filter((v): v is number => typeof v === "number");

  if (values.length === 0) {
    return (
      <div
        style={{ height }}
        className="grid place-items-center rounded-xl bg-panel text-[0.85rem] text-muted"
      >
        No soil readings for this market yet.
      </div>
    );
  }

  const lo = Math.min(...values, wiltingPoint) - 0.03;
  const hi = Math.max(...values, fieldCapacity) + 0.03;
  const maxRain = Math.max(10, ...rows.map((r) => r.rain));

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#E2E2D6" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={shortDate}
            tick={{ fontSize: 11, fill: "#6F6F63" }}
            axisLine={{ stroke: "#E2E2D6" }}
            tickLine={false}
            minTickGap={44}
          />
          <YAxis
            yAxisId="soil"
            domain={[Number(lo.toFixed(2)), Number(hi.toFixed(2))]}
            tickFormatter={(v) => Number(v).toFixed(2)}
            tick={{ fontSize: 11, fill: "#6F6F63" }}
            axisLine={false}
            tickLine={false}
            width={46}
          />
          <YAxis yAxisId="rain" orientation="right" domain={[0, maxRain * 3]} hide />

          {/* Below the refill point is the zone that costs yield. */}
          <ReferenceArea
            yAxisId="soil"
            y1={lo}
            y2={refillPoint}
            fill="#B4342B"
            fillOpacity={0.07}
            ifOverflow="hidden"
          />

          <Area
            yAxisId="rain"
            dataKey="rain"
            stroke="none"
            fill="#8AA79A"
            fillOpacity={0.3}
            isAnimationActive={false}
          />

          <ReferenceLine
            yAxisId="soil"
            y={fieldCapacity}
            stroke="#177245"
            strokeDasharray="4 4"
            strokeOpacity={0.7}
            label={{
              value: "field capacity",
              position: "insideTopRight",
              fontSize: 10,
              fill: "#6F6F63",
            }}
          />
          <ReferenceLine
            yAxisId="soil"
            y={refillPoint}
            stroke="#B4342B"
            strokeDasharray="4 4"
            strokeOpacity={0.8}
            label={{
              value: "irrigate below here",
              position: "insideBottomRight",
              fontSize: 10,
              fill: "#B4342B",
            }}
          />
          {firstForecast && (
            <ReferenceLine
              yAxisId="soil"
              x={firstForecast}
              stroke="#16160F"
              strokeDasharray="3 3"
              strokeOpacity={0.35}
              label={{
                value: "forecast",
                position: "insideTopLeft",
                fontSize: 10,
                fill: "#6F6F63",
              }}
            />
          )}

          <Line
            yAxisId="soil"
            dataKey="soil"
            stroke="#16160F"
            strokeWidth={1.9}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />

          <Tooltip
            contentStyle={{
              borderRadius: 12,
              border: "1px solid #E2E2D6",
              fontSize: 12,
              boxShadow: "0 8px 30px rgba(22,22,15,0.12)",
            }}
            labelFormatter={(l) => shortDate(String(l))}
            formatter={(value: unknown, name: string) => {
              if (value == null) return ["—", name];
              if (name === "rain") return [`${Number(value).toFixed(1)} mm`, "Rainfall"];
              return [`${Number(value).toFixed(3)} m³/m³`, "Root-zone moisture"];
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
