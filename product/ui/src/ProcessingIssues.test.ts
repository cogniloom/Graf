import { afterEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import ProcessingIssues from "./ProcessingIssues.svelte";
import type { Status } from "./api";
const status = {
  state: "ready_with_gaps",
  revision: 7,
  published_revision: 7,
  counts: {},
} as Status;
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
it("shows unsupported paths and reasons, paginates and resets on filter change", async () => {
  const fetch = vi.fn(
    async (url: string) =>
      new Response(
        JSON.stringify({
          items: [
            {
              id: url,
              path: "/Legal/archive/file.xyz",
              status: "unsupported",
              warnings: ["Unsupported format: .xyz"],
              passage_count: 0,
            },
          ],
          total: 51,
        }),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  render(ProcessingIssues, { status });
  expect(fetch).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "Review processing issues" }),
  );
  expect(await screen.findByText("/Legal/archive/file.xyz")).toBeTruthy();
  expect(screen.getByText("Unsupported format: .xyz")).toBeTruthy();
  await userEvent.click(screen.getByText("Next issues"));
  await waitFor(() =>
    expect(fetch.mock.calls.at(-1)?.[0]).toContain("offset=50"),
  );
  await userEvent.selectOptions(screen.getByLabelText("Show"), "partial");
  await waitFor(() =>
    expect(fetch.mock.calls.at(-1)?.[0]).toBe(
      "/api/document-issues?kind=partial&offset=0&limit=50",
    ),
  );
});
it("clears stale results when the collection revision changes", async () => {
  let resolve!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>((r) => (resolve = r))),
  );
  const view = render(ProcessingIssues, { status });
  await userEvent.click(screen.getByText("Review processing issues"));
  await view.rerender({
    status: { ...status, revision: 8, state: "updating" },
  });
  resolve(
    new Response(
      JSON.stringify({
        items: [
          { id: "old", path: "old.xyz", warnings: [], status: "unsupported" },
        ],
        total: 1,
      }),
    ),
  );
  await Promise.resolve();
  expect(screen.queryByText("old.xyz")).toBeNull();
  expect(
    screen.getByText(/Counts during the scan are provisional/),
  ).toBeTruthy();
});
it("explains the restart needed by a backend already running an ingestion", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }),
    ),
  );
  render(ProcessingIssues, { status });
  await userEvent.click(screen.getByText("Review processing issues"));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Restart Graf after the current scan finishes",
  );
});

it("shows the recorded reason for each failed document", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify({
            total: url.includes("kind=failed") ? 1 : 0,
            items: url.includes("kind=failed")
              ? [
                  {
                    id: "failed-pdf",
                    path: "/Legal/exhibit.pdf",
                    status: "failed",
                    warnings: [
                      "PDF parser failed: cannot open broken document",
                    ],
                    passage_count: 0,
                  },
                ]
              : [],
          }),
        ),
    ),
  );
  render(ProcessingIssues, { status });
  await userEvent.click(screen.getByText("Review processing issues"));
  await userEvent.selectOptions(screen.getByLabelText("Show"), "failed");
  expect(await screen.findByText("/Legal/exhibit.pdf")).toBeTruthy();
  expect(
    screen.getByText("PDF parser failed: cannot open broken document"),
  ).toBeTruthy();
});

it("waits beyond the quick API deadline and aborts obsolete requests", async () => {
  const timeout = vi.spyOn(AbortSignal, "timeout");
  const requests: {
    signal: AbortSignal;
    resolve: (value: Response) => void;
  }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((resolve) => {
          requests.push({ signal: init.signal as AbortSignal, resolve });
        }),
    ),
  );
  const view = render(ProcessingIssues, { status });
  await userEvent.click(screen.getByText("Review processing issues"));
  expect(screen.getByText(/Verifying source files/)).toBeTruthy();
  expect(timeout).not.toHaveBeenCalled();
  vi.useFakeTimers();
  await vi.advanceTimersByTimeAsync(16000);
  expect(requests[0].signal.aborted).toBe(false);
  vi.useRealTimers();
  await userEvent.selectOptions(screen.getByLabelText("Show"), "failed");
  expect(requests[0].signal.aborted).toBe(true);
  requests[0].resolve(
    new Response(
      JSON.stringify({
        items: [
          {
            id: "stale",
            path: "stale.xyz",
            status: "unsupported",
            warnings: [],
          },
        ],
        total: 1,
      }),
    ),
  );
  await Promise.resolve();
  expect(screen.queryByText("stale.xyz")).toBeNull();
  await userEvent.click(screen.getByText("Hide processing issues"));
  expect(requests[1].signal.aborted).toBe(true);
  await userEvent.click(screen.getByText("Review processing issues"));
  view.unmount();
  expect(requests[2].signal.aborted).toBe(true);
  timeout.mockRestore();
});

it("retries a failed request and displays the completed report", async () => {
  const fetch = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("Network connection lost"))
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ items: [], total: 0 })),
    );
  vi.stubGlobal("fetch", fetch);
  render(ProcessingIssues, { status });
  await userEvent.click(screen.getByText("Review processing issues"));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Network connection lost",
  );
  await userEvent.click(screen.getByText("Retry loading issues"));
  expect(
    await screen.findByText("No documents match this filter."),
  ).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(2);
});
