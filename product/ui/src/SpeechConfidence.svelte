<script lang="ts">
  export let locators: unknown;
  type SpeechLocator = {
    start_seconds?: number;
    end_seconds?: number;
    confidence?: { level?: string; score?: number | null; withheld?: boolean };
  };
  $: speech = (Array.isArray(locators) ? locators : [])
    .filter(
      (entry) =>
        entry?.modality === "asr" ||
        entry?.locator?.kind === "speech_transcript",
    )
    .map((entry) => entry.locator as SpeechLocator);
  function level(value: string | undefined) {
    return value === "high" || value === "medium" || value === "low"
      ? value
      : "unavailable";
  }
</script>

{#if speech.length}
  <div class="notice" aria-label="Speech transcription confidence">
    <strong>Automatic transcript — unverified</strong>
    <p>
      Model confidence is not accuracy. Check the original audio before relying
      on these words.
    </p>
    {#each speech as location}
      <p>
        {#if Number.isFinite(location?.start_seconds) && Number.isFinite(location?.end_seconds)}
          {location.start_seconds!.toFixed(2)}–{location.end_seconds!.toFixed(
            2,
          )}s ·
        {/if}
        Confidence: {level(location?.confidence?.level)}
        {#if typeof location?.confidence?.score === "number" && Number.isFinite(location.confidence.score)}
          · decoder score {location.confidence.score.toFixed(3)}
        {/if}
        {#if location?.confidence?.withheld}{" "}· wording withheld from search{/if}
      </p>
    {/each}
  </div>
{/if}
