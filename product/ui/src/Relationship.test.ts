import { it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import Relationship from "./Relationship.svelte";
it("shows source-backed relationships and navigates the connected document", async () => {
  const onSelect = vi.fn();
  render(Relationship, {
    record: {
      relation_type: "EMAIL_REPLY_REFERENCE",
      status: "resolved",
      from_node: "d1",
      to_node: "d2",
      derivation_json: JSON.stringify({
        observed_value: "approval@example.test",
        basis: "header_observation",
      }),
    },
    documentId: "d1",
    names: new Map([["d2", "/source/approval.eml"]]),
    onSelect,
  });
  expect(screen.getByText("email reply reference")).toBeTruthy();
  expect(screen.getByText("approval@example.test")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "approval.eml" }));
  expect(onSelect).toHaveBeenCalledWith("d2");
  expect(screen.getByText("Evidence record").closest("details")?.open).toBe(
    false,
  );
});
it("handles malformed derivation without inventing reference data", () => {
  render(Relationship, {
    record: {
      relation_type: "EXPLICIT_DOCUMENT_REFERENCE",
      status: "unresolved",
      derivation_json: "not JSON",
    },
    documentId: "d1",
    names: new Map(),
    onSelect: () => {},
  });
  expect(screen.getByText("Reference details could not be read.")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});
