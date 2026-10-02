"use client";

/**
 * Demo SSO sign-in page. Posts email + password to the NextAuth "credentials"
 * provider and returns to the dashboard on success. The three seeded logins are
 * shown as helper text — signing in is optional; the app also works signed out.
 */

import { signIn } from "next-auth/react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

const DEMO_LOGINS = [
  { email: "admin@camai.dev", role: "admin", tenant: "demo-tenant" },
  { email: "manager@acme.dev", role: "manager", tenant: "acme-foods" },
  { email: "viewer@camai.dev", role: "viewer", tenant: "demo-tenant" },
];

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    const res = await signIn("credentials", {
      email,
      password,
      redirect: false,
      callbackUrl: "/",
    });
    setSubmitting(false);
    if (res?.error) {
      setError("Invalid email or password.");
    } else if (res?.url) {
      window.location.href = res.url;
    }
  }

  return (
    <main className="mx-auto flex min-h-[70vh] max-w-md flex-col justify-center px-4 py-10">
      <div className="rounded-2xl border border-line bg-panel p-6 shadow-sm">
        <h1 className="text-xl font-semibold tracking-tight">
          Sign in to Cam<span className="text-accent">AI</span>
        </h1>
        <p className="mt-1 text-sm text-muted">
          Demo SSO — the dashboard also works without signing in.
        </p>

        <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-4">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted">Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
              className="rounded-lg border border-line bg-panel-2 px-3 py-2 text-fg outline-none focus:border-accent"
              placeholder="admin@camai.dev"
            />
          </label>

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted">Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              className="rounded-lg border border-line bg-panel-2 px-3 py-2 text-fg outline-none focus:border-accent"
              placeholder="demo"
            />
          </label>

          {error ? <p className="text-sm text-bad">{error}</p> : null}

          <button
            type="submit"
            disabled={submitting}
            className="mt-1 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-bg transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <div className="mt-6 border-t border-line pt-4">
          <p className="text-xs uppercase tracking-wide text-muted">
            Demo logins (password: demo)
          </p>
          <ul className="mt-2 flex flex-col gap-1 text-sm text-fg">
            {DEMO_LOGINS.map((u) => (
              <li key={u.email} className="flex items-center justify-between">
                <span>{u.email}</span>
                <span className="text-xs text-muted">
                  {u.role} · {u.tenant}
                </span>
              </li>
            ))}
          </ul>
        </div>

        <Link
          href="/"
          className="mt-6 inline-block text-sm text-muted hover:text-fg"
        >
          ← Back to dashboard
        </Link>
      </div>
    </main>
  );
}
