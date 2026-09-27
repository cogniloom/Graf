import { it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import Explore from "./Explore.svelte";
import { Status } from "./api";

it("discards late source previews when evidence becomes unpublished", async () => {
  let resolveDetail: (x: Response) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/graph"))
        return Promise.resolve(
          new Response(JSON.stringify({ nodes: [], edges: [] })),
        );
      if (url.includes("/documents?"))
        return Promise.resolve(
          new Response(
            JSON.stringify({
              items: [
                {
                  id: "d1",
                  path: "/test.txt",
                  status: "ready",
                  warnings: [],
                  passage_count: 1,
                },
              ],
              total: 1,
            }),
          ),
        );
      return new Promise<Response>((r) => {
        resolveDetail = r;
      });
    }),
  );
  const status = {
    state: "ready",
    revision: 1,
    published_revision: 1,
    snapshot_id: "snapshot",
  } as Status;
  const { rerender } = render(Explore, {
    status,
    query: "test",
    onSources: () => {},
  });
  await userEvent.click(await screen.findByText("test.txt"));
  await screen.findByText("Loading source…");
  await rerender({
    status: { ...status, state: "updating", revision: 2 },
    query: "test",
    onSources: () => {},
  });
  resolveDetail(
    new Response(
      JSON.stringify({
        id: "d1",
        path: "/test.txt",
        status: "ready",
        warnings: [],
        passages: [{ id: "p1", text: "STALE SECRET", locators: {} }],
        relationships: [],
      }),
    ),
  );
  await waitFor(() => expect(screen.queryByText("STALE SECRET")).toBeNull());
  expect(screen.getByText("Preparing your evidence")).toBeTruthy();
  vi.unstubAllGlobals();
});
