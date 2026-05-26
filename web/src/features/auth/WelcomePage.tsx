import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import logoLight from "@/assets/logo-light.svg";
import logoDark from "@/assets/logo-dark.svg";
import { Button } from "@/components/ui/button";
import { CreateWorkspaceDialog } from "@/features/workspace/CreateWorkspaceDialog";

export function WelcomePage() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(true);

  function handleCreated(slug: string) {
    setOpen(false);
    void navigate({ to: "/$ws/worksheets", params: { ws: slug } });
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-canvas)]">
      <div className="w-full max-w-md space-y-6 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-8 text-center shadow-e2">
        <div className="flex flex-col items-center gap-2">
          <img
            src={logoLight}
            alt="duckhaven"
            className="w-40 h-auto block dark:hidden"
          />
          <img
            src={logoDark}
            alt="duckhaven"
            className="w-40 h-auto hidden dark:block"
          />
        </div>
        <div className="space-y-1.5">
          <h1 className="text-lg font-semibold text-text-primary">
            Create your first workspace
          </h1>
          <p className="text-sm text-text-secondary">
            A workspace is bound to a storage backend. Create one to start
            querying.
          </p>
        </div>
        <Button className="w-full h-9" onClick={() => setOpen(true)}>
          Get started
        </Button>
      </div>
      <CreateWorkspaceDialog
        open={open}
        onOpenChange={setOpen}
        onCreated={handleCreated}
      />
    </div>
  );
}
