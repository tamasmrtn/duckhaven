import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@tests/mock/server";
import { createWrapper, render, screen, waitFor } from "@tests/utils";
import { SaveAsMetricDialog } from "@/features/semantic/SaveAsMetricDialog";

function open(sql: string) {
  const { wrapper } = createWrapper();
  return render(
    <SaveAsMetricDialog
      ws="acme-analytics"
      sql={sql}
      open
      onOpenChange={() => {}}
    />,
    { wrapper },
  );
}

/**
 * The point of this dialog is that a calculation gets written down at the moment
 * it is written, rather than being re-derived — slightly differently — by the
 * next person and by the assistant. So what matters is that the seeding is
 * right and, where it cannot be, visible and correctable.
 */
describe("SaveAsMetricDialog", () => {
  it("seeds the aggregation and expression separately from the selection", async () => {
    // Not `SUM(amount)` in one box: the API stores the pair, and that split is
    // what leaves the arithmetic with the compiler.
    open("SUM(amount)");

    expect(await screen.findByLabelText("Aggregation")).toHaveTextContent("sum");
    expect(await screen.findByLabelText("Expression")).toHaveValue("amount");
  });

  it("takes the metric name from a trailing alias", async () => {
    open("SUM(amount) AS revenue");

    expect(await screen.findByLabelText("Name")).toHaveValue("revenue");
  });

  it("shows what it could not split so it can be corrected before saving", async () => {
    open("amount * quantity");

    expect(await screen.findByLabelText("Expression")).toHaveValue(
      "amount * quantity",
    );
    expect(await screen.findByLabelText("Aggregation")).toHaveTextContent("sum");
  });

  it("cannot be saved before a model and dataset are chosen", async () => {
    // The two facts a worksheet genuinely cannot know, so they are asked for.
    open("SUM(amount) AS revenue");

    expect(await screen.findByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("offers only models that are edited here", async () => {
    // `marketing` is imported from dbt; changing it here would be undone by the
    // next import, so it is not on the list.
    const user = userEvent.setup();
    open("SUM(amount)");

    await user.click(await screen.findByLabelText("Model"));

    expect(await screen.findByRole("option", { name: "Sales" })).toBeVisible();
    expect(
      screen.queryByRole("option", { name: /marketing/i }),
    ).not.toBeInTheDocument();
  });

  it("posts the aggregation and expression as separate fields", async () => {
    // The assertion that matters: what reaches the API is `agg: "sum"` over
    // `expr: "amount"`, never the string "SUM(amount)" — which would save as
    // SUM(SUM(amount)) the first time it compiled.
    let posted: Record<string, unknown> | null = null;
    server.use(
      http.post(
        "/api/workspaces/:ws/semantic/models/:slug/metrics",
        async ({ request }) => {
          posted = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ id: "sem-met-new" }, { status: 201 });
        },
      ),
    );
    const user = userEvent.setup();
    open("SUM(amount) AS gross_revenue");

    await user.click(await screen.findByLabelText("Model"));
    await user.click(await screen.findByRole("option", { name: "Sales" }));
    await user.click(await screen.findByLabelText("Dataset"));
    await user.click(await screen.findByRole("option", { name: "events" }));
    await user.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({
      name: "gross_revenue",
      dataset: "events",
      agg: "sum",
      expr: "amount",
    });
  });
});
