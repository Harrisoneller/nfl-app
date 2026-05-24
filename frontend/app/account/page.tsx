"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { useAuth } from "@/context/AuthProvider";

const inputClass =
  "w-full bg-bg border divider rounded px-3 py-2 text-sm focus:outline-none focus:border-team-primary";

export default function AccountPage() {
  const { user, loading, logout, updateProfile, changePassword } = useAuth();
  const router = useRouter();
  const [displayName, setDisplayName] = useState("");
  const [profileMsg, setProfileMsg] = useState<string | null>(null);
  const [profileErr, setProfileErr] = useState<string | null>(null);
  const [pwMsg, setPwMsg] = useState<string | null>(null);
  const [pwErr, setPwErr] = useState<string | null>(null);
  const [busyProfile, setBusyProfile] = useState(false);
  const [busyPw, setBusyPw] = useState(false);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  useEffect(() => {
    if (user) setDisplayName(user.display_name);
  }, [user]);

  if (loading) {
    return <p className="text-sm text-muted">Loading account…</p>;
  }

  if (!user) return null;

  async function saveProfile(e: FormEvent) {
    e.preventDefault();
    setProfileMsg(null);
    setProfileErr(null);
    setBusyProfile(true);
    try {
      await updateProfile(displayName.trim());
      setProfileMsg("Profile updated");
    } catch (err: unknown) {
      setProfileErr(err instanceof Error ? err.message : "Update failed");
    } finally {
      setBusyProfile(false);
    }
  }

  async function savePassword(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPwMsg(null);
    setPwErr(null);
    const fd = new FormData(e.currentTarget);
    const current = String(fd.get("current") ?? "");
    const next = String(fd.get("next") ?? "");
    const confirm = String(fd.get("confirm") ?? "");
    if (next !== confirm) {
      setPwErr("New passwords do not match");
      return;
    }
    setBusyPw(true);
    try {
      await changePassword(current, next);
      setPwMsg("Password updated");
      e.currentTarget.reset();
    } catch (err: unknown) {
      setPwErr(err instanceof Error ? err.message : "Password update failed");
    } finally {
      setBusyPw(false);
    }
  }

  const initial = (user.display_name || user.email)[0]?.toUpperCase() ?? "?";

  return (
    <div className="space-y-6 max-w-lg">
      <div className="flex items-center gap-4">
        <div
          className="w-12 h-12 rounded-full bg-team-primary/20 border divider flex items-center justify-center text-lg font-semibold text-team-primary"
          aria-hidden
        >
          {initial}
        </div>
        <div>
          <h1 className="text-2xl font-semibold">Account</h1>
          <p className="text-sm text-muted">{user.email}</p>
        </div>
      </div>

      <Card title="Profile">
        <form onSubmit={saveProfile} className="space-y-3">
          <label className="block space-y-1">
            <span className="text-xs text-muted">Display name</span>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className={inputClass}
            />
          </label>
          {profileErr && <p className="text-sm text-red-400">{profileErr}</p>}
          {profileMsg && <p className="text-sm text-green-500">{profileMsg}</p>}
          <button
            type="submit"
            disabled={busyProfile}
            className="bg-team-primary text-white text-sm rounded px-4 py-2 disabled:opacity-60"
          >
            Save profile
          </button>
        </form>
      </Card>

      <Card title="Change password">
        <form onSubmit={savePassword} className="space-y-3">
          <label className="block space-y-1">
            <span className="text-xs text-muted">Current password</span>
            <input
              name="current"
              type="password"
              required
              autoComplete="current-password"
              className={inputClass}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-muted">New password</span>
            <input
              name="next"
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
              className={inputClass}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-muted">Confirm new password</span>
            <input
              name="confirm"
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
              className={inputClass}
            />
          </label>
          {pwErr && <p className="text-sm text-red-400">{pwErr}</p>}
          {pwMsg && <p className="text-sm text-green-500">{pwMsg}</p>}
          <button
            type="submit"
            disabled={busyPw}
            className="bg-team-primary text-white text-sm rounded px-4 py-2 disabled:opacity-60"
          >
            Update password
          </button>
        </form>
      </Card>

      <div className="flex flex-wrap gap-3 text-sm">
        <button
          type="button"
          onClick={() => {
            logout();
            router.push("/");
          }}
          className="text-muted hover:text-text underline-offset-2 hover:underline"
        >
          Sign out
        </button>
        <Link href="/" className="text-team-primary hover:underline">
          Back to home
        </Link>
      </div>
    </div>
  );
}
