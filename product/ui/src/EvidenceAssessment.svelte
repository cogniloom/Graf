<script lang="ts">
  import { onDestroy } from "svelte";
  import { api } from "./api";
  import type { Detail, Review, ReviewTarget } from "./investigations";
  export let detail: Detail;
  export let onSaved: () => void = () => {};
  let targets: ReviewTarget[] = [];
  let next: number | null = 0;
  let total = 0;
  let target: ReviewTarget | null = null;
  let decision = "clarify",
    reason = "",
    correction = "",
    error = "",
    saved = "";
  let busy = false,
    loading = false,
    version = 0,
    alive = true;
  let runId = "";
  let expectedReview: string | null = null;
  $: restricted = ["erased", "deleting"].includes(detail.state);
  $: canReview = ["completed", "awaiting_input"].includes(detail.state);
  $: if (runId !== detail.id || restricted) {
    runId = detail.id;
    version++;
    targets = [];
    target = null;
    next = 0;
    total = 0;
    reason = "";
    correction = "";
    error = "";
    saved = "";
    busy = false;
    loading = false;
  }
  $: current = detail.reviews?.find(
    (r) =>
      r.current &&
      r.target_kind === target?.target_kind &&
      r.target_id === target?.target_id,
  );
  onDestroy(() => {
    alive = false;
    version++;
  });
  async function loadTargets() {
    if (loading || next === null || restricted) return;
    const generation = version,
      id = detail.id;
    loading = true;
    error = "";
    try {
      const page = await api<{
        items: ReviewTarget[];
        next_offset: number | null;
        total: number;
      }>(`/investigations/${id}/review-targets?offset=${next}`);
      if (!alive || version !== generation) return;
      targets = [...targets, ...page.items];
      next = page.next_offset;
      total = page.total;
    } catch (e) {
      if (alive && version === generation) error = (e as Error).message;
    } finally {
      if (alive && version === generation) loading = false;
    }
  }
  const kinds: Record<string, string> = {
    knowledge: "Extracted interpretation",
    segment: "Passage",
    link: "Relationship",
    conclusion: "Conclusion",
  };
  function label(item: ReviewTarget) {
    const value =
      item.record.quote ||
      item.record.text ||
      item.record.relation ||
      item.record.type ||
      item.sources[0]?.text ||
      item.target_id;
    return `${kinds[item.target_kind] || item.target_kind}: ${String(value).slice(0, 140)}`;
  }
  function choose(item: ReviewTarget) {
    target = item;
    expectedReview =
      detail.reviews?.find(
        (r) =>
          r.current &&
          r.target_kind === item.target_kind &&
          r.target_id === item.target_id,
      )?.id || null;
    reason = "";
    correction = "";
    error = "";
    saved = "";
    decision = "clarify";
  }
  async function save() {
    if (
      !target ||
      busy ||
      !canReview ||
      (current?.id || null) !== expectedReview
    )
      return;
    const generation = version,
      id = detail.id;
    busy = true;
    error = "";
    saved = "";
    try {
      const review = await api<Review>(`/investigations/${id}/reviews`, {
        method: "POST",
        body: JSON.stringify({
          target_kind: target.target_kind,
          target_id: target.target_id,
          decision,
          reason,
          correction,
          supersedes: expectedReview,
        }),
      });
      if (!alive || generation !== version) return;
      saved = `Review recorded in ledger entry ${review.ledger_seq}.`;
      reason = "";
      correction = "";
      target = null;
      onSaved();
    } catch (e) {
      if (alive && generation === version) error = (e as Error).message;
    } finally {
      if (alive && generation === version) busy = false;
    }
  }
</script>

{#if !restricted}
  <section class="assessment" aria-label="Evidence assessment">
    <h2>Evidence assessment</h2>
    <p class="muted">
      A matching quotation verifies the wording. It does not establish that a
      conclusion is true.
    </p>
    {#each detail.guidance?.messages || [] as message}
      <div class="notice">
        <strong>{message.message}</strong>
        <p>{message.action}</p>
      </div>
    {/each}
    {#if detail.guidance?.document_gaps?.length}
      <details>
        <summary
          >Documents needing attention ({detail.guidance.document_gaps_total_is_lower_bound ? "at least " : ""}{detail.guidance.document_gaps_total ??
            detail.guidance.document_gaps.length})</summary
        >
        {#each detail.guidance.document_gaps as gap}
          <p><strong>{gap.path}</strong> · {gap.status}</p>
          <ul>
            {#each gap.warnings || [] as warning}<li>{warning}</li>{/each}
          </ul>
        {/each}
        {#if (detail.guidance.document_gaps_total || 0) > detail.guidance.document_gaps.length}<p
          >
            Showing {detail.guidance.document_gaps.length} affected documents.
            Sources lists the remaining processing issues.
          </p>{/if}
      </details>
    {/if}
    {#each detail.result.conclusions || [] as conclusion}
      <article class="conclusion">
        <p class="list-label">{conclusion.status} · {conclusion.id}</p>
        <h3>{conclusion.text}</h3>
        {#each [{ label: "Supporting passages", items: conclusion.supporting }, { label: "Contrary passages", items: conclusion.contrary }] as group}
          {#if group.items.length}<h4>{group.label}</h4>{/if}
          {#each group.items as citation}<blockquote>
              {citation.quote}
              <footer><code>{citation.segment_id}</code></footer>
            </blockquote>{/each}
        {/each}
        {#if conclusion.assumptions.length}<h4>Assumptions</h4>
          <ul>
            {#each conclusion.assumptions as assumption}<li>
                {assumption}
              </li>{/each}
          </ul>{/if}
        {#if conclusion.gaps.length}<h4>What remains unresolved</h4>
          <ul>
            {#each conclusion.gaps as gap}<li>{gap}</li>{/each}
          </ul>{/if}
      </article>
    {:else}
      {#if ["completed", "awaiting_input"].includes(detail.state)}<p>
          No structured conclusion assessment was retained for this answer.
          Inspect its citations and coverage before relying on it.
        </p>{/if}
    {/each}
    <details>
      <summary>Inspect and review interpretations</summary>
      <p>
        Review a passage, extracted interpretation, relationship, or conclusion.
        Your decision is an attributed human assertion. Originals stay
        available, and every change is recorded in the ledger.
      </p>
      <button on:click={loadTargets} disabled={loading || next === null}
        >{loading
          ? "Loading interpretations…"
          : targets.length
            ? "Load more interpretations"
            : "Load interpretations"}</button
      >
      {#if targets.length}<p>
          {targets.length} of {total} retained interpretations
        </p>{/if}
      <div class="target-list">
        {#each targets as item}<button
            disabled={busy}
            aria-pressed={target?.target_kind === item.target_kind &&
              target?.target_id === item.target_id}
            on:click={() => choose(item)}>{label(item)}</button
          >{/each}
      </div>
      {#if target}
        <h3>{kinds[target.target_kind] || target.target_kind}</h3>
        <p>
          Source-bound record; a connection alone does not establish identity,
          causation, or independent corroboration.
        </p>
        <dl>
          {#each [["Condition", target.record.condition], ["Modality", target.record.modality], ["Polarity", target.record.polarity], ["Interpretation status", target.record.epistemic_status]] as [name, value]}
            {#if typeof value === "string" && value}<dt>{name}</dt>
              <dd>{value}</dd>{/if}
          {/each}
        </dl>
        <details>
          <summary>Full source-bound record</summary>
          <pre>{JSON.stringify(target.record, null, 2)}</pre>
        </details>
        <h4>Source evidence</h4>
        {#each target.sources as source}<blockquote>
            {source.text || source.quote}
            <footer>
              {source.source_path || source.segment_id || source.id}
            </footer>
          </blockquote>{:else}<p>
            No passage reference was retained for this relationship. Its meaning
            remains unresolved.
          </p>{/each}
        {#if current}<p>
            Latest decision: {current.decision} by {current.actor}. Saving
            creates a new ledger entry that supersedes it.
          </p>{/if}
        {#if (current?.id || null) !== expectedReview}<p role="alert">
            This review changed while you were editing. Select the
            interpretation again before saving.
          </p>{/if}
        <form on:submit|preventDefault={save}>
          <label
            >Review decision<select
              bind:value={decision}
              disabled={busy || !canReview}
              ><option value="clarify"
                >Clarify interpretation or identity</option
              ><option value="confirm">Confirm my interpretation</option><option
                value="reject">Reject interpretation</option
              >{#if current?.effective}<option value="retract"
                  >Retract my previous decision</option
                >{/if}</select
            ></label
          >
          <label
            >Reason for this decision<textarea
              required
              maxlength="4000"
              bind:value={reason}
              disabled={busy || !canReview}
            ></textarea></label
          >
          <label
            >Correction or clarification<textarea
              required={decision === "clarify"}
              maxlength="8000"
              bind:value={correction}
              disabled={busy || !canReview}
            ></textarea></label
          >
          <button
            disabled={busy ||
              (current?.id || null) !== expectedReview ||
              !canReview ||
              !reason.trim() ||
              (decision === "clarify" && !correction.trim())}
            >Record review in ledger</button
          >
        </form>
      {/if}
      {#if error}<p class="notice error" role="alert">{error}</p>{/if}
      {#if saved}<p role="status">{saved}</p>{/if}
    </details>
    {#if detail.supplied_reviews?.length}
      <details>
        <summary
          >Earlier human assertions supplied to this answer ({detail
            .supplied_reviews.length})</summary
        >
        <p>
          These are the attributed assertions retained when this run was
          prepared. They are not documentary facts or a live view of later
          corrections.
        </p>
        {#each detail.supplied_reviews as review}<article>
            <p><strong>{review.decision}</strong>: {review.reason}</p>
            {#if review.correction}<p>{review.correction}</p>{/if}
            <p class="muted">
              {review.actor} · {review.time} · Ledger entry {review.ledger_seq}
            </p>
          </article>{/each}
        {#if detail.supplied_reviews_omitted}<p>
            {detail.supplied_reviews_omitted} earlier assertions were omitted for
            the context budget.
          </p>{/if}
      </details>
    {/if}
    {#if detail.reviews?.length}<details>
        <summary>Attributed review history ({detail.reviews.length})</summary>
        {#each detail.reviews as review}<article>
            <p>
              <strong>{review.decision}</strong> · {review.target_kind} · {review.target_id}
              · {review.current
                ? review.effective
                  ? "Current assertion"
                  : "Retracted"
                : "Superseded"}
            </p>
            <p>{review.reason}</p>
            {#if review.correction}<p>{review.correction}</p>{/if}
            <p class="muted">
              {review.actor} · {review.time} · Ledger entry {review.ledger_seq}
            </p>
          </article>{/each}
      </details>{/if}
  </section>
{/if}

<style>
  .assessment {
    scroll-margin-top: 4rem;
    margin: 1.5rem 0;
  }
  .conclusion {
    border-top: 1px solid var(--border, #ddd);
    padding: 1rem 0;
  }
  .target-list {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin: 1rem 0;
  }
  .target-list button {
    max-width: 100%;
    overflow-wrap: anywhere;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 24rem;
    overflow: auto;
  }
  blockquote {
    margin: 1rem 0;
    padding-left: 1rem;
    border-left: 3px solid #879786;
  }
  footer,
  code {
    overflow-wrap: anywhere;
  }
  form {
    display: grid;
    gap: 0.8rem;
  }
  label {
    display: grid;
    gap: 0.4rem;
  }
  textarea,
  select {
    width: 100%;
    box-sizing: border-box;
  }
</style>
