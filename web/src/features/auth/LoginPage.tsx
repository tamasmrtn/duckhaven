import { useEffect, useState } from "react";
import logoLight from "@/assets/logo-light.svg";
import logoDark from "@/assets/logo-dark.svg";
import { useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useLogin } from "@/queries/auth";
import { useSetupStatus } from "@/queries/setup";
import { useQueryClient } from "@tanstack/react-query";
import { workspacesApi } from "@/api/workspaces";

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const login = useLogin();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const setupStatus = useSetupStatus();

  // Brand-new install lands here from the index route; bounce to /setup so the
  // operator creates the first admin before any login attempt.
  useEffect(() => {
    if (setupStatus.data?.needs_admin) {
      void navigate({ to: "/setup" });
    }
  }, [setupStatus.data, navigate]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await login.mutateAsync({ email, password });
      const workspaces = await qc.fetchQuery({
        queryKey: ["workspaces"],
        queryFn: workspacesApi.list,
      });
      const first = workspaces[0];
      void navigate(
        first
          ? { to: "/$ws/worksheets", params: { ws: first.slug } }
          : { to: "/welcome" },
      );
    } catch {
      setError("Invalid credentials. Try any email and password.");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-canvas)]">
      <div className="w-full max-w-sm space-y-6 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-8 shadow-e2">
        {/* Logo */}
        <div className="flex flex-col items-center gap-2">
          <img
            src={logoLight}
            alt="DuckHaven"
            className="w-40 h-auto block dark:hidden"
          />
          <img
            src={logoDark}
            alt="DuckHaven"
            className="w-40 h-auto hidden dark:block"
          />
          <p className="text-sm text-text-secondary">
            SQL workspace over Delta Lake
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="email" className="text-sm">
              Email
            </Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              placeholder="you@duckhaven.local"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="h-9"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password" className="text-sm">
              Password
            </Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="h-9"
            />
          </div>

          {error && (
            <p
              className="text-xs text-[var(--status-failed)]"
              role="alert"
              aria-live="polite"
            >
              {error}
            </p>
          )}

          <Button
            type="submit"
            className="w-full h-9"
            disabled={login.isPending}
          >
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>

        <p className="text-center text-2xs text-text-tertiary">
          Self-hosted · Tailscale only
        </p>
      </div>
    </div>
  );
}
