import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { QueryClientProvider } from "@tanstack/react-query";
import { server } from "@tests/mock/server";
import { createTestQueryClient } from "@tests/utils";
import { CreateCatalogDialog } from "@/features/catalog/CatalogDialogs";

const ICEBERG = {
  kind: "iceberg_polaris",
  label: "Apache Iceberg + Polaris",
  available: true,
  unavailable_reason: null,
  capabilities: {
    snapshot_granularity: "table",
    supports_storage_migration: true,
    maintenance_executable: false,
    external_engine_readable: true,
    supported_storage_kinds: ["object_store", "s3", "adls_gen2"],
  },
};

const DUCKLAKE = {
  kind: "ducklake",
  label: "DuckLake",
  available: true,
  unavailable_reason: null,
  capabilities: {
    snapshot_granularity: "catalog",
    supports_storage_migration: false,
    maintenance_executable: true,
    external_engine_readable: false,
    supported_storage_kinds: ["object_store", "s3", "adls_gen2"],
  },
};

function renderDialog() {
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <CreateCatalogDialog ws="acme-analytics" open onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
}

function kinds(...list: unknown[]) {
  server.use(http.get("/api/catalog-kinds", () => HttpResponse.json(list)));
}

describe("catalog kind choice", () => {
  it("is hidden entirely when only one kind is available", async () => {
    // The default deployment. Nothing changes for an operator who has not
    // enabled DuckLake — they must not see a choice with one option.
    kinds(ICEBERG, { ...DUCKLAKE, available: false });
    renderDialog();

    await screen.findByLabelText("Name");
    expect(screen.queryByText("Catalog kind")).not.toBeInTheDocument();
  });

  it("offers both kinds once DuckLake is enabled", async () => {
    kinds(ICEBERG, DUCKLAKE);
    renderDialog();

    expect(await screen.findByText("Catalog kind")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Apache Iceberg \+ Polaris/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /DuckLake/ }),
    ).toBeInTheDocument();
  });

  it("warns that a DuckLake catalog is readable by DuckDB only", async () => {
    // The one irreversible consequence of the choice, shown at the moment of
    // choosing rather than only in the docs.
    kinds(ICEBERG, DUCKLAKE);
    renderDialog();

    expect(
      await screen.findByText(/Readable by DuckDB only/i),
    ).toBeInTheDocument();
  });

  it("does not warn about the Iceberg kind, which other engines can read", async () => {
    kinds(ICEBERG, { ...DUCKLAKE, available: false });
    renderDialog();

    await screen.findByLabelText("Name");
    expect(
      screen.queryByText(/Readable by DuckDB only/i),
    ).not.toBeInTheDocument();
  });

  it("sends the chosen kind on create", async () => {
    const user = userEvent.setup();
    let body: Record<string, unknown> | undefined;
    kinds(ICEBERG, DUCKLAKE);
    server.use(
      http.post("/api/workspaces/:ws/catalogs", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: "cat-new" }, { status: 201 });
      }),
    );
    renderDialog();

    await user.click(await screen.findByRole("button", { name: /DuckLake/ }));
    await user.type(screen.getByLabelText("Name"), "lake");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await vi.waitFor(() => expect(body).toBeDefined());
    expect(body).toMatchObject({ name: "lake", kind: "ducklake" });
  });

  it("defaults to Iceberg, so an unchanged form creates what it always did", async () => {
    const user = userEvent.setup();
    let body: Record<string, unknown> | undefined;
    kinds(ICEBERG, DUCKLAKE);
    server.use(
      http.post("/api/workspaces/:ws/catalogs", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: "cat-new" }, { status: 201 });
      }),
    );
    renderDialog();

    await user.type(await screen.findByLabelText("Name"), "curated");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await vi.waitFor(() => expect(body).toBeDefined());
    expect(body).toMatchObject({ kind: "iceberg_polaris" });
  });

  it("shows a disabled kind with the reason rather than hiding it", async () => {
    // An operator who read the docs and expected DuckLake should learn why it
    // is not selectable, not be left wondering where it went.
    kinds(ICEBERG, DUCKLAKE, {
      ...DUCKLAKE,
      kind: "future_kind",
      label: "Future",
      available: false,
      unavailable_reason: "Not enabled on this deployment.",
    });
    renderDialog();

    expect(
      await screen.findByText(/Not enabled on this deployment/),
    ).toBeInTheDocument();
  });
});
