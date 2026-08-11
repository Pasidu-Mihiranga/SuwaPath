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
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const [search, setSearch] = useState("");

  useEffect(() => {
    api
      .get("/notifications", { params: { limit: 1 } })
      .then((response) => setUnread(response.data.unread_count))
      .catch(() => undefined);
  }, [location.pathname]);

  // Close the drawer whenever navigation happens.
  useEffect(() => setDrawerOpen(false), [location.pathname]);

  // Prevent the page behind the drawer from scrolling.
  useEffect(() => {
    document.body.style.overflow = drawerOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [drawerOpen]);

  if (!user) return null;

  const items = NAV[user.role];
  const tabs = items.filter((item) => item.tab).slice(0, 5);
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
    <div className={`flex ${fullBleed ? "h-screen w-screen overflow-hidden" : "min-h-screen"}`}>
      {/* ------------------------------------------------ desktop sidebar */}
      {!fullBleed && (
        <aside className="hidden lg:flex w-sidebar shrink-0 flex-col border-r border-line bg-surface lg:sticky lg:top-0 lg:h-screen">
          <div className="p-4 border-b border-line">
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
      <div className="flex-1 min-w-0 flex flex-col min-h-0">
        {/* Translucent rather than near-opaque, so content scrolling beneath
            reads through the bar instead of disappearing under a flat panel.
            supports-[]: keeps it solid where backdrop-filter is unavailable —
            otherwise the bar would go see-through with nothing blurring it. */}
        {!fullBleed && (
          <header className="sticky top-0 z-30 border-b border-line bg-surface/95 backdrop-blur-xl supports-[backdrop-filter]:bg-surface/70">
            <div className="flex items-center gap-2 px-3 sm:px-4 lg:px-8 h-topbar">
              <button
                className="lg:hidden sp-btn sp-btn-ghost !px-2"
                onClick={() => setDrawerOpen(true)}
              >
                <Icon name="menu" size={22} label="Open menu" />
              </button>
              <Link to={home} className="lg:hidden" aria-label="SuwaPath home">
                <Brand variant="mark" />
              </Link>

              {user.role === "patient" && (
                <form
                  role="search"
                  className="hidden md:flex items-center gap-2 flex-1 max-w-md"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const q = search.trim();
                    navigate(
                      q
                        ? `/patient/find-care?q=${encodeURIComponent(q)}`
                        : "/patient/find-care",
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
                      placeholder="Search doctors, hospitals, specialties…"
                      aria-label="Search care providers"
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
              : "flex-1 min-w-0 p-3 sm:p-4 lg:p-8 pb-[calc(var(--sp-bottomnav-h)+1rem)] lg:pb-8 max-w-[1600px] w-full mx-auto"
          }
        >
          <Outlet />
        </main>
      </div>

      {/* --------------------------------------------- mobile bottom tabs */}
      {!fullBleed && (
        <nav className="sp-tabbar lg:hidden" aria-label="Primary">
          {tabs.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              className={({ isActive }) =>
                `sp-tab ${isActive ? "sp-tab-active" : ""}`
              }
            >
              <span className="relative">
                <Icon name={tab.icon} size={22} />
                {tab.icon === "notifications" && unread > 0 && (
                  <span className="absolute -top-1 -right-1.5 min-w-[15px] h-[15px] rounded-full bg-danger-text text-white text-[9px] font-bold grid place-items-center px-1">
                    {unread > 9 ? "9+" : unread}
                  </span>
                )}
              </span>
              {tab.short ?? tab.label}
            </NavLink>
          ))}
        </nav>
      )}

      {/* Floating chat assistant shortcut — visible on every page except
          the Assistant itself. The component checks role and route. */}
      <ChatFab />
    </div>
  );
}
