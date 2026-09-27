<script lang="ts">
  import { onMount } from "svelte";
  import { api, ApiError, bootstrap, type Status } from "./api";
  import Investigations from "./Investigations.svelte";
  import WorkspaceGraph from "./WorkspaceGraph.svelte";
  import Management from "./Management.svelte";
  import Explore from "./Explore.svelte";
  import Icon from "./Icon.svelte";
  let tab = "Sessions",
    status: Status | null = null,
    tick = 0,
    error = "",
    auth = false,
    loading = true,
    query = "";
  let version = 0,
    alive = true;
  async function refresh() {
    const v = ++version;
    try {
      const s = await api<Status>("/status");
      if (!alive || v !== version) return;
      status = s;
      auth = false;
      error = "";
      tick++;
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
        if (alive && c === connection) void poll(c);
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
    window.addEventListener("hashchange", hashChange);
    void connect();
    return () => {
      alive = false;
      version++;
      connection++;
      clearTimeout(timer);
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
          on:click={() => (tab = name)}
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
            on:input={() => (tab = "Explore")}
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
    {#if status && status.state !== "ready"}<div class="phase-strip">
        <strong>{status.phase}</strong><span>{status.message}</span
        >{#if status.counts.gaps > 0}<span>{status.counts.gaps} gaps</span>{/if}
      </div>{/if}
    <main id="main" tabindex="-1">
      {#if error}<p role="alert" class="notice error">
          {error}
        </p>{/if}{#if loading}<div class="empty" role="status">
          Connecting to your local workspace…
        </div>{:else if auth}<div class="empty">
          <h1>Open your workspace securely</h1>
          <p>Run <code>./graf open</code> to establish a local session.</p>
          <button on:click={refresh}>Check connection</button>
        </div>{:else if !status}<div class="empty">
          <h1>Local server unavailable</h1>
          <p>Check that Graf is running, then reconnect.</p>
          <button on:click={refresh}>Retry connection</button>
        </div>{:else if tab === "Sessions"}<Investigations
          {status}
          {tick}
        />{:else if tab === "Graph"}<WorkspaceGraph
        />{:else if tab === "Explore"}<Explore
          {status}
          {query}
          onSources={() => (tab = "Sources")}
        />{:else}{#key tab}<Management
            view={tab}
            {status}
            {tick}
            {refresh}
            {logout}
          />{/key}{/if}
    </main>
  </div>
</div>
