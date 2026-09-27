<script lang="ts">
  export let name: string;
  const paths: Record<string, string> = {
    Sessions: "M4 4h16v12H8l-4 4V4m4 4h8m-8 4h5",
    Sources: "M6 3h8l4 4v14H6V3m8 0v5h4M9 12h6m-6 4h6",
    Graph: "M12 5v6m-6 7 6-7 6 7M9 3h6v4H9V3M3 17h6v4H3v-4m12 0h6v4h-6v-4",
    Activity: "M3 12h4l3-8 4 16 3-8h4",
    Settings:
      "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2",
    Search: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14m5 12 6 6",
  };
</script>

<svg
  width="22"
  height="22"
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.4"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"><path d={paths[name] || paths.Sources} /></svg
>
