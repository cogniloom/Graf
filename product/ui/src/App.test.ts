import { it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/svelte";
import App from "./App.svelte";
afterEach(() => {
  vi.unstubAllGlobals();
  history.replaceState(null, "", "/");
});
it("consumes a new bootstrap fragment on an already mounted unauthorized page", async () => {
  let authenticated = false;
  history.replaceState(null, "", "/");
  const fetch = vi.fn(async (url: string) => {
    if (url === "/api/session") {
      expect(location.hash).toBe("");
      authenticated = true;
      return new Response("{}");
    }
    if (url === "/api/investigations") return new Response('{"items":[]}');
    return new Response(
      JSON.stringify(
        authenticated
          ? {
              workspace_name: "Test",
              state: "empty",
              counts: {},
              revision: 1,
              published_revision: 0,
            }
          : { detail: "Authentication required" },
      ),
      { status: authenticated ? 200 : 401 },
    );
  });
  vi.stubGlobal("fetch", fetch);
  render(App);
  await screen.findByText("Open your workspace securely");
  location.hash = "token=synthetic-mounted-page-token";
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  await screen.findByLabelText("What would you like to investigate?");
  await waitFor(() => expect(location.hash).toBe(""));
  expect(
    fetch.mock.calls.filter(([url]) => url === "/api/session"),
  ).toHaveLength(1);
});

it("restores the selected page after remount and follows browser history", async () => {
  history.replaceState(null, "", "/?page=settings");
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(
            url === "/api/status"
              ? {
                  workspace_name: "Test",
                  state: "updating",
                  phase: "ingesting",
                  counts: {},
                  revision: 5,
                  published_revision: 1,
                  message: "Processing sources",
                }
              : url === "/api/settings"
                ? { device: "cpu" }
                : { items: [] },
          ),
        ),
    ),
  );
  const app = render(App);
  await screen.findByRole("heading", { name: "Workspace settings" });
  await fireEvent.click(screen.getByRole("button", { name: "Sources" }));
  await screen.findByRole("heading", { name: "Your sources" });
  expect(new URLSearchParams(location.search).get("page")).toBe("sources");
  app.unmount();
  render(App);
  await screen.findByRole("heading", { name: "Your sources" });
  history.replaceState(null, "", "/?page=settings");
  window.dispatchEvent(new PopStateEvent("popstate"));
  await screen.findByRole("heading", { name: "Workspace settings" });
});

it("loads sources and keeps navigation usable while status is pending", async () => {
  history.replaceState(null, "", "/?page=sources");
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      url === "/api/status"
        ? new Promise<Response>(() => {})
        : Promise.resolve(
            new Response(
              JSON.stringify(
                url === "/api/settings" ? { device: "cpu" } : { items: [] },
              ),
            ),
          ),
    ),
  );
  render(App);
  await screen.findByText("Start with your first source");
  expect(
    screen
      .getByRole("button", { name: "Choose folder…" })
      .hasAttribute("disabled"),
  ).toBe(false);
  await fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  await screen.findByRole("heading", { name: "Workspace settings" });
});

it("shows the current running retry instead of an old stopped message", async () => {
  history.replaceState(null, "", "/?page=activity");
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(
            url === "/api/status"
              ? {
                  workspace_name: "Test",
                  state: "blocked",
                  phase: "ingesting",
                  counts: {},
                  revision: 5,
                  published_revision: 1,
                  message: "Source ingestion stopped",
                  jobs: [{ state: "running", phase: "ingesting", revision: 5 }],
                }
              : { items: [] },
          ),
        ),
    ),
  );
  render(App);
  await screen.findByText(
    "Your workspace stays available while we prepare your documents.",
  );
  expect(screen.queryByText("Source ingestion stopped")).toBeNull();
});

it("keeps a session draft mounted when the status poll fails", async () => {
  history.replaceState(null, "", "/?page=sessions");
  let checks = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/status") {
        if (++checks > 1)
          return new Response('{"detail":"Status temporarily unavailable"}', {
            status: 503,
          });
        return new Response(
          JSON.stringify({
            workspace_name: "Test",
            state: "updating",
            phase: "ingesting",
            counts: {},
            revision: 5,
            published_revision: 1,
          }),
        );
      }
      return new Response('{"items":[]}');
    }),
  );
  vi.useFakeTimers();
  try {
    render(App);
    await vi.advanceTimersByTimeAsync(0);
    const prompt = screen.getByLabelText(
      "What would you like to investigate?",
    ) as HTMLTextAreaElement;
    await fireEvent.input(prompt, { target: { value: "Keep my draft" } });
    await vi.advanceTimersByTimeAsync(3000);
    expect(screen.getByText("Status temporarily unavailable")).toBeTruthy();
    expect(screen.getByLabelText("What would you like to investigate?")).toBe(
      prompt,
    );
    expect(prompt.value).toBe("Keep my draft");
  } finally {
    vi.useRealTimers();
  }
});
