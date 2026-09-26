import { it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sources } from "./Management";
it("shows actual errors and requires explicit source removal confirmation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
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
    ),
  );
  const mutate = vi.fn().mockResolvedValue(true);
  render(<Sources tick={0} mutate={mutate} busy={false} />);
  expect(await screen.findByText("File disappeared")).toBeTruthy();
  await userEvent.click(screen.getByLabelText("Remove /allowed/test.txt"));
  expect(mutate).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText("Cancel"));
  expect(mutate).not.toHaveBeenCalled();
  await userEvent.click(screen.getByLabelText("Remove /allowed/test.txt"));
  await userEvent.click(screen.getByRole("button", { name: "Remove source" }));
  await waitFor(() =>
    expect(mutate).toHaveBeenCalledWith("/sources/s1", { method: "DELETE" }),
  );
  vi.unstubAllGlobals();
});
