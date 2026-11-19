import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import logoLight from "@/assets/logo-light.svg";
import logoDark from "@/assets/logo-dark.svg";
import { Button } from "@/components/ui/button";
import { CreateWorkspaceDialog } from "@/features/workspace/CreateWorkspaceDialog";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { useMe } from "@/queries/auth";

export function WelcomePage() {
  useDocumentTitle("Welcome");
  const navigate = useNavigate();
  const { data: me, isLoading } = useMe();
  const [open, setOpen] = useState(true);

  function handleCreated(slug: string) {
    setOpen(false);
    void navigate({ to: "/$ws/worksheets", params: { ws: slug } });
  }

  // Wait for `me`, so an account that can create a workspace never sees the
  // "ask an admin" card flash first.
  if (isLoading) return null;
  const canCreate = me?.permissions?.includes("workspaces:create") ?? false;

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-canvas)]">
      <div className="w-full max-w-md space-y-6 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-8 text-center shadow-e2">
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
        </div>
        {canCreate ? (
          <>
            <div className="space-y-1.5">
              <h1 className="text-lg font-semibold text-text-primary">
                Create your first workspace
              </h1>
              <p className="text-sm text-text-secondary">
                A workspace holds a team&apos;s catalogs, worksheets and
                history. Create one to start querying.
              </p>
            </div>
            <Button className="w-full h-9" onClick={() => setOpen(true)}>
              Get started
            </Button>
          </>
        ) : (
          <div className="space-y-1.5">
            <h1 className="text-lg font-semibold text-text-primary">
              You&apos;re not in a workspace yet
            </h1>
            <p className="text-sm text-text-secondary">
              An admin adds you to one. Send them the email you sign in with,{" "}
              <span className="font-medium text-text-primary">{me?.email}</span>
              , then reload this page.
            </p>
          </div>
        )}
      </div>
      {canCreate && (
        <CreateWorkspaceDialog
          open={open}
          onOpenChange={setOpen}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
}
