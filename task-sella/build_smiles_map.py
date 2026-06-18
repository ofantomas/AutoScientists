#!/usr/bin/env python3
"""Build a static SMILES + chemistry map for the sella TRAIN molecules.

Output (TSV): mol_id <tab> name <tab> n_atoms <tab> formula <tab> smiles

Run ONCE at build time and commit the output. It is exposed to AutoScientists agents at
`{FOCUS_ROOT}/task/molecule_smiles.tsv` (task-sella/ is copied wholesale into the run dir) so
analysts/CPU agents can reason about the chemistry behind a molecule id. It is NEVER computed at
agent runtime.

Sources (all in the sibling opt_problem repo's molecules/ dir):
  - train ids + n_atoms : train_XTB.json
  - numeric-id SMILES   : log.txt  (tab-separated; `seed` -> `smiles`). A numeric train id is
                          `<seed>_<conf>`; stripping the `_<conf>` suffix gives the log.txt seed.
                          (Verified: all 225 numeric train ids resolve this way.)
  - named-drug SMILES   : PubChem REST, looked up by name (the 25 marketed drugs, e.g. abemaciclib).
                          Needs network. RDKit/xyz2mol on the molecule's XYZ is the offline
                          alternative (see molecules/04_rowansci.py) but is not required here.
  - molecular formula   : counted (Hill notation) from xyz/<mol_id>_mm.xyz — authoritative for the
                          exact geometry, no external dependency.

Usage:
  python build_smiles_map.py [--molecules-dir ../../opt_problem/molecules] [--out molecule_smiles.tsv]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/property/{prop}/TXT"
HERE = os.path.dirname(os.path.abspath(__file__))


def hill_formula(xyz_path: str) -> str:
    """Molecular formula in Hill notation, counted from an XYZ file's element column."""
    with open(xyz_path) as f:
        lines = f.read().splitlines()
    n = int(lines[0].strip())
    atoms = Counter()
    for line in lines[2:2 + n]:            # line 0 = count, line 1 = comment, then n atom lines
        atoms[line.split()[0]] += 1
    # Hill order: carbon first, hydrogen second, then the rest alphabetical (all alphabetical if no C).
    if "C" in atoms:
        ordered = ["C"] + (["H"] if "H" in atoms else []) \
                  + sorted(el for el in atoms if el not in ("C", "H"))
    else:
        ordered = sorted(atoms)
    return "".join(el + (str(atoms[el]) if atoms[el] > 1 else "") for el in ordered)


def pubchem_smiles(name: str) -> str:
    """Canonical SMILES for a compound name via PubChem REST. Tries CanonicalSMILES then SMILES."""
    query = name.replace("_", " ")
    for prop in ("CanonicalSMILES", "SMILES"):
        url = PUBCHEM.format(name=urllib.parse.quote(query), prop=prop)
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                text = resp.read().decode().strip()
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            if first:
                return first
        except Exception:
            continue
        finally:
            time.sleep(0.25)              # be polite: PubChem allows ~5 req/s
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Build molecule_smiles.tsv for the sella train set")
    ap.add_argument("--molecules-dir", default=os.path.join(HERE, "..", "..", "opt_problem", "molecules"),
                    help="opt_problem molecules/ dir (has train_XTB.json, log.txt, xyz/)")
    ap.add_argument("--out", default=os.path.join(HERE, "molecule_smiles.tsv"))
    args = ap.parse_args()

    mdir = os.path.abspath(args.molecules_dir)
    train = json.load(open(os.path.join(mdir, "train_XTB.json")))

    # seed -> smiles from log.txt (skip '#'-prefixed failure lines)
    seed2smi = {}
    with open(os.path.join(mdir, "log.txt")) as f:
        for row in csv.DictReader(f, delimiter="\t"):
            seed = (row.get("seed") or "").strip()
            if seed and not seed.startswith("#"):
                seed2smi[seed] = (row.get("smiles") or "").strip()

    rows, missing = [], []
    for mol_id in sorted(train):
        n_atoms = train[mol_id].get("n_atoms")
        xyz_path = os.path.join(mdir, "xyz", f"{mol_id}_mm.xyz")
        formula = hill_formula(xyz_path) if os.path.exists(xyz_path) else ""
        stem = mol_id.rsplit("_", 1)[0]
        is_numeric = stem.isdigit()
        if is_numeric:
            name, smiles = "", seed2smi.get(stem, "")
        else:
            name = mol_id
            smiles = pubchem_smiles(mol_id)
            print(f"  PubChem {mol_id:28s} -> {'OK' if smiles else 'MISS'}", file=sys.stderr)
        if not smiles:
            missing.append(mol_id)
        rows.append({"mol_id": mol_id, "name": name, "n_atoms": n_atoms,
                     "formula": formula, "smiles": smiles})

    if missing:
        raise SystemExit(f"ERROR: {len(missing)}/{len(train)} molecules missing SMILES: {missing}")
    if len(rows) != len(train):
        raise SystemExit(f"ERROR: row count {len(rows)} != train size {len(train)}")

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["mol_id", "name", "n_atoms", "formula", "smiles"])
        for r in rows:
            w.writerow([r["mol_id"], r["name"], r["n_atoms"], r["formula"], r["smiles"]])
    print(f"Wrote {len(rows)} molecules (all with SMILES) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
