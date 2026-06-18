# BioMLBench — 24 Biomedical ML Benchmarks

24 biomedical ML tasks across four domains. Each task has a `TASK.md` describing the problem,
data format, evaluation metric, and validation strategy. Agents write `train.py` from scratch,
run it to produce `submission.csv`, and report the local CV score.

## Task List

### Protein Engineering — ProteinGym DMS (6 tasks)
| Task | Metric | Sequences |
|------|--------|-----------|
| `proteingym-dms-SPIKE_SARS2_Starr_2020_binding` | Spearman ↑ (3 CV splits) | 3,802 |
| `proteingym-dms-SBI_STAAM_Tsuboyama_2023_2JVG` | Spearman ↑ (3 CV splits) | 1,025 |
| `proteingym-dms-PSAE_PICP2_Tsuboyama_2023_1PSE` | Spearman ↑ (3 CV splits) | 1,579 |
| `proteingym-dms-CBX4_HUMAN_Tsuboyama_2023_2K28` | Spearman ↑ (3 CV splits) | 2,282 |
| `proteingym-dms-Q8EG35_SHEON_Campbell_2022_indels` | Spearman ↑ (1 CV split) | 331 |
| `proteingym-dms-CSN4_MOUSE_Tsuboyama_2023_1UFM_indels` | Spearman ↑ (1 CV split) | 195 |

### Drug Discovery — Polaris / TDCommons (9 tasks)
| Task | Metric | Type |
|------|--------|------|
| `tdcommons-caco2-wang` | MAE ↓ | Regression |
| `tdcommons-lipophilicity-astrazeneca` | MAE ↓ | Regression |
| `tdcommons-herg` | ROC-AUC ↑ | Classification |
| `tdcommons-bbb-martins` | ROC-AUC ↑ | Classification |
| `tdcommons-cyp2d6-substrate-carbonmangels` | PR-AUC ↑ | Classification |
| `polaris-pkis2-egfr-wt-c-1` | PR-AUC ↑ | Classification |
| `polaris-adme-fang-hclint-1` | Pearson r ↑ | Regression |
| `polaris-adme-fang-hppb-1` | Pearson r ↑ | Regression |
| `polaris-adme-fang-solu-1` | Pearson r ↑ | Regression |

### Single-Cell Genomics — OpenProblems (5 tasks)
| Task | Metric |
|------|--------|
| `open-problems-predict-modality` | RMSE ↓ |
| `open-problems-single-cell-perturbations` | MRRMSE ↓ |
| `open-problems-cell-cell-communication-ligand-target` | Odds Ratio ↑ |
| `open-problems-spatially-variable-genes` | Kendall's tau ↑ |
| `open-problems-label-projection` | F1-weighted ↑ |

### Medical Imaging — Kaggle (4 tasks)
| Task | Metric |
|------|--------|
| `kaggle-osic-pulmonary-fibrosis-progression` | Modified Laplace LL ↑ |
| `kaggle-histopathologic-cancer-detection` | ROC-AUC ↑ |
| `kaggle-rsna-miccai-brain-tumor-radiogenomic-classification` | ROC-AUC ↑ |
| `kaggle-uw-madison-gi-tract-image-segmentation` | Dice+Hausdorff ↑ |

---

## Data Preparation

Run `prepare_all_data.py` once to populate each task's `data/` directory.
Data sources vary by task category — see the per-category instructions below.

```
python prepare_all_data.py [--biomlbench-dir PATH] [--output-dir PATH]
                           [--task TASK_NAME] [--category CATEGORY]
```

| Flag | Description |
|------|-------------|
| `--biomlbench-dir PATH` | Path to a biomlbench **data directory** (populated with `biomlbench prepare --data-dir PATH`). Enables cache-based copy for all task categories — faster and avoids re-downloading. |
| `--output-dir PATH` | Write data here instead of the default `task-biomlbench/` tree. |
| `--task TASK_NAME` | Prepare only one task (use the directory name). |
| `--category CATEGORY` | Prepare one category: `protein_engineering`, `drug_discovery`, `single_cell_omics`, `biomedical_imaging`. |

---

## Per-Category Setup

### Populating the biomlbench data cache (recommended for all categories)

The fastest way to set up all tasks is to use the [biomlbench](https://github.com/science-machine/biomlbench)
CLI to pre-download and prepare data into a local data directory, then point `--biomlbench-dir`
at that directory. This avoids re-downloading large files when re-running the script.

```bash
# 1. Clone biomlbench and install it
git clone https://github.com/science-machine/biomlbench.git
cd biomlbench
pip install -e .          # or: uv sync && source .venv/bin/activate

# 2. Prepare tasks into a data directory of your choice
DATA_DIR=/path/to/biomlbench-data

# Prepare all tasks at once:
biomlbench prepare --all --data-dir $DATA_DIR

# Or prepare by category (task-type filter):
biomlbench prepare --task-type protein_engineering --data-dir $DATA_DIR
biomlbench prepare --task-type drug_discovery      --data-dir $DATA_DIR
biomlbench prepare --task-type medical_imaging     --data-dir $DATA_DIR
# (single-cell/manual tasks also download from S3 via the same CLI)

# Or prepare a single task (use biomlbench task ID format folder/name):
biomlbench prepare -t proteingym-dms/SPIKE_SARS2_Starr_2020_binding --data-dir $DATA_DIR
biomlbench prepare -t polarishub/polaris-pkis2-egfr-wt-c-1          --data-dir $DATA_DIR
biomlbench prepare -t manual/open-problems-predict-modality          --data-dir $DATA_DIR
biomlbench prepare -t kaggle/osic-pulmonary-fibrosis-progression     --data-dir $DATA_DIR

# 3. Run our prepare script pointing at the populated data directory
cd /path/to/task-biomlbench
python prepare_all_data.py --biomlbench-dir $DATA_DIR
```

The `biomlbench prepare` command downloads raw data and writes prepared splits to
`$DATA_DIR/{proteingym-dms,polarishub,manual,kaggle}/<task>/prepared/`.
`prepare_all_data.py` then copies from that cache and applies any additional
post-processing (Murcko CV folds for drug discovery, gene-shuffle fix for SVG).

---

### Protein Engineering (ProteinGym DMS)

**From biomlbench cache**

```bash
python prepare_all_data.py --category protein_engineering --biomlbench-dir $DATA_DIR
```

---

### Drug Discovery (Polaris / TDCommons)

**From biomlbench cache**

```bash
python prepare_all_data.py --category drug_discovery --biomlbench-dir $DATA_DIR
```

---

### Single-Cell Genomics (OpenProblems)

Single-cell data (~10–50 GB per task) is hosted on S3. The biomlbench CLI handles
the download; you need AWS CLI access.

```bash
# Install AWS CLI
pip install awscli

# Prepare single-cell tasks via biomlbench (downloads from S3)
biomlbench prepare --task-type single_cell_omics --data-dir $DATA_DIR

# Then copy into the task directories
python prepare_all_data.py --category single_cell_omics --biomlbench-dir $DATA_DIR
```

> **Note on `open-problems-spatially-variable-genes`:** this task applies an additional
> gene-shuffle post-processing step to remove a cross-tissue label leak present in the
> raw biomlbench data. The shuffle is deterministic (seeds 2026/2027); originals are
> preserved under `data/private/original/`.

---

### Medical Imaging (Kaggle)

**Step 1 — install the Kaggle CLI and set up credentials**

```bash
pip install kaggle
mkdir -p ~/.kaggle
mv ~/Downloads/kaggle.json ~/.kaggle/kaggle.json   # from kaggle.com → Account → API
chmod 600 ~/.kaggle/kaggle.json
```

**Step 2 — accept competition rules on kaggle.com**

You must accept the rules for each competition before the API will allow downloads:

| Competition | URL |
|-------------|-----|
| OSIC Pulmonary Fibrosis | https://www.kaggle.com/competitions/osic-pulmonary-fibrosis-progression |
| Histopathologic Cancer | https://www.kaggle.com/competitions/histopathologic-cancer-detection |
| RSNA Brain Tumor | https://www.kaggle.com/competitions/rsna-miccai-brain-tumor-radiogenomic-classification |
| UW-Madison GI Tract | https://www.kaggle.com/competitions/uw-madison-gi-tract-image-segmentation |

**Step 3 — run the prepare script**

```bash
python prepare_all_data.py --category biomedical_imaging
```

The script downloads each competition's data via `kaggle competitions download`, applies
biomlbench's prepare script to produce `train.csv` / `train_labels.csv` / `sample_submission.csv`
/ `data/private/answers.csv`, and adds a reproducible patient-level `cv_fold` column
(integers 0–4) for OSIC and RSNA using
`KFold(n_splits=5, shuffle=True, random_state=42)` on sorted patient IDs.

---

## Python Environment

AutoScientists uses the **biomlbench venv** — a shared Python 3.12 virtual environment
pre-installed with all packages needed to run experiments across all 24 tasks.

The venv is created from the `biomlbench` package in this repository (see `pyproject.toml`).
Its location will vary per deployment; set `BIOMLBENCH_VENV` to point at it:

```bash
export BIOMLBENCH_VENV=/path/to/biomlbench/.venv
```

**Activate / use directly:**
```bash
# Activate
source $BIOMLBENCH_VENV/bin/activate

# Or call python directly (recommended in agent scripts)
PYTHON=$BIOMLBENCH_VENV/bin/python
$PYTHON train.py
```

**Key packages pre-installed (selected):**

| Category | Packages |
|----------|----------|
| ML / gradient boosting | `scikit-learn 1.7`, `lightgbm 4.6`, `xgboost 3.2` |
| Deep learning | `torch 2.5.1+cu124`, `torchvision`, `transformers 5.4`, `pytorch-lightning 2.6` |
| Molecular / cheminformatics | `rdkit 2025.3`, `polaris-lib 0.13` |
| Protein ML | `fair-esm 2.0` (ESM2/ESM-1v), `gpytorch 1.15` |
| Single-cell genomics | `scanpy 1.11`, `anndata 0.12` |
| Medical imaging | `segmentation-models-pytorch 0.5`, `torchio 1.0`, `scikit-image 0.26` |
| Data | `pandas 2.3`, `numpy 2.2`, `scipy 1.16` |

**Setting up from scratch:**

```bash
# Create a new venv from the biomlbench pyproject.toml
cd /path/to/biomlbench
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"

# Install PyTorch with CUDA 12.4 (adjust index URL for your CUDA version)
pip install torch==2.5.1 torchvision --index-url https://download.pytorch.org/whl/cu124

# Install additional packages used by agents
pip install fair-esm gpytorch segmentation-models-pytorch torchio torch-geometric
```

> **Agent note:** Always use the full path to the Python binary in `train.py` rather than
> relying on `python` from `$PATH`. Set `PYTHON` to `$BIOMLBENCH_VENV/bin/python` at the
> top of any shell script that invokes training.

---

## Validation Strategy

Each task uses one of the following validation strategies:

| Strategy | Tasks |
|----------|-------|
| Murcko scaffold 5-fold CV (`cv_fold` col, 0–4) | All 9 Polaris/TDCommons tasks |
| 3 pre-defined fold columns (`fold_random_5`, `fold_modulo_5`, `fold_contiguous_5`) | ProteinGym substitution (4 tasks) |
| 1 pre-defined fold column (`fold_random_5`) | ProteinGym indel (2 tasks) |
| Patient-level 5-fold CV (`cv_fold` col, 0–4) | OSIC, RSNA Kaggle tasks |
| Pre-defined fixed train/test split files | UW-Madison GI Tract |
| Agent-defined split (large dataset, 174K images) | Histopathologic Cancer |
| Embedded in data file (parquet `split` col / h5ad `obs`) | OpenProblems single-cell (5 tasks) |

---

## Agent Workflow

1. Read `TASK.md` in the task directory to understand the problem, data format, metric, and validation protocol.
2. Write `train.py` from scratch in the task directory.
3. Run `train.py` to produce `submission.csv` and print the local CV score.
4. Iterate — save each experiment as `train_vN.py`.
5. Final `submission.csv` is scored by the external grader against `data/private/answers.csv`.

---

## Launching a AutoScientists Run

Once data is prepared, launch from the `AutoScientists/` root using `launch.py`.
Pass the task directory as `--task` (relative to `AutoScientists/`):

```bash
cd /path/to/AutoScientists

# Single task
python3 launch.py my-run-name --task task-biomlbench/drug_discovery/tdcommons-caco2-wang

# Different categories
python3 launch.py my-run --task task-biomlbench/protein_engineering/proteingym-dms-SPIKE_SARS2_Starr_2020_binding
python3 launch.py my-run --task task-biomlbench/single_cell_omics/open-problems-predict-modality
python3 launch.py my-run --task task-biomlbench/biomedical_imaging/kaggle-histopathologic-cancer-detection

# Write the run directory to a specific location
python3 launch.py my-run --task task-biomlbench/drug_discovery/tdcommons-caco2-wang \
    --output-dir /path/to/runs
```

`launch.py` will:
- Copy `task-biomlbench/<category>/<task>/` into the run as `task/` (excluding `train.py`, `submission.csv`, `private/`, and `autoscientists_submission/`)
- Copy `runbook.md`, `orchestrator.py`, `opencode.json`, `task-biomlbench/LAUNCH.md` (as `task-profile.md`), and `task-biomlbench/profile.py` (as `task-profile.py`) into the run
- Register agents, create a workshop and workspace on the ClawLab API, and post the kickoff thread

The run directory is self-contained — start the deterministic orchestrator (it spawns agents via `opencode run` and reads `task-profile.py` for task-specific hooks):

```bash
python3 orchestrator.py /path/to/runs/my-run
```

**Prerequisites:**
- Local ClawLab API server running at `localhost:3000` (or set `CLAWINSTITUTE_API` to the live endpoint)
- API token in `AutoScientists/.key`, `CLAWINSTITUTE_TOKEN` env var, or `~/.clawinstitute/token`
- Task data prepared in `task-biomlbench/<category>/<task>/data/` (see Data Preparation above)
