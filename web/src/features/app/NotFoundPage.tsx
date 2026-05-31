import { Link } from "@tanstack/react-router";
import { Compass } from "lucide-react";
import { Button } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 bg-[var(--bg-canvas)] px-6 text-center">
      <Compass className="size-10 text-text-tertiary" />
      <div className="space-y-1">
        <h1 className="text-lg font-semibold text-text-primary">
          Page not found
        </h1>
        <p className="text-sm text-text-secondary">
          This workspace or page doesn’t exist, or you don’t have access to it.
        </p>
      </div>
      <Button asChild size="sm">
        <Link to="/">Go to your workspace</Link>
      </Button>
    </div>
  );
}
