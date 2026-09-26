import { describe, it, expect, vi, afterEach } from "vitest";
import { api, bootstrap, isReady, Status, ApiError } from "./api";
afterEach(() => vi.unstubAllGlobals());
describe("local API boundary", () => {
  it("uses same-origin cookie credentials and surfaces server rejection", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Path outside allowed roots" }), {
        status: 403,
      }),
    );
    vi.stubGlobal("fetch", fetch);
    await expect(
      api("/sources", { method: "POST", body: "{}" }),
    ).rejects.toThrow("Path outside allowed roots");
    expect(fetch.mock.calls[0][1].credentials).toBe("include");
  });
  it("removes bootstrap secret before posting and never places it in request URL", async () => {
    history.replaceState(null, "", "/#token=synthetic-test-token");
    const fetch = vi.fn().mockImplementation(async () => {
      expect(location.hash).toBe("");
      return new Response("{}");
    });
    vi.stubGlobal("fetch", fetch);
    await bootstrap();
    expect(fetch.mock.calls[0][0]).toBe("/api/session");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      token: "synthetic-test-token",
    });
  });
  it("gates unpublished and blocked evidence", () => {
    const status = {
      state: "ready",
      revision: 2,
      published_revision: 1,
    } as Status;
    expect(isReady(status)).toBe(false);
    expect(isReady({ ...status, published_revision: 2 })).toBe(true);
    expect(
      isReady({ ...status, state: "blocked", published_revision: 2 }),
    ).toBe(false);
    expect(
      isReady({ ...status, state: "ready_with_gaps", published_revision: 2 }),
    ).toBe(true);
  });
  it("preserves unauthorized status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 401 })),
    );
    await expect(api("/status")).rejects.toBeInstanceOf(ApiError);
  });
});
