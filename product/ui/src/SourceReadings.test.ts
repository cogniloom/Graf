import { it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import SourceReadings from "./SourceReadings.svelte";
const reading = () => ({
  id: "r1",
  snapshot_id: "s1",
  page: 1,
  frame: null,
  text: "Order 1847 was approved.",
  effective_text: "Order 1847 was approved.",
  method: "native",
  review_status: "automatic_unreviewed",
  review_revision: null,
  words: [],
  claims: [
    {
      id: "c1",
      quote: "Order 1847 was approved.",
      review_status: "automatic_unreviewed",
      review_revision: null,
    },
  ],
  history: [],
  correction_candidates: [],
  metadata: {},
  warnings: [],
});
afterEach(() => vi.unstubAllGlobals());
it("requires image and comparison before confirmation and preserves review history", async () => {
  const fetcher = vi.fn(
    async (_url: string, init?: RequestInit) =>
      new Response(
        JSON.stringify(
          init?.method === "POST"
            ? {
                ...reading(),
                review_status: "confirmed",
                review_revision: "rev1",
                history: [
                  {
                    id: "rev1",
                    target_kind: "reading",
                    target_id: "r1",
                    decision: "confirmed",
                    reviewer: "Alex",
                    reason: "Checked text",
                    correction: "",
                    created_at: 1,
                  },
                ],
              }
            : { item: reading(), total: 1 },
        ),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(SourceReadings, { documentId: "d1", onClose: () => {} });
  await screen.findByText(
    "No word-level OCR scores are available for this reading. Accuracy is unknown.",
  );
  const confirm = screen.getByRole("button", {
    name: "Confirm original text",
  }) as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  await userEvent.type(screen.getByLabelText("Reviewer name"), "Alex");
  await userEvent.type(screen.getByLabelText("Review reason"), "Checked text");
  expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(
    true,
  );
  await fireEvent.load(screen.getByAltText("Original PDF page 1"));
  await userEvent.click(screen.getByRole("checkbox"));
  expect(confirm.disabled).toBe(false);
  expect(
    (
      screen.getByRole("button", {
        name: "Accept interpretation",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await userEvent.click(confirm);
  await screen.findByText(
    "Review saved. Original text and previous reviews are preserved.",
  );
  expect(screen.getByText("Review history (1)")).toBeTruthy();
  const sent = JSON.parse(
    fetcher.mock.calls.find((c) => c[1]?.method === "POST")![1]!.body as string,
  );
  expect(sent).toMatchObject({
    snapshot_id: "s1",
    expected_revision: null,
    source_checked: true,
    target_kind: "reading",
    decision: "confirmed",
  });
});
it("keeps confirmation disabled when image fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () => new Response(JSON.stringify({ item: reading(), total: 1 })),
    ),
  );
  render(SourceReadings, { documentId: "d1", onClose: () => {} });
  await fireEvent.error(await screen.findByAltText("Original PDF page 1"));
  expect(screen.getByRole("alert").textContent).toContain(
    "Page image unavailable",
  );
  expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(
    true,
  );
});
it("shows corrections without replacing original quotations", async () => {
  const item = {
    ...reading(),
    review_status: "corrected",
    effective_text: "Order 1847 was not approved.",
    claims: [{ ...reading().claims[0], review_status: "needs_reassessment" }],
    correction_candidates: [
      {
        id: "kc1",
        quote: "Order 1847 was not approved.",
        review_status: "automatic_unreviewed",
        review_revision: null,
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ item, total: 1 }))),
  );
  render(SourceReadings, { documentId: "d1", onClose: () => {} });
  await screen.findByText("Current corrected reading");
  expect(screen.getByText("needs reassessment")).toBeTruthy();
  expect(
    screen.getAllByText("Order 1847 was approved.").length,
  ).toBeGreaterThan(0);
});
it("discards late responses after closing", async () => {
  let resolve!: (r: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>((r) => (resolve = r))),
  );
  const { unmount } = render(SourceReadings, {
    documentId: "d1",
    onClose: () => {},
  });
  unmount();
  resolve(new Response(JSON.stringify({ item: reading(), total: 1 })));
  await waitFor(() =>
    expect(screen.queryByText("Original extracted text")).toBeNull(),
  );
});

it("highlights the source coordinates of a flagged OCR word", async () => {
  const item = {
    ...reading(),
    method: "ocr",
    words: [
      {
        text: "approved",
        start: 15,
        end: 23,
        bbox: [10, 20, 30, 40],
        score: 12,
      },
    ],
    metadata: { image_width: 300, image_height: 500 },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ item, total: 1 }))),
  );
  const { container } = render(SourceReadings, {
    documentId: "d1",
    onClose: () => {},
  });
  await userEvent.click(await screen.findByText("Words to check (1)"));
  await userEvent.click(screen.getByRole("button", { name: "approved · 12" }));
  const rect = container.querySelector("rect")!;
  expect(rect.getAttribute("x")).toBe("10");
  expect(rect.getAttribute("width")).toBe("30");
  expect(container.querySelector("svg")!.getAttribute("viewBox")).toBe(
    "0 0 300 500",
  );
});
