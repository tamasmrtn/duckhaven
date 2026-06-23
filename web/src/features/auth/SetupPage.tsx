import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import logoLight from "@/assets/logo-light.svg";
import logoDark from "@/assets/logo-dark.svg";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSetupStatus, useCreateFirstAdmin } from "@/queries/setup";
import type { SystemStorageKind } from "@/api/setup";

export function SetupPage() {
  const navigate = useNavigate();
  const status = useSetupStatus();
  const createAdmin = useCreateFirstAdmin();

  const [token, setToken] = useState("");
  const [name, setName] = useState("Admin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [storageKind, setStorageKind] =
    useState<SystemStorageKind>("object_store");
  const [storageUri, setStorageUri] = useState("");
  const [error, setError] = useState("");

  // If setup is already complete, bounce to /login. Deep-linkers and refresh
  // after the admin was created elsewhere both land here.
  useEffect(() => {
    if (status.data && !status.data.needs_admin) {
      void navigate({ to: "/login" });
    }
  }, [status.data, navigate]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await createAdmin.mutateAsync({
        token: token.trim(),
        input: {
          email,
          password,
          name,
          system_storage: {
            kind: storageKind,
            name: "System",
            root_uri: storageKind === "object_store" ? "" : storageUri.trim(),
          },
        },
      });
      void navigate({ to: "/welcome" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create admin");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-canvas)]">
      <div className="w-full max-w-md space-y-6 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-8 shadow-e2">
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
            Create the first admin account
          </p>
        </div>

        <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-3 text-2xs text-text-secondary">
          Run on the control-plane host to read your setup token, then paste it
          below:
          <pre className="mt-2 overflow-x-auto rounded bg-[var(--bg-surface)] p-2 font-mono text-xs">
            docker compose exec api cat /var/duckhaven/setup_token
          </pre>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="token" className="text-sm">
              Setup token
            </Label>
            <Input
              id="token"
              type="text"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              required
              className="h-9 font-mono text-xs"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="name" className="text-sm">
              Your name
            </Label>
            <Input
              id="name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="h-9"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="email" className="text-sm">
              Email
            </Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
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
              autoComplete="new-password"
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="h-9"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="system-storage" className="text-sm">
              System catalog storage
            </Label>
            <p className="text-2xs text-text-secondary">
              Where the built-in, read-only system catalog (query history,
              audit, object metadata) stores its data.
            </p>
            <select
              id="system-storage"
              value={storageKind}
              onChange={(e) =>
                setStorageKind(e.target.value as SystemStorageKind)
              }
              className="h-9 w-full rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 text-sm"
            >
              <option value="object_store">Bundled object store (MinIO)</option>
              <option value="s3">Amazon S3</option>
              <option value="adls_gen2">Azure Data Lake Gen2</option>
            </select>
            {storageKind !== "object_store" && (
              <Input
                id="system-storage-uri"
                type="text"
                placeholder={
                  storageKind === "s3"
                    ? "s3://bucket/prefix"
                    : "abfss://container@account.dfs.core.windows.net/prefix"
                }
                value={storageUri}
                onChange={(e) => setStorageUri(e.target.value)}
                required
                className="h-9 font-mono text-xs"
                aria-label="System catalog storage URI"
              />
            )}
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
            disabled={createAdmin.isPending}
          >
            {createAdmin.isPending ? "Creating admin…" : "Create admin"}
          </Button>
        </form>
      </div>
    </div>
  );
}
