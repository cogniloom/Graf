<script lang="ts">
  export let record: unknown;
  export let documentId: string;
  export let names: Map<string, string>;
  export let onSelect: (id: string) => void;
  function object(v: unknown): Record<string, unknown> {
    return v !== null && typeof v === "object" && !Array.isArray(v)
      ? (v as Record<string, unknown>)
      : {};
  }
  function parse(r: unknown) {
    const item = object(r);
    let derivation = object(item.derivation_json),
      invalid = false;
    if (typeof item.derivation_json === "string") {
      try {
        derivation = object(JSON.parse(item.derivation_json));
      } catch {
        invalid = true;
      }
    }
    return { item, derivation, invalid };
  }
  $: ({ item, derivation, invalid } = parse(record));
  $: type =
    typeof item.relation_type === "string"
      ? item.relation_type
      : typeof item.type === "string"
        ? item.type
        : "Recorded relationship";
  $: from =
    typeof item.from_node === "string"
      ? item.from_node
      : typeof item.source === "string"
        ? item.source
        : undefined;
  $: to =
    typeof item.to_node === "string"
      ? item.to_node
      : typeof item.target === "string"
        ? item.target
        : undefined;
  $: connected = from === documentId ? to : (from ?? to);
  $: label = connected ? names.get(connected) || connected : "";
  $: observed =
    typeof derivation.observed_value === "string"
      ? derivation.observed_value
      : typeof derivation.literal === "string"
        ? derivation.literal
        : null;
</script>

<article class="relationship-record">
  <div class="row between">
    <h4>{type.replaceAll("_", " ").toLowerCase()}</h4>
    {#if typeof item.status === "string"}<span class="muted">{item.status}</span
      >{/if}
  </div>
  {#if observed}<p>
      <small>Observed reference</small><br /><strong>{observed}</strong>
    </p>{/if}{#if typeof derivation.basis === "string"}<p class="muted">
      Basis: {derivation.basis.replaceAll("_", " ")}
    </p>{/if}{#if connected && connected !== documentId}<button
      class="text-button"
      on:click={() => onSelect(connected!)}
      >{label.startsWith("/") ? label.split("/").at(-1) : label}</button
    >{:else}<p class="muted">
      No resolved document returned.
    </p>{/if}{#if Array.isArray(derivation.does_not_establish)}<p class="muted">
      Does not establish: {derivation.does_not_establish
        .filter((x) => typeof x === "string")
        .join(", ")}.
    </p>{/if}{#if invalid}<p class="notice">
      Reference details could not be read.
    </p>{/if}
  <details>
    <summary>Evidence record</summary>
    <pre>{JSON.stringify(record, null, 2)}</pre>
  </details>
</article>
