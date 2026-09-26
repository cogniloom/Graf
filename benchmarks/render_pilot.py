"""Render the dated pilot chart; requires matplotlib."""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

root = Path(sys.argv[1])
inputs = [
    ("documents", "1,000 fictional operational documents\n15 valid questions · 1 rubric exclusion"),
    ("code", "189 real source/doc files · 31,039 lines\n9 valid questions · 1 rubric exclusion"),
]
fig, axes = plt.subplots(2, 3, figsize=(13, 7.5))
colors = ["#506784", "#008878"]
for row, (kind, label) in enumerate(inputs):
    data = json.loads((root / f"{kind}-primary.json").read_text())
    quality = json.loads((root / f"{kind}-quality.json").read_text())
    if not quality["complete_measured_and_graded"]:
        raise SystemExit("Refusing to plot incomplete or ungraded comparisons")
    arms = [data["arms"][a] for a in ("files", "graf")]
    values = [
        [a["mean_input_tokens"] + a["mean_output_tokens"] for a in arms],
        [a["median_seconds"] for a in arms],
        [100 * a["answer_accuracy"] for a in arms],
    ]
    titles = [
        "Mean model tokens\nincludes cached input",
        "Median elapsed seconds\nincludes retrieval and tools",
        "Strict automated pass rate\nhuman review pending",
    ]
    for col in range(3):
        ax = axes[row, col]
        ax.barh([1, 0], values[col], color=colors, height=0.5)
        ax.set_yticks([1, 0], ["File search", "Graf + file tools"])
        ax.set_xlim(0, 116 if col == 2 else max(values[col]) * 1.36)
        ax.set_ylim(-0.65, 1.65)
        for i, value in enumerate(values[col]):
            if col == 0:
                text = f"{value:,.0f}"
            elif col == 1:
                text = f"{value:.1f} s"
            else:
                text = f"{arms[i]['strict_passes']}/{arms[i]['scheduled']} ({value:.1f}%)"
            ax.text(
                value - 3 if col == 2 else value + ax.get_xlim()[1] * 0.018,
                1 - i,
                text,
                va="center",
                ha="right" if col == 2 else "left",
                color="white" if col == 2 else "black",
                fontsize=10,
            )
        ax.set_title(titles[col], loc="left", fontsize=11, pad=12)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="y", length=0, labelsize=9)
        ax.tick_params(axis="x", labelsize=8)
        ax.grid(axis="x", alpha=0.15)
        ax.set_axisbelow(True)
        if col == 2:
            ax.set_xticks([0, 50, 100], ["0%", "50%", "100%"])
    axes[row, 0].text(0, 1.34, label, transform=axes[row, 0].transAxes, fontsize=11, fontweight="bold")
fig.suptitle(
    "Pilot: Graf hybrid retrieval vs file-search baseline",
    x=0.055,
    ha="left",
    fontsize=17,
    fontweight="bold",
    y=0.98,
)
fig.text(
    0.055,
    0.91,
    "Both arms request GPT-6-astra, medium effort. Graf receives initial retrieved context.",
    fontsize=11,
    color="#333333",
)
fig.text(
    0.055,
    0.055,
    "One repetition · six-response limit · shared Linux host · actual subscription token receipts",
    fontsize=10,
)
fig.text(
    0.055,
    0.026,
    "Code speed direction changes with rubric exclusion. Setup costs, original results and limitations: REPORT.md.",
    fontsize=9,
    color="#444444",
)
fig.subplots_adjust(left=0.14, right=0.96, top=0.76, bottom=0.13, hspace=1.15, wspace=0.58)
fig.savefig(root / "comparison.png", dpi=160, facecolor="white")
fig.savefig(root / "comparison.svg", facecolor="white")
