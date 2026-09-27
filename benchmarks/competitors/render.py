"""Render a static, zero-baseline comparison chart from verified exported metrics."""

import argparse
import json
from pathlib import Path


def render(root):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    rows = {r["system"]: r for r in json.loads((root / "summary.json").read_text())}
    order = ["graf", "trustgraph", "lightrag", "cognee", "graphiti"]
    names = {"graf": "Graf", "trustgraph": "TrustGraph", "lightrag": "LightRAG",
             "cognee": "Cognee", "graphiti": "Graphiti"}
    colors = ["#087f8c" if name == "graf" else "#8495aa" for name in order]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.spines.left": False, "axes.edgecolor": "#d9e0e9",
                         "text.color": "#203047", "axes.labelcolor": "#53657a",
                         "xtick.color": "#53657a", "ytick.color": "#203047"})
    panels = [
        ("Indexing time", "Seconds · lower is better", lambda r: r["ingest_seconds"], "seconds"),
        ("Indexing LLM usage", "Tokens · lower is better", lambda r:
         (r["ingest_usage"]["input_tokens"] + r["ingest_usage"]["output_tokens"])
         if r.get("ingest_usage") else None, "tokens"),
        ("Native context stage", "First-pass median seconds · lower is better", lambda r: r["median_retrieval_seconds"], "seconds"),
        ("Context + final answer", "Median seconds · lower is better", lambda r: r["median_end_to_end_seconds"], "seconds"),
        ("Query LLM usage", "Mean tokens, retrieval + answer · lower is better", lambda r: r["mean_query_tokens"], "tokens"),
        ("Strict answer quality", "Strict automated passes · higher is better", lambda r:
         r["strict_passes"] if r.get("graded_queries") == r.get("planned_queries") else None, "passes"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(17.5, 9.5))
    fig.patch.set_facecolor("#f7f9fc")
    for ax, (title, subtitle, metric, kind) in zip(axes.flat, panels):
        ax.set_facecolor("#f7f9fc")
        values = []
        for name in order:
            row = rows.get(name)
            values.append(metric(row) if row and row["status"] == "complete" else None)
        maximum = max([v for v in values if v is not None] or [1])
        if kind == "passes":
            maximum = max([r["planned_queries"] for r in rows.values()] or [16])
        ax.barh(range(5), [v or 0 for v in values], color=colors, height=.55, zorder=3)
        ax.set_yticks(range(5), [names[n] for n in order])
        ax.invert_yaxis()
        ax.set_xlim(0, maximum * 1.28 if maximum else 1)
        ax.tick_params(axis="y", length=0, pad=8)
        ax.grid(axis="x", color="#e2e8ef", linewidth=.7, zorder=0)
        ax.set_title(title, loc="left", weight="bold", fontsize=14, pad=28)
        ax.text(0, 1.045, subtitle, transform=ax.transAxes, fontsize=10, color="#53657a")
        ax.xaxis.set_major_formatter(FuncFormatter(lambda x, pos:
            f"{x / 1_000_000:g}M" if x >= 1_000_000 else f"{x / 1000:g}k" if x >= 1000 else f"{x:g}"))
        if kind == "passes":
            ax.set_xlim(0, maximum)
        for i, value in enumerate(values):
            if value is None:
                label = "Not measured" if order[i] not in rows else "Incomplete"
                if kind == "passes" and order[i] in rows and rows[order[i]]["status"] == "complete":
                    label = "Not fully graded"
            elif kind == "passes":
                label = f"{value}/{rows[order[i]]['planned_queries']}"
            elif kind == "tokens":
                label = f"{value:,.0f}"
            else:
                label = f"{value:,.2f} s"
            ax.text((value or 0) + maximum * .025, i, label, va="center", fontsize=10,
                    color="#087f8c" if order[i] == "graf" else "#203047", weight="bold" if order[i] == "graf" else "normal")
    first = next(iter(rows.values()))
    fig.suptitle("Graf vs. graph retrieval systems", x=.07, y=.985, ha="left", fontsize=24, weight="bold")
    fig.text(.07, .937, f"{first['corpus_files']} shared fictional documents · {first['planned_queries']} questions · GPT-6-luna / high · CPU",
             fontsize=13, color="#53657a")
    fig.text(.07, .045, "Measured local configuration comparison, not a universal ranking. One answering pass; authored questions; human review pending.",
             fontsize=10, color="#53657a")
    fig.text(.07, .024, "TrustGraph context stage includes native synthesis. Codex transport; different embeddings; shared host. See REPORT.md for scope and receipts.",
             fontsize=10, color="#53657a")
    fig.subplots_adjust(left=.09, right=.97, top=.835, bottom=.12, hspace=.55, wspace=.45)
    fig.savefig(root / "comparison.png", dpi=180, facecolor=fig.get_facecolor())
    fig.savefig(root / "comparison.svg", facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    render(parser.parse_args().results)
