"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

const inputClass =
  "w-full bg-bg border divider rounded px-3 py-2 text-sm focus:outline-none focus:border-team-primary";

export function AuthForm({
  mode,
  onSubmit,
}: {
  mode: "login" | "register";
  onSubmit: (data: { email: string; password: string; displayName?: string }) => Promise<void>;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await onSubmit({
        email: email.trim(),
        password,
        displayName: displayName.trim() || undefined,
      });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const isLogin = mode === "login";

  return (
    <form onSubmit={handleSubmit} className="space-y-4 max-w-md">
      <p className="text-sm text-muted">
        Optional — browse teams, scores, and odds without an account. Sign in to save widgets and
        personalize AI chat when multi-user mode is enabled on the server.
      </p>

      {!isLogin && (
        <label className="block space-y-1">
          <span className="text-xs text-muted">Display name</span>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            autoComplete="name"
            className={inputClass}
            placeholder="Optional"
          />
        </label>
      )}

      <label className="block space-y-1">
        <span className="text-xs text-muted">Email</span>
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          className={inputClass}
        />
      </label>

      <label className="block space-y-1">
        <span className="text-xs text-muted">Password</span>
        <input
          type="password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={isLogin ? "current-password" : "new-password"}
          className={inputClass}
        />
        {!isLogin && (
          <span className="text-[11px] text-muted">At least 8 characters</span>
        )}
      </label>

      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={busy}
        className="w-full bg-team-primary text-white text-sm rounded px-4 py-2 disabled:opacity-60"
      >
        {busy ? "Please wait…" : isLogin ? "Sign in" : "Create account"}
      </button>

      <p className="text-sm text-muted">
        {isLogin ? (
          <>
            No account?{" "}
            <Link href="/register" className="text-team-primary hover:underline">
              Register
            </Link>
          </>
        ) : (
          <>
            Already have an account?{" "}
            <Link href="/login" className="text-team-primary hover:underline">
              Sign in
            </Link>
          </>
        )}
      </p>
    </form>
  );
}
