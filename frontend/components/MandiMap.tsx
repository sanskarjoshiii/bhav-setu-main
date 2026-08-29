"use client";

/**
 * The markets we carry, plotted for real.
 *
 * Coordinates and prices come from the API, not a mock file. When a farmer is
 * signed in, his village is the white marker and lines are drawn to each market
 * — that line is the transport cost the compare page charges him for, made
 * visible. His own district is highlighted; the others are still shown, because
 * "the far market pays more but costs more to reach" is the whole point.
 */

import { Fragment, useEffect, useMemo } from "react";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { rupees } from "@/lib/format";

export interface MapMarket {
  id: number;
  name: string;
  district: string;
  lat: number;
  lon: number;
  todayModal: number;
  distanceKm: number;
  arrivalQtl: number;
}

export interface MapOrigin {
  name: string;
  lat: number;
  lon: number;
}

/** Fits the view to everything plotted, so the map never opens blank or too far out. */
function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView(points[0], 10);
      return;
    }
    map.fitBounds(points, { padding: [45, 45] });
  }, [map, points]);
  return null;
}

export default function MandiMap({
  markets,
  origin,
  highlightDistrict,
  height = 420,
}: {
  markets: MapMarket[];
  origin?: MapOrigin | null;
  /** The signed-in farmer's district — drawn filled, the rest hollow. */
  highlightDistrict?: string | null;
  height?: number;
}) {
  const points = useMemo<[number, number][]>(() => {
    const pts = markets
      .filter((m) => Number.isFinite(m.lat) && Number.isFinite(m.lon))
      .map((m) => [m.lat, m.lon] as [number, number]);
    if (origin) pts.push([origin.lat, origin.lon]);
    return pts;
  }, [markets, origin]);

  if (points.length === 0) {
    return (
      <div
        className="grid place-items-center rounded-2xl border border-dashed border-line text-sm text-muted"
        style={{ height }}
      >
        No markets to plot yet.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-line" style={{ height }}>
      <MapContainer center={points[0]} zoom={8} scrollWheelZoom={false}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitBounds points={points} />

        {markets.map((m) => {
          const mine = !highlightDistrict || m.district === highlightDistrict;
          return (
            <Fragment key={m.id}>
              {origin && (
                <Polyline
                  positions={[[origin.lat, origin.lon], [m.lat, m.lon]]}
                  pathOptions={{
                    color: "#16160F",
                    weight: 1,
                    opacity: mine ? 0.35 : 0.12,
                    dashArray: "4 5",
                  }}
                />
              )}
              <CircleMarker
                center={[m.lat, m.lon]}
                radius={mine ? 10 : 7}
                pathOptions={{
                  color: mine ? "#1F3D2B" : "#16160F",
                  fillColor: mine ? "#1F3D2B" : "#FFFFFF",
                  fillOpacity: mine ? 0.85 : 0.9,
                  weight: 2,
                }}
              >
                <Popup>
                  <p className="text-[0.9rem] font-semibold">{m.name}</p>
                  <p className="text-[0.78rem] opacity-70">{m.district} district</p>
                  <p className="text-[0.8rem]">
                    {m.todayModal > 0 ? `${rupees(m.todayModal)}/qtl` : "no price today"}
                    {m.distanceKm > 0 && ` · ${m.distanceKm} km`}
                  </p>
                  {m.arrivalQtl > 0 && (
                    <p className="text-[0.75rem] opacity-70">
                      Arrivals {Math.round(m.arrivalQtl).toLocaleString("en-IN")} qtl
                    </p>
                  )}
                </Popup>
              </CircleMarker>
            </Fragment>
          );
        })}

        {origin && (
          <CircleMarker
            center={[origin.lat, origin.lon]}
            radius={7}
            pathOptions={{ color: "#1F3D2B", fillColor: "#FFFFFF", fillOpacity: 1, weight: 3 }}
          >
            <Popup>
              <p className="text-[0.9rem] font-semibold">{origin.name}</p>
              <p className="text-[0.8rem]">Your village</p>
            </Popup>
          </CircleMarker>
        )}
      </MapContainer>
    </div>
  );
}
