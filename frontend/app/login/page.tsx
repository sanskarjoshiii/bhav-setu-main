"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Loader2, MessageCircle, ShieldCheck, Sprout } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";

const REASSURANCE = [
  { icon: ShieldCheck, text: "We never share your number with any trader, agent or buyer." },
  { icon: MessageCircle, text: "The code expires in ten minutes and works only once." },
];

export default function LoginPage() {
  const router = useRouter();
  const { requestOtp, verifyOtp } = useAuth();

  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [stage, setStage] = useState<"phone" | "otp">("phone");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Shown only while the backend is on `otp.channel: log` — the demo path. */
  const [devCode, setDevCode] = useState<string | null>(null);

  async function sendCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const challenge = await requestOtp(phone);
      setDevCode(challenge.devCode ?? null);
      setStage("otp");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send the code");
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const farmer = await verifyOtp(phone, otp);
      // A number we have never seen still needs a name and village.
      router.push(farmer.name ? "/dashboard" : "/signup");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign you in");
      setBusy(false);
    }
  }

  return (
    <div className="shell grid min-h-[70vh] items-center gap-12 py-10 lg:grid-cols-2">
      <div className="hidden lg:block">
        <span className="grid h-11 w-11 place-items-center rounded-full bg-ink text-cream">
          <Sprout size={20} />
        </span>
        <h1 className="mt-6 text-[2.4rem] font-bold leading-[1.1] tracking-[-0.02em]">
          Welcome back
        </h1>
        <p className="mt-4 max-w-md text-[0.95rem] leading-relaxed text-muted">
          Your lots, your sale reports and your history are tied to your phone number.
          No password to remember.
        </p>
        <ul className="mt-8 space-y-4">
          {REASSURANCE.map(({ icon: Icon, text }) => (
            <li key={text} className="flex gap-3 text-[0.88rem] text-muted">
              <Icon size={17} className="mt-0.5 shrink-0 text-ink/60" />
              {text}
            </li>
          ))}
        </ul>
      </div>

      <div className="card p-7">
        {stage === "phone" ? (
          <form onSubmit={sendCode} className="space-y-5">
            <div>
              <h2 className="h3">Sign in</h2>
              <p className="mt-1 text-[0.86rem] text-muted">
                We will send a six-digit code to this number.
              </p>
            </div>

            <div>
              <label className="label" htmlFor="phone">Mobile number</label>
              <input
                id="phone"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="98765 43210"
                inputMode="tel"
                autoComplete="tel"
                required
                className="input"
              />
              <p className="mt-1.5 text-[0.75rem] text-muted">
                10 digits, or with +91 — either is fine.
              </p>
            </div>

            {error && (
              <p role="alert" className="rounded-lg bg-down/10 px-3 py-2 text-[0.84rem] text-down">
                {error}
              </p>
            )}

            <button type="submit" disabled={busy || phone.length < 10} className="btn-primary w-full">
              {busy ? <Loader2 size={16} className="animate-spin" /> : <>Send code <ArrowRight size={16} /></>}
            </button>

            <p className="text-center text-[0.84rem] text-muted">
              New here?{" "}
              <Link href="/signup" className="font-medium text-ink underline underline-offset-4">
                Create an account
              </Link>
            </p>
          </form>
        ) : (
          <form onSubmit={verify} className="space-y-5">
            <div>
              <h2 className="h3">Enter the code</h2>
              <p className="mt-1 text-[0.86rem] text-muted">
                Sent to {phone}.{" "}
                <button
                  type="button"
                  onClick={() => { setStage("phone"); setOtp(""); setError(null); }}
                  className="underline underline-offset-4"
                >
                  Change number
                </button>
              </p>
            </div>

            {devCode && (
              <p className="rounded-lg border border-dashed border-line bg-panel/50 px-3 py-2 text-[0.82rem]">
                <strong>Demo mode:</strong> your code is{" "}
                <span className="font-mono text-[0.95rem] font-semibold">{devCode}</span>.
                <span className="block text-[0.74rem] text-muted">
                  Shown because the server is on <code>otp.channel: log</code>. Real
                  delivery never returns the code.
                </span>
              </p>
            )}

            <div>
              <label className="label" htmlFor="otp">Six-digit code</label>
              <input
                id="otp"
                value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="••••••"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                className="input text-center font-mono text-[1.3rem] tracking-[0.4em]"
              />
            </div>

            {error && (
              <p role="alert" className="rounded-lg bg-down/10 px-3 py-2 text-[0.84rem] text-down">
                {error}
              </p>
            )}

            <button type="submit" disabled={busy || otp.length < 6} className="btn-primary w-full">
              {busy ? <Loader2 size={16} className="animate-spin" /> : <>Sign in <ArrowRight size={16} /></>}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
