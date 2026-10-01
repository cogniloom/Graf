<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { api } from "./api";
  export let documentId: string;
  export let onClose: () => void;
  type Review = {
    id: string;
    target_kind: string;
    target_id: string;
    decision: string;
    reviewer: string;
    reason: string;
    correction: string;
    created_at: number;
  };
  type Word = {
    text: string;
    start: number;
    end: number;
    bbox: number[];
    score: number | null;
  };
  type Claim = {
    id: string;
    quote: string;
    review_status: string;
    review_revision: string | null;
  };
  type Reading = {
    id: string;
    snapshot_id: string;
    page: number | null;
    frame: number | null;
    text: string;
    effective_text: string;
    method: string;
    review_status: string;
    review_revision: string | null;
    words: Word[];
    claims: Claim[];
    history: Review[];
    correction_candidates: Claim[];
    metadata: { image_width?: number; image_height?: number };
    warnings: string[];
  };
  let dialog: HTMLDialogElement;
  let item: Reading | null = null;
  let offset = 0,
    total = 0,
    version = 0;
  let error = "",
    message = "",
    reason = "",
    reviewer = "",
    correction = "";
  let busy = false,
    imageReady = false,
    imageError = false,
    checked = false;
  let selectedWord: Word | null = null;
  let loading = true,
    zoom = false;
  let alive = true;
  onMount(() => {
    dialog.showModal();
    load(0);
  });
  onDestroy(() => {
    alive = false;
    version++;
  });
  $: imageUrl = item
    ? `/api/documents/${encodeURIComponent(documentId)}/readings/${encodeURIComponent(item.id)}/image?snapshot_id=${encodeURIComponent(item.snapshot_id)}`
    : "";
  $: flagged = (item?.words || []).filter(
    (w) => w.score === null || w.score < 60,
  );
  $: canSave =
    !busy && imageReady && checked && !!reason.trim() && !!reviewer.trim();
  function label(value: string) {
    return value.replaceAll("_", " ");
  }
  async function load(page: number) {
    const v = ++version;
    offset = page;
    loading = true;
    zoom = false;
    item = null;
    error = "";
    message = "";
    imageReady = false;
    imageError = false;
    checked = false;
    selectedWord = null;
    reason = "";
    try {
      const data = await api<{ item: Reading | null; total: number }>(
        `/documents/${encodeURIComponent(documentId)}/readings?offset=${page}`,
      );
      if (!alive || v !== version) return;
      item = data.item;
      total = data.total;
      correction = item?.effective_text || "";
    } catch (e) {
      if (alive && v === version) error = (e as Error).message;
    } finally {
      if (alive && v === version) loading = false;
    }
  }
  async function save(decision: string, claim?: Claim) {
    if (!item || !canSave) return;
    const v = version;
    busy = true;
    error = "";
    message = "";
    try {
      const updated = await api<Reading>(
        `/documents/${encodeURIComponent(documentId)}/readings/${encodeURIComponent(item.id)}/reviews`,
        {
          method: "POST",
          body: JSON.stringify({
            snapshot_id: item.snapshot_id,
            target_kind: claim ? "claim" : "reading",
            target_id: claim?.id || item.id,
            decision,
            reason,
            reviewer,
            correction: decision === "corrected" ? correction : "",
            expected_revision: claim
              ? claim.review_revision
              : item.review_revision,
            reading_revision: item.review_revision,
            source_checked: checked,
          }),
        },
      );
      if (!alive || v !== version) return;
      item = updated;
      correction = updated.effective_text;
      reason = "";
      checked = false;
      message =
        "Review saved. Original text and previous reviews are preserved.";
    } catch (e) {
      if (alive && v === version) error = (e as Error).message;
    } finally {
      if (alive && v === version) busy = false;
    }
  }
</script>

<dialog bind:this={dialog} on:close={onClose} aria-labelledby="reading-title">
  <header class="reading-header">
    <div>
      <h2 id="reading-title">Review source reading</h2>
      <p>
        Check what the page says. This does not verify that its statements are
        true.
      </p>
    </div>
    <button aria-label="Close reading review" on:click={() => dialog.close()}
      >Close</button
    >
  </header>
  {#if error}<p class="notice error" role="alert">
      {error}
      <button disabled={busy} on:click={() => load(offset)}
        >Reload reading</button
      >
    </p>{/if}
  {#if message}<p role="status" class="notice">{message}</p>{/if}
  {#if item}
    <nav aria-label="Reading pages" class="reading-nav">
      <button disabled={busy || offset === 0} on:click={() => load(offset - 1)}
        >Previous page</button
      >
      <strong
        >{item.page ? `Page ${item.page}` : `Image ${(item.frame || 0) + 1}`} · {offset +
          1} of {total}</strong
      >
      <button
        disabled={busy || offset + 1 >= total}
        on:click={() => load(offset + 1)}>Next page</button
      >
    </nav>
    <p>
      <strong>{label(item.review_status)}</strong> · {item.method === "native"
        ? "PDF text layer (may contain earlier OCR)"
        : "OCR reading"}
    </p>
    <div class="reading-grid">
      <section aria-label="Original page">
        <h3>Original page</h3>
        <button aria-pressed={zoom} on:click={() => (zoom = !zoom)}
          >{zoom ? "Fit page" : "Zoom page"}</button
        >
        {#if imageError}<p role="alert">
            Page image unavailable. Reload the reading to retry; confirmation is
            disabled.
          </p>{/if}
        <div class="image-scroll">
          <div class="page-image" style:width={zoom ? "200%" : "100%"}>
            <img
              src={imageUrl}
              alt={item.page
                ? `Original PDF page ${item.page}`
                : "Original source image"}
              on:load={() => (imageReady = true)}
              on:error={() => {
                imageError = true;
                imageReady = false;
              }}
            />
            {#if selectedWord && item.metadata.image_width && item.metadata.image_height}
              <svg
                aria-hidden="true"
                viewBox={`0 0 ${item.metadata.image_width} ${item.metadata.image_height}`}
                ><rect
                  x={selectedWord.bbox[0]}
                  y={selectedWord.bbox[1]}
                  width={selectedWord.bbox[2]}
                  height={selectedWord.bbox[3]}
                /></svg
              >
            {/if}
          </div>
        </div>
      </section>
      <section aria-label="Text reading">
        <h3>Original extracted text</h3>
        <pre class="reading-text">{item.text ||
            "No readable text was extracted."}</pre>
        <p class="muted">
          Unreviewed text can help find evidence. Check important names,
          amounts, dates and negations against the page.
        </p>
        {#if item.words.length}
          <details>
            <summary>Words to check ({flagged.length})</summary>
            <p>
              Raw OCR scores below 60 or missing scores are review hints, not
              accuracy probabilities. Other words can still be wrong.
            </p>
            {#each flagged.slice(0, 100) as word}<button
                class="word"
                on:click={() => (selectedWord = word)}
                >{word.text} · {word.score === null
                  ? "score unavailable"
                  : word.score.toFixed(0)}</button
              >{/each}
            {#if flagged.length > 100}<p>
                First 100 hints shown. Review the complete text above.
              </p>{/if}
          </details>
        {:else}<p>
            No word-level OCR scores are available for this reading. Accuracy is
            unknown.
          </p>{/if}
        <label
          >Reviewer name <input
            bind:value={reviewer}
            maxlength="200"
            autocomplete="name"
          /></label
        >
        <label
          >Review reason <textarea bind:value={reason} maxlength="4000" rows="2"
          ></textarea></label
        >
        <label class="attestation"
          ><input type="checkbox" bind:checked disabled={!imageReady || busy} />
          I compared this reading with the original page.</label
        >
        <div class="actions">
          <button
            disabled={!canSave || !item.text.trim()}
            on:click={() => save("confirmed")}>Confirm original text</button
          ><button disabled={!canSave} on:click={() => save("unreadable")}
            >Mark unreadable</button
          >
        </div>
        <details>
          <summary>Correct the reading</summary>
          <label
            >New reading <textarea
              bind:value={correction}
              maxlength="16000"
              rows="7"
            ></textarea></label
          >
          <p>
            The original remains unchanged. Dependent claims will need
            reassessment.
          </p>
          <button
            disabled={!canSave ||
              !correction.trim() ||
              correction === item.text}
            on:click={() => save("corrected")}>Save corrected reading</button
          >
        </details>
        {#if item.review_status === "corrected"}<h3>
            Current corrected reading
          </h3>
          <pre class="reading-text">{item.effective_text}</pre>{/if}
      </section>
    </div>
    <section aria-label="Dependent claims">
      <h3>Claims from this reading ({item.claims.length})</h3>
      <p>
        Accepting an interpretation means it matches the confirmed text; it does
        not establish truth. Use the reviewer, reason and page comparison above
        for each decision.
      </p>
      {#each item.claims as claim}<article class="claim-review">
          <blockquote>{claim.quote}</blockquote>
          <p>{label(claim.review_status)}</p>
          <div class="actions">
            <button
              disabled={!canSave || item.review_status !== "confirmed"}
              on:click={() => save("accepted", claim)}
              >Accept interpretation</button
            >
            <button disabled={!canSave} on:click={() => save("rejected", claim)}
              >Reject interpretation</button
            >
            <button
              disabled={!canSave}
              on:click={() => save("unresolved", claim)}
              >Leave unresolved</button
            >
          </div>
        </article>{:else}<p>
          No supported claims were extracted from this page. That does not mean
          the page contains no relevant information.
        </p>{/each}
      {#if item.correction_candidates.length}<h3>
          Reassessed candidates from your correction
        </h3>
        <p>
          These quote the corrected reading, not the original extraction.
          Review each interpretation separately below.
        </p>
        {#each item.correction_candidates as candidate}<article
            class="claim-review"
          >
            <blockquote>{candidate.quote}</blockquote>
            <p>{label(candidate.review_status)}</p>
            <div class="actions">
              <button
                disabled={!canSave}
                on:click={() => save("accepted", candidate)}
                >Accept corrected interpretation</button
              >
              <button
                disabled={!canSave}
                on:click={() => save("rejected", candidate)}
                >Reject corrected interpretation</button
              >
              <button
                disabled={!canSave}
                on:click={() => save("unresolved", candidate)}
                >Leave corrected interpretation unresolved</button
              >
            </div>
          </article>{/each}{/if}
    </section>
    <details>
      <summary>Review history ({item.history.length})</summary
      >{#each item.history as review}<article>
          <strong>{label(review.decision)}</strong> · {review.reviewer} (self-reported)
          · {new Date(review.created_at * 1000).toLocaleString()}
          <p>{review.reason}</p>
          {#if review.correction}<pre
              class="reading-text">{review.correction}</pre>{/if}
        </article>{/each}
    </details>
    <details>
      <summary>Extraction warnings</summary>{#each item.warnings as warning}<p>
          {warning}
        </p>{:else}<p>
          No extraction warning was recorded. This is not an accuracy guarantee.
        </p>{/each}
    </details>
  {:else if !error}<p role="status">
      {loading
        ? "Loading source reading…"
        : "No PDF or image readings are available for this document."}
    </p>{/if}
</dialog>

<style>
  dialog {
    width: min(1200px, 95vw);
    max-width: 95vw;
    box-sizing: border-box;
    max-height: 94vh;
    border: 1px solid #cbd5e1;
    border-radius: 12px;
    padding: 24px;
    color: #16202d;
    background: #fff;
  }
  dialog::backdrop {
    background: #10203099;
  }
  .reading-header,
  .reading-nav,
  .actions {
    display: flex;
    gap: 12px;
    align-items: center;
    justify-content: space-between;
  }
  .reading-header {
    align-items: start;
  }
  .reading-header h2 {
    margin-top: 0;
  }
  .reading-nav {
    margin: 16px 0;
  }
  .reading-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 24px;
  }
  .reading-grid section {
    min-width: 0;
  }
  .page-image {
    position: relative;
  }
  .image-scroll {
    overflow: auto;
    max-height: 70vh;
    margin-top: 12px;
  }
  .page-image img {
    display: block;
    width: 100%;
    height: auto;
    border: 1px solid #cbd5e1;
  }
  .page-image svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }
  rect {
    fill: #fbbf2440;
    stroke: #b45309;
    stroke-width: 3;
  }
  .reading-text {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font: inherit;
    padding: 12px;
    background: #f1f5f9;
    max-height: 360px;
    overflow: auto;
  }
  label {
    display: block;
    margin: 12px 0;
  }
  input:not([type="checkbox"]),
  textarea {
    display: block;
    width: 100%;
    box-sizing: border-box;
  }
  .attestation {
    display: flex;
    align-items: start;
    gap: 8px;
  }
  .attestation input {
    width: auto;
    margin-top: 4px;
  }
  .actions {
    justify-content: start;
    flex-wrap: wrap;
    margin: 12px 0;
  }
  details {
    margin: 16px 0;
  }
  summary {
    cursor: pointer;
  }
  .claim-review {
    border-top: 1px solid #dbe2ea;
    padding: 12px 0;
  }
  .word {
    margin: 4px;
  }
  @media (max-width: 760px) {
    .reading-grid {
      grid-template-columns: 1fr;
    }
    dialog {
      padding: 14px;
    }
    .reading-nav {
      flex-wrap: wrap;
    }
  }
</style>
