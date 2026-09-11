import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate, Link } from "react-router-dom";
import { Radar, LogIn, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { api, apiError } from "@/lib/api";

const DEMO = [
  { role: "analyst", email: "analyst@sentinelmar.demo", scope: "ingest · correlate · review" },
  { role: "supervisor", email: "supervisor@sentinelmar.demo", scope: "+ acknowledge alerts · override cases" },
];
const DEMO_PASSWORDS = {}; // never ship passwords in the bundle — demo buttons only pre-fill the e-mail

export default function Login() {
  const { user, login } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);
  const [caps, setCaps] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => { api.get("/auth/capabilities").then((r) => setCaps(r.data)).catch(() => setCaps(null)); }, []);
  const googleReady = caps?.authentication?.google?.enabled === true;
  const showDemo = caps?.demo_mode === true;

  if (user) return <Navigate to={loc.state?.from || "/"} replace />;

  const googleSignIn = () => {
    if (googleBusy) return;
    setGoogleBusy(true);
    // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
    const redirectUrl = window.location.origin + "/";
    window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
  };

  const submit = async (e) => {
    e?.preventDefault();
    setBusy(true); setError("");
    try {
      const u = await login(email, password);
      toast.success(`Signed in as ${u.name} (${u.role})`);
      nav(loc.state?.from || "/", { replace: true });
    } catch (err) { setError(apiError(err)); } finally { setBusy(false); }
  };

  return (
    <div className="grid h-screen grid-cols-1 lg:grid-cols-[1.1fr_1fr]" style={{ background: "var(--bg-primary)" }} data-testid="login-page">
      <div className="hidden lg:flex flex-col justify-between p-12 grid-bg border-r" style={{ borderColor: "var(--border-default)" }}>
        <div className="flex items-center gap-2.5">
          <span className="grid h-9 w-9 place-items-center rounded-md" style={{ background: "rgba(0,240,255,0.12)", border: "1px solid rgba(0,240,255,0.4)" }}><Radar size={18} color="#00F0FF" /></span>
          <span className="font-display text-xl font-bold tracking-tight">Varuna <span style={{ color: "#00F0FF" }}>Netra</span></span>
        </div>
        <div className="max-w-lg fade-up">
          <p className="label-mono mb-3">Authority console · restricted</p>
          <h1 className="font-display text-4xl font-extrabold tracking-tight lg:text-5xl leading-[1.05]">Satellite spill detection, AIS correlation, auditable decisions.</h1>
          <p className="mt-5 text-sm leading-relaxed text-slate-400">Every ingestion, correlation run, review and export is signed with your identity and written to an immutable audit trail. Output is decision support — never a legal determination of responsibility.</p>
        </div>
        <div className="flex items-center gap-2 font-mono text-[11px] text-slate-500"><ShieldCheck size={12} /> Role-based access · JWT sessions · bcrypt credentials</div>
      </div>
      <div className="flex items-center justify-center p-8">
        <form onSubmit={submit} className="panel w-full max-w-md p-8 fade-up" data-testid="login-form">
          <h2 className="font-display text-2xl font-bold tracking-tight">Sign in</h2>
          <p className="mt-1 text-xs text-slate-400">Use your authority account credentials.</p>
          <label className="mt-6 block"><span className="label-mono mb-1 block">Email</span>
            <input data-testid="login-email-input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required
              className="w-full rounded border bg-slate-900/60 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60" style={{ borderColor: "var(--border-highlight)" }} /></label>
          <label className="mt-3 block"><span className="label-mono mb-1 block">Password</span>
            <input data-testid="login-password-input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required
              className="w-full rounded border bg-slate-900/60 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60" style={{ borderColor: "var(--border-highlight)" }} /></label>
          {error && <p data-testid="login-error" className="mt-3 rounded px-3 py-2 text-xs" style={{ color: "#FF2A6D", background: "rgba(255,42,109,0.1)", border: "1px solid rgba(255,42,109,0.4)" }}>{error}</p>}
          <button data-testid="login-submit-button" disabled={busy} type="submit" className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded bg-cyan-400 px-4 py-2 font-mono text-xs font-semibold uppercase tracking-wider text-slate-950 hover:bg-cyan-300 disabled:opacity-50">
            <LogIn size={14} /> {busy ? "Signing in…" : "Sign in"}
          </button>
          <Link to="/forgot-password" data-testid="forgot-password-link" className="mt-3 block text-center font-mono text-[11px] uppercase tracking-wider text-slate-400 hover:text-cyan-300">Forgot password?</Link>
          {googleReady && (
            <div className="mt-5" data-testid="google-signin-block">
              <div className="flex items-center gap-3"><span className="h-px flex-1" style={{ background: "var(--border-default)" }} /><span className="label-mono">or</span><span className="h-px flex-1" style={{ background: "var(--border-default)" }} /></div>
              <button type="button" data-testid="google-signin-button" onClick={googleSignIn} disabled={googleBusy}
                className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded border px-4 py-2 font-mono text-xs font-semibold uppercase tracking-wider text-slate-100 hover:bg-slate-800/60 disabled:opacity-50" style={{ borderColor: "var(--border-highlight)" }}>
                <svg width="14" height="14" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.5l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.3l7.9 6.1C12.4 13.6 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.7 6c4.5-4.2 6.9-10.3 6.9-17.7z"/><path fill="#FBBC05" d="M10.5 28.6A14.5 14.5 0 0 1 9.5 24c0-1.6.3-3.1.8-4.6l-7.9-6.1A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.7l7.9-6.1z"/><path fill="#34A853" d="M24 48c6.3 0 11.7-2.1 15.6-5.8l-7.7-6c-2.1 1.4-4.8 2.3-7.9 2.3-6.3 0-11.6-4.1-13.5-9.9l-7.9 6.1C6.5 42.6 14.6 48 24 48z"/></svg>
                {googleBusy ? "Redirecting to Google…" : "Continue with Google"}
              </button>
              <p className="mt-2 text-center text-[10px] text-slate-500">Invite-only: your Google e-mail must already be an authorized Varuna Netra account.</p>
            </div>
          )}
          {showDemo && <div className="mt-6 border-t pt-4" style={{ borderColor: "var(--border-default)" }}>
            <p className="label-mono mb-2">Demo accounts</p>
            <div className="space-y-1.5">
              {DEMO.map((d) => (
                <button key={d.role} type="button" data-testid={`demo-login-${d.role}`} onClick={() => { setEmail(d.email); setPassword(DEMO_PASSWORDS[d.role] || ""); }}
                  className="flex w-full items-center justify-between rounded border px-3 py-2 text-left text-xs transition-colors hover:bg-slate-800/60" style={{ borderColor: "var(--border-default)" }}>
                  <span><span className="font-mono uppercase tracking-wider text-cyan-300">{d.role}</span> <span className="text-slate-400 ml-2">{d.email}</span></span>
                  <span className="text-[10px] text-slate-500">{d.scope}</span>
                </button>
              ))}
            </div>
            <p className="mt-2 text-[10px] text-slate-500">{Object.keys(DEMO_PASSWORDS).length ? "Demo credentials are pre-filled." : "Demo buttons pre-fill the email only — enter the issued password."} Admin account is the workspace owner's email (manages users).</p>
          </div>}
        </form>
      </div>
    </div>
  );
}
