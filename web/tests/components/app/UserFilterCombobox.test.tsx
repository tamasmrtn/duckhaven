import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@tests/mock/server";
import { createWrapper } from "@tests/utils";
import { UserFilterCombobox } from "@/components/app/UserFilterCombobox";

const USERS = [
  {
    id: "u-1",
    email: "marton@duckhaven.local",
    name: "Marton",
    role: "admin",
    theme: "system",
    auth_provider: "local",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "u-2",
    email: "jess@duckhaven.local",
    name: "Jess",
    role: "user",
    theme: "dark",
    auth_provider: "local",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
  },
];

function renderCombobox(value: string | null, onChange = vi.fn()) {
  server.use(
    http.get("/api/admin/users", () =>
      HttpResponse.json({ items: USERS, cursor: null, has_more: false }),
    ),
  );
  const { wrapper: Wrapper } = createWrapper();
  return {
    onChange,
    ...render(
      <Wrapper>
        <UserFilterCombobox value={value} onChange={onChange} />
      </Wrapper>,
    ),
  };
}

describe("UserFilterCombobox", () => {
  it('defaults to "All users" and lists every user when opened', async () => {
    const user = userEvent.setup();
    renderCombobox(null);

    expect(screen.getByRole("combobox")).toHaveTextContent("All users");
    await user.click(screen.getByRole("combobox"));

    expect(await screen.findByText("Marton")).toBeInTheDocument();
    expect(screen.getByText("Jess")).toBeInTheDocument();
  });

  it("selects a user and reports their id", async () => {
    const user = userEvent.setup();
    const { onChange } = renderCombobox(null);

    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByText("Jess"));

    expect(onChange).toHaveBeenCalledWith("u-2");
  });

  it("shows the selected user name on the trigger", async () => {
    renderCombobox("u-2");
    await waitFor(() =>
      expect(screen.getByRole("combobox")).toHaveTextContent("Jess"),
    );
  });

  it('clears the filter via "All users"', async () => {
    const user = userEvent.setup();
    const { onChange } = renderCombobox("u-2");

    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByText("All users"));

    expect(onChange).toHaveBeenCalledWith(null);
  });
});
