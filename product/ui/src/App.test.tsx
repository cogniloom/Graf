import { it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { App } from "./App";
vi.mock("./Explore", () => ({
  Explore: () => <div>Authenticated explore view</div>,
}));
it("consumes a new bootstrap fragment on an already mounted unauthorized page", async () => {
  let authenticated = false;
  history.replaceState(null, "", "/");
  const fetch = vi.fn(async (url: string) => {
    if (url === "/api/session") {
      expect(location.hash).toBe("");
      authenticated = true;
      return new Response("{}");
    }
    return new Response(
      JSON.stringify(
        authenticated
          ? {
              workspace_name: "Test",
              state: "empty",
              counts: { documents: 0, passages: 0, connections: 0 },
              revision: 1,
              published_revision: 0,
            }
          : { detail: "Authentication required" },
      ),
      { status: authenticated ? 200 : 401 },
    );
  });
  vi.stubGlobal("fetch", fetch);
  render(<App />);
  await screen.findByText("Open your workspace securely");
  location.hash = "token=synthetic-mounted-page-token";
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  await screen.findByText("Authenticated explore view");
  await waitFor(() => expect(location.hash).toBe(""));
  expect(
    fetch.mock.calls.filter(([url]) => url === "/api/session"),
  ).toHaveLength(1);
  vi.unstubAllGlobals();
});
