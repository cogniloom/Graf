import { it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import SourcePicker from "./SourcePicker.svelte";
afterEach(() => vi.unstubAllGlobals());
it("navigates folders and selects a file without uploading it", async () => {
  const select = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(
            url.includes("path=")
              ? {
                  path: "/home/me/docs",
                  parent: "/home/me",
                  items: [
                    {
                      name: "Brief ü.pdf",
                      path: "/home/me/docs/Brief ü.pdf",
                      kind: "file",
                    },
                  ],
                  total: 1,
                }
              : {
                  path: "/home/me",
                  parent: "/home",
                  items: [
                    { name: "docs", path: "/home/me/docs", kind: "directory" },
                  ],
                  total: 1,
                },
          ),
        ),
    ),
  );
  render(SourcePicker, { kind: "file", select, close: vi.fn() });
  await userEvent.click(await screen.findByRole("button", { name: "docs/" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Brief ü.pdf" }),
  );
  expect(select).toHaveBeenCalledWith("/home/me/docs/Brief ü.pdf");
});
it("shows browse failures and lets the user cancel without selecting", async () => {
  const select = vi.fn(),
    close = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "Cannot open this folder" }), {
          status: 422,
        }),
    ),
  );
  render(SourcePicker, { kind: "directory", select, close });
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Cannot open this folder",
  );
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(close).toHaveBeenCalled());
  expect(select).not.toHaveBeenCalled();
});
