/* ==========================================================================
   Responsive application shell.
   --------------------------------------------------------------------------
   Desktop (>=1024px): persistent sidebar + top bar.
   Tablet/mobile:      top bar + slide-over drawer for the full menu, plus a
                       bottom tab bar holding the 4-5 destinations that role
                       actually uses day to day.

   Mobile is not a shrunken desktop dashboard (spec §28): each role gets its
   own tab set, and the drawer carries the long tail.
   ========================================================================== */

import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import Icon, { type IconName } from "./Icon";
import { Avatar, Brand } from "./ui";
import { api } from "../lib/api";
import { useAuth, type Role } from "../lib/auth";
import ChatFab from "./ChatFab";

interface NavItem {
  to: string;
  label: string;
  /** Shorter label for the cramped bottom tab bar. */
  short?: string;
  icon: IconName;
  section?: string;
  /** Include in the mobile bottom tab bar. */
  tab?: boolean;
  end?: boolean;
}

const NAV: Record<Role, NavItem[]> = {
  patient: [
    { to: "/patient", label: "Dashboard", short: "Home", icon: "home", tab: true, end: true },
    { to: "/patient/appointments", label: "Appointments", short: "Visits", icon: "calendar", tab: true },
    { to: "/patient/find-care", label: "Doctors & Hospitals", icon: "hospital", section: "Find care" },
    { to: "/patient/reports", label: "Medical Reports", icon: "description", section: "Find care" },
    { to: "/patient/imaging", label: "Image Screening", icon: "scan", section: "Find care" },
    { to: "/patient/programmes", label: "Care Programmes", short: "Care", icon: "favorite", section: "Continuity", tab: true },
    { to: "/patient/history", label: "Medical History", icon: "history", section: "Continuity" },
    { to: "/patient/sharing", label: "Sharing & Consent", icon: "lock", section: "Account" },
    { to: "/patient/notifications", label: "Notifications", short: "Alerts", icon: "notifications", section: "Account", tab: true },
  ],
  guardian: [
    { to: "/guardian", label: "My Dependents", short: "Family", icon: "group", tab: true, end: true },
    { to: "/guardian/alerts", label: "Alerts", icon: "warning", tab: true },
    { to: "/guardian/notifications", label: "Notifications", short: "Inbox", icon: "notifications", tab: true },
  ],
  doctor: [
    { to: "/doctor", label: "Dashboard", short: "Home", icon: "dashboard", tab: true, end: true },
    { to: "/doctor/queue", label: "Patient Queue", short: "Queue", icon: "queue", section: "Clinical", tab: true },
    { to: "/doctor/appointments", label: "Appointments", short: "Visits", icon: "calendar", section: "Clinical", tab: true },
    { to: "/doctor/patients", label: "My Patients", short: "Patients", icon: "patients", section: "Clinical", tab: true },
    { to: "/doctor/notifications", label: "Notifications", icon: "notifications", section: "Account" },
  ],
  hospital_admin: [
    { to: "/hospital", label: "Dashboard", short: "Overview", icon: "analytics", section: "Operations", tab: true, end: true },
    { to: "/hospital/forecast", label: "Demand Forecast", short: "Forecast", icon: "forecast", section: "Operations", tab: true },
    { to: "/hospital/no-show", label: "No-show Risk", short: "Risk", icon: "warning", section: "Operations", tab: true },
    { to: "/hospital/capacity", label: "Capacity", icon: "capacity", section: "Operations", tab: true },
    { to: "/hospital/providers", label: "Providers", icon: "stethoscope", section: "Administration" },
    { to: "/hospital/notifications", label: "Notifications", icon: "notifications", section: "Administration" },
  ],
  system_admin: [
    { to: "/admin", label: "Overview", short: "Overview", icon: "analytics", tab: true, end: true },
    { to: "/admin/users", label: "Users & Roles", short: "Users", icon: "group", section: "Management", tab: true },
    { to: "/admin/providers", label: "Provider Verification", short: "Verify", icon: "verified", section: "Management", tab: true },
    { to: "/admin/facilities", label: "Facilities", icon: "facilities", section: "Management" },
    { to: "/admin/ai", label: "AI Configuration", short: "AI", icon: "ai", section: "Platform", tab: true },
    { to: "/admin/audit", label: "Audit Log", icon: "audit", section: "Platform" },
    { to: "/admin/notifications", label: "Notifications", icon: "notifications", section: "Platform" },
  ],
};

const ROLE_LABEL: Record<Role, string> = {
  patient: "Patient",
  guardian: "Guardian",
  doctor: "Doctor",
  hospital_admin: "Hospital Admin",
  system_admin: "System Admin",
};

/**
 * Where the top-bar search sends each role, and what it searches.
 *
 * Only roles whose destination actually filters on `?q=` appear here. A
 * search box that leads nowhere is worse than no search box — the same
 * mistake as a button wired to an empty handler — so guardian and system
 * admin are deliberately absent: a guardian has a handful of dependents on
 * one screen, and the admin user list carries its own filter.
 */
const SEARCH_TARGET: Partial<Record<Role, { to: string; placeholder: string }>> = {
  patient: {
    to: "/patient/find-care",
    placeholder: "Search doctors, hospitals, specialties…",
  },
  doctor: {
    to: "/doctor/patients",
    placeholder: "Search your patients…",
  },
  hospital_admin: {
    to: "/hospital/providers",
    placeholder: "Search providers and specialties…",
  },
};

const ROLE_HOME: Record<Role, string> = {
  patient: "/patient",
  guardian: "/guardian",
  doctor: "/doctor",
  hospital_admin: "/hospital",
  system_admin: "/admin",
};

export default function AppShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [unread, setUnread] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const [search, setSearch] = useState("");

  useEffect(() => {
    api
      .get("/notifications", { params: { limit: 1 } })
      .then((response) => setUnread(response.data.unread_count))
      .catch(() => undefined);
  }, [location.pathname]);

  // Close the drawer & plus menu whenever navigation happens.
  useEffect(() => {
    setDrawerOpen(false);
    setPlusMenuOpen(false);
  }, [location.pathname]);

  // Prevent the page behind the drawer from scrolling.
  useEffect(() => {
    document.body.style.overflow = drawerOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [drawerOpen]);

  if (!user) return null;

  const items = NAV[user.role];
  const sections = [...new Set(items.map((item) => item.section ?? ""))];
  const home = ROLE_HOME[user.role];
  // Routes that manage their own viewport rather than sitting in the
  // padded document column.
  const fullBleed = location.pathname.endsWith("/assistant");

  const menu = (
    <nav className="flex flex-col gap-0.5" aria-label="Main">
      {sections.map((section) => (
        <div key={section}>
          {section && <p className="sp-nav-section">{section}</p>}
          {items
            .filter((item) => (item.section ?? "") === section)
            .map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `sp-nav-link ${isActive ? "sp-nav-link-active" : ""}`
                }
              >
                <Icon name={item.icon} size={19} />
                <span className="truncate">{item.label}</span>
                {item.icon === "notifications" && unread > 0 && (
                  <span className="ml-auto sp-chip sp-chip-danger !px-1.5 !py-0.5">
                    {unread > 99 ? "99+" : unread}
                  </span>
                )}
              </NavLink>
            ))}
        </div>
      ))}
    </nav>
  );

  return (
    <div className={`flex w-full ${fullBleed ? "h-screen overflow-hidden" : "min-h-screen"}`}>
      {/* ------------------------------------------------ desktop sidebar */}
      {!fullBleed && (
        <aside className="hidden lg:flex w-sidebar shrink-0 flex-col border-r border-line bg-surface lg:sticky lg:top-0 lg:h-screen">
          {/* Same height as the topbar, so the two bottom borders form one
              continuous line across the page. `p-4` around a 3rem logo made
              this block 5rem tall against the topbar's 3.75rem, which read as
              the header being misaligned with the sidebar. */}
          <div className="flex h-topbar items-center px-4 border-b border-line">
            <Link to={home} aria-label="SuwaPath home">
              <Brand />
            </Link>
          </div>
          <div className="flex-1 overflow-y-auto p-3">{menu}</div>
          <div className="p-3">
            <div className="rounded-lg bg-ink-50 p-4">
              <p className="text-sm font-semibold text-ink-800">Need help?</p>
              <p className="text-xs text-ink-500 mt-0.5">Contact SuwaPath Care</p>
              <a
                href="tel:0112123456"
                className="mt-1.5 inline-flex items-center gap-1.5 text-brand-700 font-bold"
              >
                <Icon name="phone" size={15} />
                0112 123 456
              </a>
            </div>
          </div>
        </aside>
      )}

      {/* ---------------------------------------------------- mobile drawer */}
      {drawerOpen && (
        <div className="lg:hidden fixed inset-0 z-50 flex">
          <button
            className="absolute inset-0 bg-ink-900/45"
            onClick={() => setDrawerOpen(false)}
            aria-label="Close menu"
          />
          <aside className="relative w-72 max-w-[85vw] bg-surface flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-line">
              <Brand />
              <button
                className="sp-btn sp-btn-ghost sp-btn-sm !px-2"
                onClick={() => setDrawerOpen(false)}
              >
                <Icon name="close" size={20} label="Close menu" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3">{menu}</div>
            {/* The help number was desktop-only, which had it backwards: the
                phone is the device someone actually calls from. */}
            <div className="px-3 pb-3">
              <div className="rounded-lg bg-ink-50 p-3.5">
                <p className="text-sm font-semibold text-ink-800">Need help?</p>
                <p className="text-xs text-ink-500 mt-0.5">Contact SuwaPath Care</p>
                <a
                  href="tel:0112123456"
                  className="mt-1.5 inline-flex items-center gap-1.5 text-brand-700 font-bold"
                >
                  <Icon name="phone" size={15} />
                  0112 123 456
                </a>
              </div>
            </div>
            <div className="p-3 border-t border-line">
              <button
                className="sp-btn sp-btn-danger sp-btn-block"
                onClick={() => {
                  logout();
                  navigate("/login");
                }}
              >
                <Icon name="logoutDoor" size={17} />
                Sign out
              </button>
            </div>
          </aside>
        </div>
      )}

      {/* ------------------------------------------------------- main area */}
      <div className="flex-1 min-w-0 w-full flex flex-col min-h-0 overflow-x-hidden">
        {/* Translucent rather than near-opaque, so content scrolling beneath
            reads through the bar instead of disappearing under a flat panel.
            supports-[]: keeps it solid where backdrop-filter is unavailable —
            otherwise the bar would go see-through with nothing blurring it. */}
        {!fullBleed && (
          <header className="fixed top-0 right-0 left-0 lg:left-64 z-30 h-topbar border-b border-line bg-surface/95 backdrop-blur-xl supports-[backdrop-filter]:bg-surface/70 shadow-sm">
            <div className="flex items-center gap-2 px-3 sm:px-4 lg:px-8 h-topbar">
              {/* Mobile only. On large screens the sidebar already carries the
                  wordmark, and showing the mark here too puts two logos on one
                  row. */}
              <Link to={home} className="lg:hidden" aria-label="SuwaPath home">
                <Brand variant="mark" />
              </Link>

              {SEARCH_TARGET[user.role] && (
                <form
                  role="search"
                  className="hidden md:flex items-center gap-2 flex-1 max-w-md"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const target = SEARCH_TARGET[user.role]!;
                    const q = search.trim();
                    navigate(
                      q ? `${target.to}?q=${encodeURIComponent(q)}` : target.to,
                    );
                  }}
                >
                  <div className="relative w-full">
                    <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-400">
                      <Icon name="search" size={17} />
                    </span>
                    <input
                      type="search"
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      placeholder={SEARCH_TARGET[user.role]!.placeholder}
                      aria-label={SEARCH_TARGET[user.role]!.placeholder}
                      className="sp-input !h-9 !pl-9 bg-surface/80"
                    />
                  </div>
                </form>
              )}

              <div className="ml-auto flex items-center gap-1 sm:gap-2">
                <Link
                  to={`${home}/notifications`}
                  className="relative sp-btn sp-btn-ghost !px-2"
                >
                  <Icon name="notifications" size={20} label="Notifications" />
                  {unread > 0 && (
                    <span className="absolute top-0.5 right-0.5 min-w-[17px] h-[17px] rounded-full bg-danger-text text-white text-[10px] font-bold grid place-items-center px-1">
                      {unread > 99 ? "99+" : unread}
                    </span>
                  )}
                </Link>

                <div className="relative">
                  <button
                    onClick={() => setProfileMenuOpen(!profileMenuOpen)}
                    className="flex items-center gap-2.5 sm:pl-3 sm:border-l border-line text-left hover:bg-ink-50 p-1.5 -my-1.5 rounded-lg transition"
                  >
                    <Avatar name={user.full_name} size={34} />
                    <div className="hidden sm:block leading-tight pr-2">
                      <p className="text-sm font-semibold text-ink-900">
                        {user.full_name}
                      </p>
                      <p className="text-[11px] text-ink-500">
                        {ROLE_LABEL[user.role]}
                      </p>
                    </div>
                    <Icon name="chevronDown" size={18} className="text-ink-400 hidden sm:block" />
                  </button>

                  {profileMenuOpen && (
                    <>
                      <div 
                        className="fixed inset-0 z-40" 
                        onClick={() => setProfileMenuOpen(false)}
                      />
                      <div className="absolute right-0 mt-2 w-56 bg-surface border border-line rounded-xl shadow-xl py-1.5 z-50">
                        <Link 
                          to={`${home}/profile`} 
                          className="w-full text-left px-4 py-2.5 text-sm text-ink-700 hover:bg-ink-50 flex items-center gap-3 transition"
                          onClick={() => setProfileMenuOpen(false)}
                        >
                          <Icon name="person" size={18} />
                          My Profile
                        </Link>
                        <Link 
                          to={`${home}/settings`} 
                          className="w-full text-left px-4 py-2.5 text-sm text-ink-700 hover:bg-ink-50 flex items-center gap-3 transition"
                          onClick={() => setProfileMenuOpen(false)}
                        >
                          <Icon name="settings" size={18} />
                          Settings
                        </Link>
                        <div className="h-px bg-line my-1.5" />
                        <button
                          onClick={() => {
                            setProfileMenuOpen(false);
                            logout();
                            navigate("/login");
                          }}
                          className="w-full text-left px-4 py-2.5 text-sm text-danger-text hover:bg-danger-surface flex items-center gap-3 font-medium transition"
                        >
                          <Icon name="logoutDoor" size={18} />
                          Sign out
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>
          </header>
        )}

        {/* Most pages are documents: padded, centred, scrolling as a page.
            The assistant is an application surface — it owns the viewport,
            manages its own scrolling, and would look wrong boxed inside a
            centred column. `full-bleed` routes opt out of both. */}
        <main
          className={
            fullBleed
              ? "flex-1 min-h-0 w-full h-screen overflow-hidden"
              : "flex-1 min-w-0 p-3 sm:p-4 lg:p-8 pt-[calc(var(--sp-topbar-h)+0.75rem)] sm:pt-[calc(var(--sp-topbar-h)+1rem)] pb-[calc(var(--sp-bottomnav-h)+1rem)] lg:pb-8 max-w-[1600px] w-full mx-auto"
          }
        >
          <Outlet />
        </main>
      </div>

      {/* ── Mobile Plus (+) Quick Action Sheet ── */}
      {plusMenuOpen && (
        <>
          <div
            className="fixed inset-0 z-[50] bg-ink-900/20 transition-opacity"
            onClick={() => setPlusMenuOpen(false)}
            aria-hidden="true"
          />
          <div
            className="
              fixed z-[60]
              bottom-[calc(var(--sp-bottomnav-h)+0.75rem)] inset-x-3
              sm:inset-x-auto sm:left-1/2 sm:-translate-x-1/2 sm:w-[24rem]
              rounded-3xl border border-line bg-surface p-5
              shadow-[0_20px_60px_rgba(0,0,0,0.22)] ring-1 ring-ink-900/10
              animate-[fabSlideUp_250ms_cubic-bezier(0.16,1,0.3,1)]
            "
          >
            <div className="flex items-center justify-between pb-3 border-b border-line">
              <h2 className="text-xs font-bold text-ink-700 uppercase tracking-wider">Quick Actions</h2>
              <button
                onClick={() => setPlusMenuOpen(false)}
                className="grid h-7 w-7 place-items-center rounded-full bg-ink-100 text-ink-600 hover:bg-ink-200"
              >
                <Icon name="close" size={16} />
              </button>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-y-5 gap-x-3">
              {/* 1. AI Assistant */}
              <button
                onClick={() => {
                  setPlusMenuOpen(false);
                  window.dispatchEvent(new CustomEvent("toggle-chat-fab"));
                }}
                className="flex flex-col items-center text-center gap-1.5 transition active:scale-95 group"
              >
                <span className="grid h-14 w-14 place-items-center rounded-2xl bg-brand-600 text-white shadow-md shadow-brand-600/20 group-hover:scale-105 transition-transform">
                  <Icon name="ai" size={26} />
                </span>
                <span className="text-xs font-medium text-ink-800 leading-snug">AI Assistant</span>
              </button>

              {/* 2. Image Screening */}
              <Link
                to="/patient/imaging"
                onClick={() => setPlusMenuOpen(false)}
                className="flex flex-col items-center text-center gap-1.5 transition active:scale-95 group"
              >
                <span className="grid h-14 w-14 place-items-center rounded-2xl bg-purple-50 text-purple-600 border border-purple-100 shadow-sm group-hover:scale-105 transition-transform">
                  <Icon name="scan" size={26} />
                </span>
                <span className="text-xs font-medium text-ink-800 leading-snug">Image Screening</span>
              </Link>

              {/* 3. Care Programmes */}
              <Link
                to="/patient/programmes"
                onClick={() => setPlusMenuOpen(false)}
                className="flex flex-col items-center text-center gap-1.5 transition active:scale-95 group"
              >
                <span className="grid h-14 w-14 place-items-center rounded-2xl bg-pink-50 text-pink-600 border border-pink-100 shadow-sm group-hover:scale-105 transition-transform">
                  <Icon name="favorite" size={26} />
                </span>
                <span className="text-xs font-medium text-ink-800 leading-snug">Care Programmes</span>
              </Link>

              {/* 4. Medical History */}
              <Link
                to="/patient/history"
                onClick={() => setPlusMenuOpen(false)}
                className="flex flex-col items-center text-center gap-1.5 transition active:scale-95 group"
              >
                <span className="grid h-14 w-14 place-items-center rounded-2xl bg-amber-50 text-amber-600 border border-amber-100 shadow-sm group-hover:scale-105 transition-transform">
                  <Icon name="history" size={26} />
                </span>
                <span className="text-xs font-medium text-ink-800 leading-snug">Medical History</span>
              </Link>

              {/* 5. Sharing & Consent */}
              <Link
                to="/patient/sharing"
                onClick={() => setPlusMenuOpen(false)}
                className="flex flex-col items-center text-center gap-1.5 transition active:scale-95 group"
              >
                <span className="grid h-14 w-14 place-items-center rounded-2xl bg-emerald-50 text-emerald-600 border border-emerald-100 shadow-sm group-hover:scale-105 transition-transform">
                  <Icon name="lock" size={26} />
                </span>
                <span className="text-xs font-medium text-ink-800 leading-snug">Sharing & Consent</span>
              </Link>

              {/* 6. Medical Reports */}
              <Link
                to="/patient/reports"
                onClick={() => setPlusMenuOpen(false)}
                className="flex flex-col items-center text-center gap-1.5 transition active:scale-95 group"
              >
                <span className="grid h-14 w-14 place-items-center rounded-2xl bg-blue-50 text-blue-600 border border-blue-100 shadow-sm group-hover:scale-105 transition-transform">
                  <Icon name="description" size={26} />
                </span>
                <span className="text-xs font-medium text-ink-800 leading-snug">Medical Reports</span>
              </Link>
            </div>
          </div>
        </>
      )}

      {/* --------------------------------------------- mobile bottom tabs (5 sections) */}
      {!fullBleed && (
        <nav className="sp-tabbar lg:hidden" aria-label="Primary">
          {/* Tab 1: Home */}
          <NavLink
            to="/patient"
            end
            className={({ isActive }) => `sp-tab ${isActive ? "sp-tab-active" : ""}`}
          >
            <Icon name="home" size={22} />
            <span className="truncate w-full text-center">Home</span>
          </NavLink>

          {/* Tab 2: Visits */}
          <NavLink
            to="/patient/appointments"
            className={({ isActive }) => `sp-tab ${isActive ? "sp-tab-active" : ""}`}
          >
            <Icon name="calendar" size={22} />
            <span className="truncate w-full text-center">Visits</span>
          </NavLink>

          {/* Tab 3 (CENTER): Plus (+) Quick Action Menu */}
          <button
            type="button"
            onClick={() => setPlusMenuOpen(!plusMenuOpen)}
            className="sp-tab flex flex-col items-center justify-center shrink-0 min-w-0 px-1"
            title="More Options & Actions"
          >
            <span className="grid h-12 w-12 sm:h-13 sm:w-13 place-items-center rounded-full bg-brand-600 text-white shadow-xl shadow-brand-600/40 border-2 border-surface -mt-4 transition active:scale-95">
              <Icon
                name="add"
                size={28}
                className={`transition-transform duration-[1800ms] ease-in-out ${
                  plusMenuOpen ? "rotate-[360deg]" : "rotate-0"
                }`}
              />
            </span>
            <span className="truncate w-full text-center text-[11px] font-bold text-brand-700 mt-0.5">
              More
            </span>
          </button>

          {/* Tab 4: Find Care */}
          <NavLink
            to="/patient/find-care"
            className={({ isActive }) => `sp-tab ${isActive ? "sp-tab-active" : ""}`}
          >
            <Icon name="hospital" size={22} />
            <span className="truncate w-full text-center">Find Care</span>
          </NavLink>

          {/* Tab 5: Reports */}
          <NavLink
            to="/patient/reports"
            className={({ isActive }) => `sp-tab ${isActive ? "sp-tab-active" : ""}`}
          >
            <Icon name="description" size={22} />
            <span className="truncate w-full text-center">Reports</span>
          </NavLink>
        </nav>
      )}

      {/* Floating chat assistant shortcut — visible on every page except
          the Assistant itself. The component checks role and route. */}
      <ChatFab />
    </div>
  );
}
