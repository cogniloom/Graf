"""Render the README's paired model comparisons with matplotlib.

Run from any directory: MPLCONFIGDIR=/tmp/graf-mpl python benchmarks/render_model_comparison.py
The checked-in README tables are the data source; no model calls are made.
"""

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[1]
COLORS = ("#7B8798", "#2166AC")
SELECTIONS = (
    ("gpt-6-astra", "medium", "Controlled · no Graf"),
    ("gpt-6-astra", "medium", "Graf · controlled pilot"),
    ("gpt-6-luna", "low", "Native · no Graf"),
    ("gpt-6-luna", "low", "Graf · native Codex"),
)


def read_rows():
    """Select exactly one matching row per model/arm in each workload table."""
    text = (ROOT / "README.md").read_text()
    workloads = []
    for heading in ("#### Documents —", "#### Source code —"):
        section = text.split(heading, 1)[1].split("\n\n", 2)[1]
        rows = {}
        for line in section.splitlines():
            cells = [cell.strip().replace("**", "") for cell in line.split("|")[1:-1]]
            key = tuple(cells[:3])
            if key not in SELECTIONS:
                continue
            if key in rows:
                raise ValueError(f"Duplicate comparison row: {key}")
            match = re.fullmatch(r"(\d+)/(\d+) \([\d.]+%\)", cells[3])
            if match is None:
                raise ValueError(f"Missing measured pass count: {cells[3]}")
            passes, count = map(int, match.groups())
            total = int(cells[8].replace(",", ""))
            mean = total / count
            if not 0 <= passes <= count or abs(mean - int(cells[4].replace(",", ""))) > 0.51:
                raise ValueError(f"Inconsistent counts or mean tokens: {key}")
            rows[key] = (mean, 100 * passes / count, float(cells[9]), passes, count)
        workloads.append([rows[key] for key in SELECTIONS])
    return workloads


def main():
    data = read_rows()
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, axes = plt.subplots(2, 3, figsize=(15, 10.5))
    fig.patch.set_facecolor("white")
    fig.text(0.04, 0.955, "With Graf / Without Graf", fontsize=25, weight="bold", color="#172B45")
    fig.text(0.04, 0.913, "GPT-6-astra · medium     /     GPT-6-luna · low", fontsize=15, color="#334155")
    fig.legend(
        handles=[Patch(facecolor=COLORS[0], label="Without Graf"), Patch(facecolor=COLORS[1], label="With Graf")],
        loc="upper right", bbox_to_anchor=(0.97, 0.963), frameon=False, ncol=2,
    )
    titles = ("Mean model tokens ↓", "Strict automated passes ↑", "Median elapsed time ↓")
    limits = (110000, 132, 62)
    ticks = ([0, 25000, 50000, 75000, 100000], [0, 25, 50, 75, 100], [0, 15, 30, 45, 60])
    y = [3.4, 2.6, 1.1, 0.3]
    labels = ["Astra medium\nWithout Graf", "Astra medium\nWith Graf", "Luna low\nWithout Graf", "Luna low\nWith Graf"]
    for row, rows in enumerate(data):
        for col, ax in enumerate(axes[row]):
            values = [item[col] for item in rows]
            ax.barh(y, values, color=[COLORS[i % 2] for i in range(4)], height=0.55)
            ax.set_xlim(0, limits[col])
            ax.set_ylim(-0.25, 4)
            ax.set_xticks(ticks[col])
            ax.set_yticks(y, labels if col == 0 else [""] * 4)
            ax.tick_params(axis="both", length=0, labelsize=10, pad=8)
            ax.spines[["top", "right", "left"]].set_visible(False)
            ax.spines["bottom"].set_color("#CBD5E1")
            ax.set_axisbelow(True)
            ax.grid(axis="x", color="#E2E8F0", linewidth=0.7)
            ax.set_title(titles[col], loc="left", fontsize=13, weight="bold", pad=17)
            if col == 0:
                ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value / 1000:g}k" if value else "0"))
            elif col == 1:
                ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}%"))
            else:
                ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}s"))
            for i, value in enumerate(values):
                label = (f"{value:,.0f}" if col == 0 else
                         f"{rows[i][3]}/{rows[i][4]}" if col == 1 else f"{value:.2f} s")
                ax.text(value + limits[col] * 0.022, y[i], label, va="center", fontsize=11, weight="bold", color="#172B45")
    fig.text(0.04, 0.837, "DOCUMENTS", fontsize=14, weight="bold", color="#172B45")
    fig.text(0.04, 0.807, "1,000 fictional operational records · Astra: 15 paired questions · Luna: 16 paired questions", fontsize=11, color="#475569")
    fig.text(0.04, 0.485, "SOURCE CODE", fontsize=14, weight="bold", color="#172B45")
    fig.text(0.04, 0.455, "Astra: 189 source/doc files, 9 paired questions · Luna: 192 source/doc files, 10 paired questions", fontsize=11, color="#475569")
    fig.text(0.04, 0.095, "Matched baseline within each model: Astra uses controlled file tools; Luna uses native Codex.", fontsize=11, color="#334155")
    fig.text(0.04, 0.067, "One repetition · 26–27 Sep 2026 · automated grading; human review pending · protocols differ between models.", fontsize=10, color="#475569")
    fig.text(0.04, 0.043, "Tokens include cached input. Time includes retrieval/tools; excludes setup and grading. Luna: parallel baseline, sequential Graf.", fontsize=10, color="#475569")
    fig.text(0.04, 0.019, "Astra excludes one rubric-defective pair per workload; code speed direction changes before exclusion. Source: README benchmark tables.", fontsize=10, color="#475569")
    fig.subplots_adjust(left=0.155, right=0.975, top=0.755, bottom=0.17, hspace=0.94, wspace=0.20)
    for extension in ("png", "svg"):
        fig.savefig(ROOT / f"docs/assets/graf-model-comparison.{extension}", dpi=160, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()
