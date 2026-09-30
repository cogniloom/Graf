<script lang="ts">
  import { onMount } from "svelte";
  import { api, ApiError, bootstrap, type Status } from "./api";
  import Investigations from "./Investigations.svelte";
  import WorkspaceGraph from "./WorkspaceGraph.svelte";
  import Management from "./Management.svelte";
  import Explore from "./Explore.svelte";
  import Icon from "./Icon.svelte";
  import ProcessingStatus from "./ProcessingStatus.svelte";
  const pages = [
    "Sessions",
    "Sources",
    "Graph",
    "Activity",
    "Settings",
    "Explore",
  ];
  function pageFromUrl() {
    const page = new URLSearchParams(location.search).get("page");
    return pages.find((name) => name.toLowerCase() === page) ?? "Sessions";
  }
  let tab = pageFromUrl(),
    status: Status | null = null,
    tick = 0,
    error = "",
    auth = false,
    loading = true,
    query = new URLSearchParams(location.search).get("q") ?? "";
  let connected = false;
  $: viewStatus = status ?? {
    workspace_name: "Local workspace",
    state: "unknown",
    phase: "connecting",
    revision: -1,
    published_revision: -2,
    snapshot_id: null,
    counts: {},
    message:
      "Collection status is unavailable. Sources, settings, and saved sessions remain accessible.",
    models_ready: false,
    device: "",
  };
  function navigate(page: string, replace = false) {
    tab = page;
    const url = new URL(location.href);
    url.searchParams.set("page", page.toLowerCase());
    if (query) url.searchParams.set("q", query);
    else url.searchParams.delete("q");
    if (url.href !== location.href)
      history[replace ? "replaceState" : "pushState"](null, "", url);
  }
  function restorePage() {
    tab = pageFromUrl();
    query = new URLSearchParams(location.search).get("q") ?? "";
  }
  let version = 0,
    alive = true;
  async function refresh() {
    const v = ++version;
    try {
      const s = await api<Status>("/status");
      if (!alive || v !== version) return;
      const running = s.jobs?.find(
        (job) => job.revision === s.revision && job.state === "running",
      );
      status =
        running && !["ready", "ready_with_gaps"].includes(s.state)
          ? {
              ...s,
              state: "updating",
              phase: running.phase,
              message:
                "Processing sources in the background. Saved sessions and workspace controls remain available.",
            }
          : s;
      auth = false;
      error = "";
    } catch (e) {
      if (!alive || v !== version) return;
      status = null;
      error = (e as Error).message;
      auth = e instanceof ApiError && e.status === 401;
    } finally {
      if (alive && v === version) loading = false;
    }
  }
  onMount(() => {
    let timer: ReturnType<typeof setTimeout>,
      connection = 0;
    async function poll(c: number) {
      await refresh();
      if (alive && c === connection) timer = setTimeout(() => poll(c), 3000);
    }
    async function connect() {
      const c = ++connection;
      version++;
      clearTimeout(timer);
      loading = true;
      try {
        await bootstrap();
        if (alive && c === connection) {
          connected = true;
          auth = false;
          void poll(c);
        }
      } catch (e) {
        if (alive && c === connection) {
          status = null;
          auth = true;
          error = (e as Error).message;
          loading = false;
        }
      }
    }
    function hashChange() {
      if (new URLSearchParams(location.hash.slice(1)).has("token"))
        void connect();
    }
    const pageTimer = setInterval(() => {
      if (connected && !auth) tick++;
    }, 3000);
    window.addEventListener("popstate", restorePage);
    window.addEventListener("hashchange", hashChange);
    void connect();
    return () => {
      alive = false;
      version++;
      connection++;
      clearTimeout(timer);
      clearInterval(pageTimer);
      window.removeEventListener("popstate", restorePage);
      window.removeEventListener("hashchange", hashChange);
    };
  });
  async function logout() {
    try {
      await api("/logout", { method: "POST" });
      version++;
      status = null;
      auth = true;
    } catch (e) {
      error = (e as Error).message;
    }
  }
</script>

<div class="app-shell">
  <a class="skip" href="#main">Skip to content</a>
  <aside class="rail">
    <a href="/" class="brand" aria-label="Graf home">Graf</a>
    <nav aria-label="Main navigation">
      {#each ["Sessions", "Sources", "Graph", "Activity", "Settings"] as name}<button
          class:active={tab === name}
          aria-current={tab === name ? "page" : undefined}
          on:click={() => navigate(name)}
          ><Icon {name} /><span>{name}</span></button
        >{/each}
    </nav>
    <div class="rail-footer"><i class="local-dot"></i><span>LOCAL</span></div>
  </aside>
  <div class="workspace-shell">
    <header class="workspace-bar">
      <div class="workspace-name">
        {status?.workspace_name || "Local workspace"}
      </div>
      <div class="workspace-tools">
        <label class="global-search"
          ><Icon name="Search" /><input
            aria-label="Search document names"
            placeholder="Find a document…"
            bind:value={query}
            on:input={() => navigate("Explore", tab === "Explore")}
          /></label
        ><span class="workspace-readiness" role="status"
          ><i
            class="state-dot"
            class:running={!!status &&
              !["ready", "ready_with_gaps", "empty"].includes(status.state)}
          ></i>{loading
            ? "Connecting"
            : auth
              ? "Locked"
              : status
                ? status.state.replaceAll("_", " ")
                : "Offline"}</span
        >
      </div>
    </header>
    {#if status?.knowledge_restart_required}<p class="notice">
        Knowledge processing changed while Graf was running. Let any active scan
        finish, then restart Graf to finish applying the update.
      </p>{/if}
    {#if status && status.state !== "ready"}<ProcessingStatus
        {status}
        onActivity={() => navigate("Activity")}
        onSources={() => navigate("Sources")}
      />{/if}
    <main id="main" tabindex="-1">
      {#if error}<p role="alert" class="notice error">
          {error}
        </p>{/if}
      {#if connected && loading}<p class="notice" role="status">
          Checking collection status…
        </p>{/if}
      {#if connected && !status && !loading && !auth}<p class="notice">
          Collection status is unavailable. You can still browse saved sessions
          and manage your workspace.
          <button on:click={refresh}>Retry status</button>
        </p>{/if}
      {#if !connected && loading}<div class="empty" role="status">
          Connecting to your local workspace…
        </div>{:else if auth}<div class="empty">
          <h1>Open your workspace securely</h1>
          <p>Run <code>./graf open</code> to establish a local session.</p>
          <button on:click={refresh}>Check connection</button>
        </div>{:else if tab === "Sessions"}<Investigations
          status={viewStatus}
          {tick}
        />{:else if tab === "Graph"}<WorkspaceGraph
        />{:else if tab === "Explore"}<Explore
          status={viewStatus}
          {query}
          onSources={() => navigate("Sources")}
        />{:else}{#key tab}<Management
            view={tab}
            status={viewStatus}
            {tick}
            {refresh}
            {logout}
          />{/key}{/if}
    </main>
  </div>
</div>
