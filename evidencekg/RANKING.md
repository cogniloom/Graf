# Ranked discovery and the frozen comparison

`discover` returns a bounded preview of original passages. It supplements the strongest lexical results with query-relevant graph neighbours. Exact lexical/literal search, graph membership pagination, preserved sources and exhaustive source-review queues remain independent of this preview.

```bash
evidencekg --state /local/case-state discover 'What requires written consent?' --limit 12
```

The ranking protects up to six leading lexical passages, considers the strength and rarity of typed connections, loads original explicit-link endpoints including attachments/replies, and uses compact relationship reasons. Weak shared addresses, dates or names do not receive mandatory result slots. Supplemental exact duplicate context is suppressed without deleting source occurrences or treating copies as corroboration. Original wording and negation are retained. Empty-text linked sources remain counted gaps rather than answer passages. Dense locator metadata is previewed with explicit remaining counts and complete snapshot-bound `segment_locators` pagination; primary text is never truncated. Omitted counts describe bounded selection, not exhaustive relevance.

Optional bilingual discovery annotations are stored separately from evidence. They help match German and English concepts, remain attributed model interpretations, and cannot replace original source passages. Building the index is an explicit administrator operation that sends the scheduled source text to the selected subscription model. Ordinary ingestion, mechanical graph construction, search and MCP discovery do not invoke a model.

```bash
# Requires an existing authenticated subscription worker state; no billing fallback.
evidencekg --state /local/case-state index-discovery \
  --output /local/case-discovery \
  --lawcase-project /path/to/docworm \
  --lawcase-worker-state /path/to/docworm/.lawcase

evidencekg --state /local/case-state discover 'What requires written consent?' \
  --discovery-index /local/case-discovery

evidencekg --state /local/case-state serve --transport stdio \
  --discovery-index /local/case-discovery
```

The index is bound to its frozen snapshot. A mismatched or incomplete index is rejected. MCP clients cannot supply arbitrary index paths or initiate index-building cloud calls; the serving administrator selects the index.

The private comparison uses 1,000 sampled original files and 120 frozen source-backed questions: 100 test and 20 development. Baseline answers were captured before optimization. The original automated judge accepted safe refusals as correct, so a separate blinded completion grader evaluates all variants uniformly without regenerating their answers. It distinguishes completed supported answers, abstentions, partial/wrong answers, unsupported answers and unclear references. All questions remain in denominators.

The direct three-way comparison uses lexical retrieval, the original graph ranking, and the optimized mechanical ranking without bilingual annotations. A separate additional experiment combines the optimized ranking with the optional bilingual index; its outcome measures that combined pipeline. Index preparation and model-call costs are reported separately. All variants use the same original source representations, answer model and input budgets. The model receives original passages; graph reasons guide selection rather than being supplied as truth.

The completed direct comparison on 2026-09-26 produced 52/100 supported correct answers without graph, 39/100 with the old ranking, and 69/100 with the new mechanical ranking. Twenty development questions were scored separately. All 120 new-ranking packets and answers passed source/schema validation, and all 120 received completion grades. The full suite passed 289 tests; the post-run vault integrity check passed. These validation counts include abstentions and must not be presented as semantic accuracy.

The new ranking recovered 19 test answers that the no-graph baseline missed, while two previously successful answers regressed. It recovered 30 answers over the old graph ranking, with no previously successful old-graph answers lost in this run. The additional bilingual-index experiment remains in progress; it is not included in the 69/100 result. Detailed questions, citations, receipts and reports remain in the private benchmark directory.

This sampled, model-authored/model-checked evaluation is not independent human legal adjudication. Source gaps remain visible. Complete scheduled source delivery, exact citation validation, retrieval recall on designated references and semantic answer quality are separate measurements; none establishes perfect understanding or zero missed wording.
