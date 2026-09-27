import { it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import App from "./App.svelte";
afterEach(() => vi.unstubAllGlobals());
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
