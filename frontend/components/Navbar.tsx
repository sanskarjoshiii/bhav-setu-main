"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ChevronDown, HelpCircle, Menu, Search, Sprout, X } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { t } from "@/lib/i18n";
import { cx } from "@/lib/format";

const LINKS = [
  { href: "/", key: "nav_home" as const },
  { href: "/dashboard", key: "nav_dashboard" as const },
  { href: "/advisor", key: "nav_advisor" as const },
  { href: "/compare", key: "nav_compare" as const },
  { href: "/irrigation", key: "nav_irrigation" as const },
  { href: "/community", key: "nav_community" as const },
  { href: "/history", key: "nav_history" as const },
  { href: "/chat", key: "nav_chat" as const },
];

export default function Navbar() {
  const pathname = usePathname();
  const { user, language, setLanguage, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [userOpen, setUserOpen] = useState(false);
  const [langOpen, setLangOpen] = useState(false);

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-cream/90 backdrop-blur">
      {/* Wide shell: the nav needs more room than the page content */}
      <div className="mx-auto flex h-[70px] w-full max-w-[1560px] items-center gap-5 px-5 sm:px-7">
        <Link href="/" className="flex shrink-0 items-center gap-2.5">
          <span className="grid h-9 w-9 place-items-center rounded-full bg-ink text-cream">
            <Sprout size={18} />
          </span>
          <span className="text-[1.12rem] font-bold tracking-[-0.02em]">
            {t("brand", language)}
          </span>
        </Link>

        <label className="relative hidden w-[200px] shrink-0 items-center 2xl:flex">
          <Search size={15} className="pointer-events-none absolute left-3.5 text-muted" />
          <input
            placeholder="Search crop or mandi…"
            className="w-full rounded-full border border-line bg-panel/60 py-2 pl-10 pr-4 text-[0.84rem] outline-none placeholder:text-muted focus:border-ink/30"
          />
        </label>

        <nav className="hidden flex-1 items-center justify-center gap-0.5 xl:flex">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={cx("navlink whitespace-nowrap", isActive(l.href) && "navlink-active")}
            >
              {t(l.key, language)}
            </Link>
          ))}
        </nav>

        <div className="ml-auto flex shrink-0 items-center gap-2 xl:ml-0">
          <Link
            href="/help"
            aria-label="Help"
            className="hidden h-9 w-9 place-items-center rounded-full border border-line text-muted transition hover:text-ink 2xl:grid"
          >
            <HelpCircle size={17} />
          </Link>

          <div className="relative hidden sm:block">
            <button
              onClick={() => setLangOpen((v) => !v)}
              className="flex h-9 items-center gap-1 rounded-full border border-line px-3 text-[0.8rem] font-medium"
            >
              {language.toUpperCase()}
              <ChevronDown size={13} />
            </button>
            {langOpen && (
              <div className="absolute right-0 mt-2 w-32 overflow-hidden rounded-xl border border-line bg-card shadow-pop">
                {(["en", "mr"] as const).map((code) => (
                  <button
                    key={code}
                    onClick={() => {
                      setLanguage(code);
                      setLangOpen(false);
                    }}
                    className="block w-full px-4 py-2.5 text-left text-[0.85rem] hover:bg-panel"
                  >
                    {code === "en" ? "English" : "मराठी"}
                  </button>
                ))}
              </div>
            )}
          </div>

          {user ? (
            <div className="relative">
              <button
                onClick={() => setUserOpen((v) => !v)}
                className="flex h-9 items-center gap-2 rounded-full border border-line pl-1.5 pr-3"
              >
                <span className="grid h-6 w-6 place-items-center rounded-full bg-panel text-[0.62rem] font-bold">
                  {user.name.slice(0, 1)}
                </span>
                <span className="hidden max-w-[120px] truncate text-[0.85rem] font-medium 2xl:inline">
                  {user.name}
                </span>
                <ChevronDown size={13} />
              </button>
              {userOpen && (
                <div className="absolute right-0 mt-2 w-56 overflow-hidden rounded-xl border border-line bg-card shadow-pop">
                  <div className="border-b border-line px-4 py-3">
                    <p className="text-[0.85rem] font-semibold">{user.name}</p>
                    <p className="text-[0.75rem] text-muted">
                      {user.village} · {user.phone}
                    </p>
                  </div>
                  {[
                    ["My lots", "/lots"],
                    ["Search history", "/history"],
                    ["My sale reports", "/reports"],
                    ["Transparency scores", "/transparency"],
                  ].map(([label, href]) => (
                    <Link
                      key={href}
                      href={href}
                      onClick={() => setUserOpen(false)}
                      className="block px-4 py-2.5 text-[0.85rem] hover:bg-panel"
                    >
                      {label}
                    </Link>
                  ))}
                  <button
                    onClick={() => {
                      logout();
                      setUserOpen(false);
                    }}
                    className="block w-full border-t border-line px-4 py-2.5 text-left text-[0.85rem] text-down hover:bg-panel"
                  >
                    {t("logout", language)}
                  </button>
                </div>
              )}
            </div>
          ) : (
            <>
              <Link href="/login" className="btn-quiet hidden sm:inline-flex">
                {t("login", language)}
              </Link>
              <Link href="/signup" className="btn-primary">
                {t("signup", language)}
              </Link>
            </>
          )}

          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="grid h-9 w-9 place-items-center rounded-full border border-line xl:hidden"
            aria-label="Menu"
          >
            {menuOpen ? <X size={17} /> : <Menu size={17} />}
          </button>
        </div>
      </div>

      {menuOpen && (
        <div className="border-t border-line bg-cream xl:hidden">
          <div className="mx-auto grid w-full max-w-[1560px] gap-1 px-5 py-3 sm:px-7">
            {LINKS.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                onClick={() => setMenuOpen(false)}
                className={cx(
                  "rounded-xl px-4 py-2.5 text-[0.95rem] font-medium",
                  isActive(l.href) ? "bg-ink text-cream" : "text-ink/75"
                )}
              >
                {t(l.key, language)}
              </Link>
            ))}
          </div>
        </div>
      )}
    </header>
  );
}
