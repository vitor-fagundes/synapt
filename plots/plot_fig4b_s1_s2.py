"""
plot_fig4b_s1_s2.py

Figura 4(B) do paper — Sistema 1 vs. Sistema 2 (ciclos cumulativos)
para todas as topologias consolidadas.

Topologias:
- 200_with_failures
- 250_with_failures
- 250_random_failures
- 300_with_failures

Saída: plots/figures/fig4_B_s1_s2.{pdf,png}
"""

import os
import re
import math
import glob
import warnings

import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────
# Estilo
# ─────────────────────────────────────────────────────────────

plt.rcParams.update({
    "font.family":        "DejaVu Sans",
    "font.size":          20,
    "axes.titlesize":     20,
    "axes.titleweight":   "bold",
    "axes.labelsize":     20,
    "axes.labelweight":   "bold",
    "xtick.labelsize":    20,
    "ytick.labelsize":    20,
    "axes.spines.top":    True,
    "axes.spines.right":  True,
    "axes.grid":          True,
    "grid.color":         "#DDDDDD",
    "grid.linestyle":     "--",
    "grid.linewidth":     0.8,
    "grid.alpha":         0.6,
    "legend.frameon":     True,
    "legend.fontsize":    18,
    "legend.framealpha":  0.95,
    "figure.facecolor":   "white",
    "axes.facecolor":     "white",
    "figure.dpi":         150,
})


# ─────────────────────────────────────────────────────────────
# Caminhos
# ─────────────────────────────────────────────────────────────

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")

RESULTS_DIR = os.path.join(ROOT, "results-synapt")
RANDOM_DIR = os.path.join(RESULTS_DIR, "250_random_failures")

OUT = os.path.join(HERE, os.environ.get("OUT_DIR", "figures"))
os.makedirs(OUT, exist_ok=True)

csv_path = os.path.join(DATA, "q_learning_evolution.csv")

if not os.path.exists(csv_path):
    raise FileNotFoundError(f"Arquivo não encontrado: {csv_path}")


# ─────────────────────────────────────────────────────────────
# Leitura do CSV consolidado
# ─────────────────────────────────────────────────────────────

df = pd.read_csv(csv_path)

required_cols = {"group", "run", "s1_count", "s2_count"}
missing = required_cols - set(df.columns)

if missing:
    raise ValueError(
        f"O arquivo q_learning_evolution.csv não possui as colunas obrigatórias: {missing}"
    )


# ─────────────────────────────────────────────────────────────
# Utilitários para o cenário random
# ─────────────────────────────────────────────────────────────

S1_COLS = [
    "cycleS1",
    "s1_count",
    "S1_count",
    "s1Count",
    "S1Count",
    "s1_cycles",
    "S1_cycles",
    "s1Cycles",
    "S1Cycles",
    "fast_count",
    "fast_cycles",
    "fastSystemCycles",
]

S2_COLS = [
    "cycleS2",
    "s2_count",
    "S2_count",
    "s2Count",
    "S2Count",
    "s2_cycles",
    "S2_cycles",
    "s2Cycles",
    "S2Cycles",
    "slow_count",
    "slow_cycles",
    "slowSystemCycles",
]


def parse_run_number(path):
    m = re.search(r"run[_-]?(\d+)", path, re.I)
    return int(m.group(1)) if m else None


def get_last_value_from_columns(stats_df, possible_cols):
    for col in possible_cols:
        if col in stats_df.columns:
            values = stats_df[col].dropna()
            if not values.empty:
                return float(values.iloc[-1])
    return None


def find_random_run_dirs(random_dir):
    direct = sorted(glob.glob(os.path.join(random_dir, "run_*")))
    if direct:
        return [p for p in direct if os.path.isdir(p)]

    stats_files = sorted(
        glob.glob(os.path.join(random_dir, "**", "IntuitiveStats.csv"), recursive=True)
    )
    return sorted({os.path.dirname(p) for p in stats_files})


# ─────────────────────────────────────────────────────────────
# Reconstrução de 250_random_failures
# ─────────────────────────────────────────────────────────────

random_rows = []

if not os.path.exists(RANDOM_DIR):
    print(f"[WARN] Diretório random não encontrado: {RANDOM_DIR}")
else:
    run_dirs = find_random_run_dirs(RANDOM_DIR)
    print(f"Diretórios de rodada random encontrados: {len(run_dirs)}")

    per_run_rows = []

    for run_dir in run_dirs:
        run = parse_run_number(run_dir)

        if run is None:
            print(f"[WARN] Não foi possível inferir a rodada em: {run_dir}")
            continue

        stats_csv = os.path.join(run_dir, "IntuitiveStats.csv")

        if not os.path.exists(stats_csv):
            print(f"[WARN] IntuitiveStats.csv ausente em: {run_dir}")
            continue

        stats = pd.read_csv(stats_csv)

        if "cycleS1" not in stats.columns or "cycleS2" not in stats.columns:
            print(f"[WARN] cycleS1/cycleS2 ausentes em: {stats_csv}")
            print(f"       Colunas disponíveis: {list(stats.columns)}")
            continue

        s1_run = pd.to_numeric(stats["cycleS1"], errors="coerce").fillna(0).sum()
        s2_run = pd.to_numeric(stats["cycleS2"], errors="coerce").fillna(0).sum()

        per_run_rows.append({
            "run": run,
            "s1_run": s1_run,
            "s2_run": s2_run,
        })

    per_run_df = pd.DataFrame(per_run_rows).sort_values("run")

    if not per_run_df.empty:
        per_run_df["s1_count"] = per_run_df["s1_run"].cumsum()
        per_run_df["s2_count"] = per_run_df["s2_run"].cumsum()

        random_rows = [
            {
                "group": "250_random_failures",
                "n_nodes": 250,
                "run": int(row["run"]),
                "s1_count": float(row["s1_count"]),
                "s2_count": float(row["s2_count"]),
            }
            for _, row in per_run_df.iterrows()
        ]

random_df = pd.DataFrame(random_rows)
print(f"250_random_failures: {len(random_df)} rodadas extraídas")

if not random_df.empty:
    print("\nResumo 250_random_failures:")
    print(random_df[["run", "s1_count", "s2_count"]].tail())

    df = df[df["group"] != "250_random_failures"].copy()
    df = pd.concat([df, random_df], ignore_index=True)
else:
    print("[WARN] Nenhuma rodada válida encontrada para 250_random_failures.")


# ─────────────────────────────────────────────────────────────
# Configuração dos cenários
# ─────────────────────────────────────────────────────────────

GROUPS_ALL = [
    "200_with_failures",
    "250_with_failures",
    "250_random_failures",
    "300_with_failures",
]

GROUP_LABELS_ALL = {
    "200_with_failures":   "200",
    "250_with_failures":   "250",
    "250_random_failures": "250 (FR)",
    "300_with_failures":   "300",
}

GROUP_COLORS_ALL = {
    "200_with_failures":   "#1565C0",
    "250_with_failures":   "#2E7D32",
    "250_random_failures": "#F57C00",
    "300_with_failures":   "#B71C1C",
}

SERIES_ALL = [
    ("s1_count", "S1", "-"),
    ("s2_count", "S2", "--"),
]

# ─────────────────────────────────────────────────────────────
# Escala Y compartilhada
# ─────────────────────────────────────────────────────────────

TICK_STEP = 2000
valid_max_values = [
    df[df["group"] == g][["s1_count", "s2_count"]].max().max()
    for g in GROUPS_ALL
    if not df[df["group"] == g].empty
]
valid_max_values = [v for v in valid_max_values if pd.notna(v)]

if not valid_max_values:
    raise RuntimeError("Nenhum dado válido encontrado para gerar os gráficos.")

Y_MAX = math.ceil(max(valid_max_values) / TICK_STEP) * TICK_STEP
if Y_MAX <= 0:
    Y_MAX = TICK_STEP

fig, ax = plt.subplots(figsize=(7, 5))

for group in GROUPS_ALL:
    sub = df[df["group"] == group].sort_values("run")

    if sub.empty:
        print(f"  skipping {group}: no data")
        continue

    print(f"  plotting {group}: {len(sub)} rows")

    for col, system_label, linestyle in SERIES_ALL:
        if col not in sub.columns or sub[col].isna().all():
            print(f"  skipping {group}/{col}: no valid data")
            continue

        ax.plot(
            sub["run"],
            sub[col],
            marker="o",
            markersize=3,
            linewidth=2,
            linestyle=linestyle,
            color=GROUP_COLORS_ALL[group],
            alpha=0.9,
            label=f"{GROUP_LABELS_ALL[group]} — {system_label}",
        )

for spine in ax.spines.values():
    spine.set_visible(True)
    spine.set_linewidth(1.1)
    spine.set_color("black")

ax.set_title("(B)", fontweight="bold")
ax.set_xlabel("Rounds")
ax.set_ylabel("Cumulative cycles")
ax.set_xlim(1, 35)
ax.xaxis.set_major_locator(MultipleLocator(5))
ax.set_ylim(0, Y_MAX)
ax.yaxis.set_major_locator(MultipleLocator(TICK_STEP))

ax.legend(
    loc="upper left",
    ncol=2,
    frameon=True,
    fontsize=14,
)

plt.tight_layout()

base = os.path.join(OUT, "fig4_B_s1_s2")
plt.savefig(base + ".pdf", bbox_inches="tight")
plt.savefig(base + ".png", bbox_inches="tight")
plt.close()

print(f"  saved {base}.pdf / .png")

print(f"\nTodos os arquivos salvos em: {OUT}")