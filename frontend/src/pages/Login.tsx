import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ErrorNote } from "../components/ui";
import Icon from "../components/Icon";
import { errorMessage } from "../lib/api";
import { HOME_BY_ROLE, useAuth } from "../lib/auth";
import { loginIllustration } from "../lib/illustration";

const DEMO_PASSWORD = "Demo@1234";

const DEMO_ACCOUNTS = [
  { label: "Patient", name: "Nimali Fernando", email: "patient@suwapath.lk" },
  { label: "Doctor", name: "Dr. Dileepa Perera", email: "doctor@suwapath.lk" },
  { label: "Guardian", name: "Nimal Fernando", email: "guardian@suwapath.lk" },
  { label: "Hospital admin", name: "Chathurika Bandara", email: "hospital@suwapath.lk" },
];

export default function Login() {
  const { user, login, loading } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);

  if (!loading && user) return <Navigate to={HOME_BY_ROLE[user.role]} replace />;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const account = await login(email, password);
      navigate(HOME_BY_ROLE[account.role]);
    } catch (err) {
      setError(errorMessage(err, "Could not sign in."));
    } finally {
      setBusy(false);
    }
  }

  return (
    /* The whole screen is exactly one viewport tall and never scrolls as a
       page: on desktop the card is capped to the viewport, on mobile the
       artwork flexes and only the bottom sheet scrolls if it has to. */
    <div className="h-[100dvh] overflow-hidden bg-[#f4fbfa] lg:bg-[#f8fafc] flex flex-col lg:items-center lg:justify-center lg:p-6">
      <div className="w-full max-w-[1000px] h-full lg:h-auto lg:max-h-[calc(100dvh-3rem)] bg-transparent lg:bg-white lg:rounded-3xl lg:shadow-[0_8px_30px_rgb(0,0,0,0.04)] overflow-hidden lg:grid lg:grid-cols-2 flex flex-col min-h-0">

        {/* Left panel (Desktop only) */}
        <div className="hidden lg:flex flex-col items-center pt-10 px-10 bg-[#f4fbfa] relative overflow-hidden min-h-0">
          <div className="max-w-md text-center z-10 w-full flex flex-col items-center flex-1 min-h-0">
            <div className="flex flex-col items-center justify-center shrink-0 mb-6">
              <img src="/brand/mark.png" alt="SuwaPath Icon" className="h-[clamp(56px,9vh,92px)] w-auto object-contain mb-4" />
              <h1 className="text-[clamp(24px,3.2vh,32px)] font-bold text-[#0a2e56]">
                SuwaPath <span className="text-[#0a2e56] font-medium mx-1">-</span> <span className="text-[#00a7b3]">සුවපත්</span>
              </h1>
            </div>
            <h2 className="text-[clamp(20px,2.8vh,27px)] font-bold text-[#0a2e56] mb-2 shrink-0">Care. Connect. Empower.</h2>
            <p className="text-ink-600 mb-4 text-[clamp(14px,1.9vh,17px)] shrink-0">
              Your trusted healthcare companion for smarter appointments, better records, and improved wellbeing.
            </p>
            <div className="w-full relative flex justify-center items-end flex-1 min-h-0 pb-6">
              <img
                src={loginIllustration.desktop}
                alt=""
                aria-hidden
                loading="eager"
                className="w-[125%] max-w-none max-h-full object-contain object-bottom mix-blend-multiply"
              />
            </div>
          </div>
        </div>

        {/* Right form panel on desktop; hero + bottom sheet on mobile. */}
        <div className="flex flex-col w-full h-full lg:h-auto min-h-0 overflow-hidden lg:justify-center lg:p-10">
          {/* Mobile hero — takes whatever height the sheet leaves it. */}
          {/* The hero takes a fixed share of the screen rather than "whatever
              the sheet leaves", so a tall sheet (narrow phone, wrapped demo
              labels) can never squeeze the artwork down to a thumbnail. */}
          <div className="lg:hidden w-full text-center flex flex-col items-center shrink-0 h-[32dvh] min-h-0 px-5 pt-[max(0.75rem,env(safe-area-inset-top))] pb-2 [@media(max-height:700px)]:h-auto lg:h-auto">
            <div className="flex flex-col items-center justify-center shrink-0">
              <img src="/brand/mark.png" alt="SuwaPath Icon" className="h-[clamp(44px,7.5vh,76px)] w-auto object-contain mb-2" />
              <h1 className="text-[clamp(18px,2.6vh,24px)] font-bold text-[#0a2e56]">
                SuwaPath <span className="text-[#0a2e56] font-medium mx-1">-</span> <span className="text-[#00a7b3]">සුවපත්</span>
              </h1>
            </div>
            <p className="text-ink-500 mt-1 text-[clamp(11px,1.7vh,14px)] px-2 leading-snug shrink-0 [@media(max-height:700px)]:hidden">
              Your health journey and care, all in one place.
            </p>
            {/* On short phones the artwork would be squeezed down to a
                thumbnail, which looks broken rather than decorative — below
                that height it steps aside so the sheet gets the room. */}
            <div className="flex-1 min-h-0 w-full max-w-[min(88%,420px)] flex items-center justify-center py-2 [@media(max-height:700px)]:hidden">
              <img
                src={loginIllustration.mobile}
                alt=""
                aria-hidden
                loading="eager"
                className="w-full h-full max-h-full object-contain mix-blend-multiply"
              />
            </div>
          </div>

          {/* Bottom sheet on mobile; plain column on desktop. The sheet is the
              only scrollable region, so the page itself never moves. */}
          {/* The sheet runs edge to edge, but its contents stay at a readable
              column width — the side padding grows to absorb the extra space
              on wider phones and tablets. */}
          <div className="w-full mx-auto flex flex-col flex-1 min-h-0 overflow-y-auto overscroll-contain bg-white rounded-t-[28px] shadow-[0_-6px_24px_rgba(10,46,86,0.10)] px-[max(1.25rem,calc((100%-24rem)/2))] pt-5 pb-[max(1rem,env(safe-area-inset-bottom))] lg:max-w-sm lg:flex-none lg:overflow-visible lg:rounded-none lg:shadow-none lg:bg-transparent lg:p-0 lg:my-auto">
            {/* Sheet grab handle, mobile only. */}
            <div className="lg:hidden mx-auto mb-3 h-1 w-10 shrink-0 rounded-full bg-ink-200" aria-hidden />

            <div className="lg:hidden text-center shrink-0">
              <h2 className="text-[22px] font-bold text-[#0a2e56] leading-tight">Welcome back</h2>
              <p className="text-ink-500 mt-0.5 text-[13px]">Sign in to continue to SuwaPath</p>
            </div>

            <div className="hidden lg:block mb-6 text-center lg:text-left">
              <h2 className="text-[clamp(24px,3.6vh,32px)] leading-tight font-bold text-[#0a2e56]">Welcome back</h2>
              <p className="text-ink-500 mt-1.5 text-[15px]">Sign in to continue to SuwaPath</p>
            </div>

            <form
              className="mt-3 lg:mt-0 space-y-3 lg:space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                void submit();
              }}
            >
              <div>
                <label className="sp-field hidden lg:block text-ink-700 font-medium mb-1.5 text-sm" htmlFor="email">Email address</label>
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                    <Icon name="person" size={20} className="text-ink-400" />
                  </div>
                  <input
                    id="email"
                    className="sp-input pl-11 bg-white border-ink-200 focus:border-brand-500 focus:ring-brand-500/20 text-[14px] lg:text-[15px] py-2.5 lg:py-3.5 rounded-xl w-full placeholder:text-ink-400"
                    type="email"
                    placeholder="Email or mobile number"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    autoComplete="username"
                    required
                  />
                </div>
              </div>
              
              <div>
                <label className="sp-field hidden lg:block text-ink-700 font-medium mb-1.5 text-sm" htmlFor="password">Password</label>
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                    <Icon name="lock" size={20} className="text-ink-400" />
                  </div>
                  <input
                    id="password"
                    className="sp-input pl-11 pr-11 bg-white border-ink-200 focus:border-brand-500 focus:ring-brand-500/20 text-[14px] lg:text-[15px] py-2.5 lg:py-3.5 rounded-xl w-full placeholder:text-ink-400"
                    type={showPassword ? "text" : "password"}
                    placeholder="Password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete="current-password"
                    required
                  />
                  <button
                    type="button"
                    className="absolute inset-y-0 right-0 flex items-center pr-4 text-ink-400 hover:text-ink-600 transition-colors"
                    onClick={() => setShowPassword(!showPassword)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                  >
                    <Icon name={showPassword ? "visibilityOff" : "visibility"} size={20} />
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-between mt-3 lg:mt-4 pb-1 lg:pb-1">
                <label className="flex items-center gap-2.5 cursor-pointer group">
                  <input
                    type="checkbox"
                    className="w-4 h-4 text-brand-600 rounded border-ink-300 focus:ring-brand-500/30 transition-shadow cursor-pointer"
                    checked={rememberMe}
                    onChange={(e) => setRememberMe(e.target.checked)}
                  />
                  <span className="text-[14px] lg:text-[15px] text-ink-600 select-none">Remember me</span>
                </label>
                {/* No password-reset flow exists yet, so this is text rather
                    than a link to nowhere. */}
                <span className="text-[14px] lg:text-[15px] text-ink-400">
                  Contact support to reset
                </span>
              </div>

              {error && <ErrorNote message={error} />}
              
              <button className="sp-btn sp-btn-primary sp-btn-block py-2.5 lg:py-3.5 rounded-xl flex justify-center items-center gap-2 font-semibold shadow-md shadow-brand-500/20 text-[14px] lg:text-[15px]" disabled={busy}>
                {busy ? "Signing in…" : "Sign In"}
                {!busy && <Icon name="arrowRight" size={18} />}
              </button>
            </form>

            <div className="mt-4 lg:mt-5 flex items-center justify-center shrink-0">
              <div className="h-px bg-ink-100 flex-1"></div>
              <span className="px-4 text-[12px] lg:text-[13px] text-ink-500 font-medium lowercase">or</span>
              <div className="h-px bg-ink-100 flex-1"></div>
            </div>

            {/* Demo accounts.
                Every record in this deployment is synthetic and these
                credentials are already published in the README, so putting
                them on screen costs nothing and saves a reviewer from having
                to go looking. Clicking fills the form rather than signing in,
                so the normal login path is still the one being exercised. */}
            <div className="mt-4 lg:mt-4 shrink-0">
              <p className="text-[12px] lg:text-[13px] text-ink-500 text-center mb-2">
                Explore with a demo account — synthetic data
              </p>
              <div className="grid grid-cols-2 gap-2">
                {DEMO_ACCOUNTS.map((account) => (
                  <button
                    key={account.email}
                    type="button"
                    onClick={() => {
                      setEmail(account.email);
                      setPassword(DEMO_PASSWORD);
                      setError(null);
                    }}
                    className="rounded-xl border border-ink-200 px-3 py-2 text-left transition hover:bg-ink-50"
                  >
                    <span className="block text-[13px] font-semibold text-ink-800">
                      {account.label}
                    </span>
                    <span className="block text-[11px] text-ink-500">
                      {account.name}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            <p className="mt-4 lg:mt-5 text-center text-[12px] lg:text-[14px] text-ink-600 shrink-0">
              Sign-up is not open in this preview — use a demo account above.
            </p>

            {/* Mobile security note */}
            <div className="lg:hidden mt-3 flex shrink-0 items-center justify-center gap-1.5 text-[11px] text-ink-500">
              <Icon name="verified" size={14} className="text-brand-600" />
              <span className="font-medium">Your health data is secure and private.</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
