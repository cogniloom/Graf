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

it("selects a local folder and adds its actual path without an allowlist", async () => {
  const fetch = vi.fn(
    async (url: string) =>
      new Response(
        JSON.stringify(
          url.startsWith("/api/filesystem")
            ? {
                path: "/home/test/Dossier Scheidung",
                parent: "/home/test",
                items: [],
                total: 0,
              }
            : { items: [] },
        ),
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
  await userEvent.click(screen.getByRole("button", { name: "Choose folder…" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Select this folder" }),
  );
  await waitFor(() =>
    expect(
      (screen.getByLabelText("File or directory path") as HTMLInputElement)
        .value,
    ).toBe("/home/test/Dossier Scheidung"),
  );
  expect(screen.queryByText("Allowed locations")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: /Add source/ }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/sources",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ path: "/home/test/Dossier Scheidung" }),
      }),
    ),
  );
});

it("accepts a slow source response across multiple polling ticks", async () => {
  let resolve!: (response: Response) => void;
  const fetch = vi.fn(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  );
  vi.stubGlobal("fetch", fetch);
  const props = {
    view: "Sources",
    tick: 0,
    status: { counts: {}, state: "updating", phase: "ingesting" } as Status,
    refresh: async () => {},
    logout: async () => {},
  };
  const app = render(Management, props);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  await app.rerender({ ...props, tick: 1 });
  await app.rerender({ ...props, tick: 2 });
  expect(fetch).toHaveBeenCalledTimes(1);
  resolve(
    new Response(
      JSON.stringify({
        items: [
          {
            id: "s1",
            path: "/slow/source.txt",
            kind: "file",
            enabled: true,
            status: "pending",
            file_count: 8594,
            error: null,
          },
        ],
      }),
    ),
  );
  await screen.findByText("source.txt");
});

it("finishes a source mutation without waiting for status and discards the old source response", async () => {
  let oldResponse!: (response: Response) => void;
  let reads = 0;
  const fetch = vi.fn((url: string, init: RequestInit) => {
    if (init.method === "POST") return Promise.resolve(new Response("{}"));
    if (++reads === 1)
      return new Promise<Response>((done) => {
        oldResponse = done;
      });
    return Promise.resolve(
      new Response(
        JSON.stringify({
          items: [
            {
              id: "new",
              path: "/new.txt",
              kind: "file",
              enabled: true,
              status: "pending",
              file_count: 1,
              error: null,
            },
          ],
        }),
      ),
    );
  });
  vi.stubGlobal("fetch", fetch);
  render(Management, {
    view: "Sources",
    tick: 0,
    status: { counts: {}, state: "updating", phase: "ingesting" } as Status,
    refresh: () => new Promise<void>(() => {}),
    logout: async () => {},
  });
  await userEvent.type(
    screen.getByLabelText("File or directory path"),
    "/new.txt",
  );
  await userEvent.click(screen.getByRole("button", { name: /Add source/ }));
  await screen.findByText("Workspace updated.");
  await screen.findByText("new.txt");
  oldResponse(new Response('{"items":[]}'));
  await waitFor(() => expect(screen.getByText("new.txt")).toBeTruthy());
  expect(
    screen
      .getByRole("button", { name: "Choose folder…" })
      .hasAttribute("disabled"),
  ).toBe(false);
});
