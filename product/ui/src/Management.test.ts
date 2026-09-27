import { it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import Management from "./Management.svelte";
import type { Status } from "./api";
afterEach(() => vi.unstubAllGlobals());
it("shows actual errors and requires explicit source removal confirmation", async () => {
  const fetch = vi.fn(
    async () =>
      new Response(
        JSON.stringify({
          items: [
            {
              id: "s1",
              path: "/allowed/test.txt",
              kind: "file",
              enabled: true,
              status: "error",
              file_count: 1,
              error: "File disappeared",
            },
          ],
          allowed_roots: ["/allowed"],
        }),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  render(Management, {
    view: "Sources",
    tick: 0,
    status: { counts: {}, state: "ready", phase: "idle" } as Status,
    refresh: async () => {},
    logout: async () => {},
  });
  expect(await screen.findByText("File disappeared")).toBeTruthy();
  await userEvent.click(screen.getByLabelText("Remove /allowed/test.txt"));
  expect(
    fetch.mock.calls.some(
      (c) => (c as unknown as [string, RequestInit])[1]?.method === "DELETE",
    ),
  ).toBe(false);
  await userEvent.click(screen.getByText("Cancel"));
  await userEvent.click(screen.getByLabelText("Remove /allowed/test.txt"));
  await userEvent.click(screen.getByRole("button", { name: "Remove source" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/sources/s1",
      expect.objectContaining({ method: "DELETE" }),
    ),
  );
});
