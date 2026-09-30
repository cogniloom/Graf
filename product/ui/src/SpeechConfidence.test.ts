import { render, screen } from "@testing-library/svelte";
import { expect, it } from "vitest";
import SpeechConfidence from "./SpeechConfidence.svelte";

it("shows confidence independently of split passage text", () => {
  render(SpeechConfidence, {
    locators: [
      {
        modality: "asr",
        locator: {
          kind: "speech_transcript",
          start_seconds: 1,
          end_seconds: 2,
          confidence: { level: "medium", score: 0.85, withheld: false },
        },
      },
    ],
  });
  expect(screen.getByText("Automatic transcript — unverified")).toBeTruthy();
  expect(screen.getByText(/Confidence: medium/).textContent).toContain("0.850");
  expect(screen.getByText(/Model confidence is not accuracy/)).toBeTruthy();
});

it("marks withheld wording and does not invent a missing score", () => {
  render(SpeechConfidence, {
    locators: [
      {
        modality: "asr",
        locator: {
          confidence: { level: "low", score: null, withheld: true },
        },
      },
    ],
  });
  expect(screen.getByText(/wording withheld from search/)).toBeTruthy();
  expect(screen.queryByText(/decoder score/)).toBeNull();
});

it("does not label ordinary text as speech", () => {
  render(SpeechConfidence, {
    locators: [{ modality: "native", locator: { kind: "text" } }],
  });
  expect(screen.queryByText(/Automatic transcript/)).toBeNull();
});
