"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, Loader2, MapPin, Sprout } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { ApiError, getLocations, type ApiDistrictLocation } from "@/lib/api";
import type { Language, RiskProfile } from "@/lib/types";
import { cx } from "@/lib/format";

const RISKS: { value: RiskProfile; label: string; hint: string }[] = [
  { value: "cautious", label: "Cautious", hint: "I have a loan to service" },
  { value: "balanced", label: "Balanced", hint: "Some risk is fine" },
  { value: "aggressive", label: "Aggressive", hint: "I can wait for a better price" },
];

const LANGUAGES: { value: Language; label: string }[] = [
  { value: "mr", label: "मराठी" },
  { value: "en", label: "English" },
];

export default function SignupPage() {
  const router = useRouter();
  const { requestOtp, verifyOtp, setLanguage } = useAuth();

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [devCode, setDevCode] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [district, setDistrict] = useState("");
  const [village, setVillage] = useState("");
  const [language, setLang] = useState<Language>("mr");
  const [risk, setRisk] = useState<RiskProfile>("balanced");

  // Only the districts we actually carry prices for. Offering one we cannot
  // serve would take a registration and then have nothing to say.
  const [locations, setLocations] = useState<ApiDistrictLocation[]>([]);
  useEffect(() => {
    getLocations()
      .then((all) => {
        const usable = all.filter((d) => d.hasData);
        setLocations(usable);
        if (usable[0]) {
          setDistrict(usable[0].name);
          setVillage(usable[0].villages[0]?.name ?? "");
        }
      })
      .catch(() => setError("Could not load districts — is the backend running?"));
  }, []);

  const villages = useMemo(
    () => locations.find((d) => d.name === district)?.villages ?? [],
    [locations, district],
  );

  async function sendCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const challenge = await requestOtp(phone);
      setDevCode(challenge.devCode ?? null);
      setStep(3);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send the code");
    } finally {
      setBusy(false);
    }
  }

  async function finish(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await verifyOtp(phone, otp, {
        name, village, district, language, riskProfile: risk,
      });
      setLanguage(language);
      router.push("/advisor");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create your account");
      setBusy(false);
    }
  }

  const selected = villages.find((v) => v.name === village);

  return (
    <div className="shell grid min-h-[70vh] items-center gap-12 py-10 lg:grid-cols-2">
      <div className="hidden lg:block">
        <span className="grid h-11 w-11 place-items-center rounded-full bg-ink text-cream">
          <Sprout size={20} />
        </span>
        <h1 className="mt-6 text-[2.4rem] font-bold leading-[1.1] tracking-[-0.02em]">
          Advice for your lot, your village
        </h1>
        <p className="mt-4 max-w-md text-[0.95rem] leading-relaxed text-muted">
          We use your village to work out the diesel to each market — the cost that
          decides which mandi actually pays you the most.
        </p>
        <ul className="mt-8 space-y-3">
          {["Real mandi prices, updated daily",
            "Net in hand after commission, cess and transport",
            "One clear recommendation, with the reasoning"].map((t) => (
            <li key={t} className="flex gap-3 text-[0.88rem] text-muted">
              <Check size={17} className="mt-0.5 shrink-0 text-up" />
              {t}
            </li>
          ))}
        </ul>
      </div>

      <div className="card p-7">
        <div className="mb-5 flex items-center gap-2">
          {[1, 2, 3].map((n) => (
            <div
              key={n}
              className={cx(
                "h-1.5 flex-1 rounded-full transition",
                step >= n ? "bg-ink" : "bg-line",
              )}
            />
          ))}
        </div>

        {step === 1 && (
          <form
            onSubmit={(e) => { e.preventDefault(); setStep(2); }}
            className="space-y-5"
          >
            <div>
              <h2 className="h3">Who are you?</h2>
              <p className="mt-1 text-[0.86rem] text-muted">Step 1 of 3</p>
            </div>

            <div>
              <label className="label" htmlFor="name">Your name</label>
              <input id="name" value={name} onChange={(e) => setName(e.target.value)}
                     placeholder="Ramesh Patil" required className="input" />
            </div>

            <div>
              <label className="label" htmlFor="phone">Mobile number</label>
              <input id="phone" value={phone} onChange={(e) => setPhone(e.target.value)}
                     placeholder="98765 43210" inputMode="tel" autoComplete="tel"
                     required className="input" />
            </div>

            <div>
              <p className="label">Language</p>
              <div className="flex gap-2">
                {LANGUAGES.map((l) => (
                  <button key={l.value} type="button" onClick={() => setLang(l.value)}
                          className={cx("chip", language === l.value && "chip-active")}>
                    {l.label}
                  </button>
                ))}
              </div>
            </div>

            <button type="submit" disabled={!name || phone.length < 10}
                    className="btn-primary w-full">
              Continue <ArrowRight size={16} />
            </button>

            <p className="text-center text-[0.84rem] text-muted">
              Already registered?{" "}
              <Link href="/login" className="font-medium text-ink underline underline-offset-4">
                Sign in
              </Link>
            </p>
          </form>
        )}

        {step === 2 && (
          <form onSubmit={sendCode} className="space-y-5">
            <div>
              <h2 className="h3">Where do you farm?</h2>
              <p className="mt-1 text-[0.86rem] text-muted">
                Step 2 of 3 — this sets your distance to each market.
              </p>
            </div>

            <div>
              <label className="label" htmlFor="district">District</label>
              <select
                id="district"
                value={district}
                onChange={(e) => {
                  setDistrict(e.target.value);
                  const next = locations.find((d) => d.name === e.target.value);
                  setVillage(next?.villages[0]?.name ?? "");
                }}
                className="input"
              >
                {locations.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name} — {d.nameMr}
                  </option>
                ))}
              </select>
              <p className="mt-1.5 text-[0.75rem] text-muted">
                We currently carry prices for {locations.length} districts.
              </p>
            </div>

            <div>
              <label className="label" htmlFor="village">Village / taluka</label>
              <select id="village" value={village}
                      onChange={(e) => setVillage(e.target.value)} className="input">
                {villages.map((v) => (
                  <option key={v.name} value={v.name}>
                    {v.name} — {v.nameMr}
                  </option>
                ))}
              </select>
              {selected?.distanceToMarketKm != null && (
                <p className="mt-1.5 flex items-center gap-1.5 text-[0.75rem] text-muted">
                  <MapPin size={12} />
                  About {selected.distanceToMarketKm} km to{" "}
                  {locations.find((d) => d.name === district)?.market} market.
                </p>
              )}
            </div>

            <div>
              <p className="label">How much risk can you take?</p>
              <div className="space-y-2">
                {RISKS.map((r) => (
                  <button key={r.value} type="button" onClick={() => setRisk(r.value)}
                          className={cx(
                            "flex w-full items-center justify-between rounded-xl border px-4 py-3 text-left transition",
                            risk === r.value
                              ? "border-ink bg-ink text-cream"
                              : "border-line bg-card hover:border-ink/30",
                          )}>
                    <span className="text-[0.9rem] font-medium">{r.label}</span>
                    <span className={cx("text-[0.78rem]", risk === r.value ? "text-cream/70" : "text-muted")}>
                      {r.hint}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {error && (
              <p role="alert" className="rounded-lg bg-down/10 px-3 py-2 text-[0.84rem] text-down">
                {error}
              </p>
            )}

            <div className="flex gap-2">
              <button type="button" onClick={() => setStep(1)} className="btn-ghost">Back</button>
              <button type="submit" disabled={busy || !district || !village}
                      className="btn-primary flex-1">
                {busy ? <Loader2 size={16} className="animate-spin" /> : <>Send code <ArrowRight size={16} /></>}
              </button>
            </div>
          </form>
        )}

        {step === 3 && (
          <form onSubmit={finish} className="space-y-5">
            <div>
              <h2 className="h3">Confirm your number</h2>
              <p className="mt-1 text-[0.86rem] text-muted">
                Step 3 of 3 — a six-digit code was sent to {phone}.
              </p>
            </div>

            {devCode && (
              <p className="rounded-lg border border-dashed border-line bg-panel/50 px-3 py-2 text-[0.82rem]">
                <strong>Demo mode:</strong> your code is{" "}
                <span className="font-mono text-[0.95rem] font-semibold">{devCode}</span>.
                <span className="block text-[0.74rem] text-muted">
                  Shown because the server is on <code>otp.channel: log</code>.
                </span>
              </p>
            )}

            <div>
              <label className="label" htmlFor="otp">Six-digit code</label>
              <input id="otp" value={otp}
                     onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                     placeholder="••••••" inputMode="numeric" autoComplete="one-time-code"
                     required className="input text-center font-mono text-[1.3rem] tracking-[0.4em]" />
            </div>

            {error && (
              <p role="alert" className="rounded-lg bg-down/10 px-3 py-2 text-[0.84rem] text-down">
                {error}
              </p>
            )}

            <div className="flex gap-2">
              <button type="button" onClick={() => setStep(2)} className="btn-ghost">Back</button>
              <button type="submit" disabled={busy || otp.length < 6} className="btn-primary flex-1">
                {busy ? <Loader2 size={16} className="animate-spin" /> : <>Create account <ArrowRight size={16} /></>}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
