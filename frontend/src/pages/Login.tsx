import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { Brand, ErrorNote } from "../components/ui";
import Icon from "../components/Icon";
import { errorMessage } from "../lib/api";
import { HOME_BY_ROLE, useAuth } from "../lib/auth";

export default function Login() {
  const { user, login, loading } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    <div className="min-h-screen grid lg:grid-cols-2">
      {/* Brand panel */}
      <div className="hidden lg:flex flex-col justify-between sp-gradient-brand p-12">
        <Brand className="h-10 brightness-0 invert" />
        <div>
          <h1 className="text-4xl font-bold leading-tight">
            From symptoms to the right care.
          </h1>
          <p className="mt-4 text-brand-50 text-lg max-w-md">
            A multilingual, multimodal platform connecting patients, verified
            providers and hospital operations.
          </p>
          <ul className="mt-8 space-y-3 text-brand-50">
            {[
              "AI symptom intake in English, Sinhala and Tamil",
              "Clinician-defined red-flag detection",
              "Capability-aware doctor and facility matching",
              "Medical report and image understanding",
            ].map((item) => (
              <li key={item} className="flex items-center gap-3">
                <span className="h-1.5 w-1.5 rounded-full bg-brand-200" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* Form panel */}
      <div className="flex items-center justify-center p-6 lg:p-12 bg-white">
        <div className="w-full max-w-md">
          <div className="lg:hidden mb-8">
            <Brand />
          </div>
          <h2 className="text-2xl font-bold text-ink-900">Welcome back</h2>
          <p className="text-ink-500 mt-1">Sign in to continue to SuwaPath.</p>

          <form
            className="mt-6 space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <div>
              <label className="sp-field" htmlFor="email">Email</label>
              <input
                id="email"
                className="sp-input"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="username"
                required
              />
            </div>
            <div>
              <label className="sp-field" htmlFor="password">Password</label>
              <div className="relative">
                <input
                  id="password"
                  className="sp-input pr-10"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  required
                />
                <button
                  type="button"
                  className="absolute inset-y-0 right-0 flex items-center pr-3 text-ink-500 hover:text-ink-700"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  <Icon name={showPassword ? "visibilityOff" : "visibility"} size={20} />
                </button>
              </div>
            </div>
            <ErrorNote message={error} />
            <button className="sp-btn sp-btn-primary w-full" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>



          <p className="mt-6 text-center text-sm text-ink-500">
            Need a private, anonymous conversation?{" "}
            <Link to="/private" className="text-brand-700 font-semibold hover:underline">
              Continue privately
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
