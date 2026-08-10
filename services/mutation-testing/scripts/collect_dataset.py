"""
Dataset collection over the whole corpus.
============================================
REQUIRES WSL/Linux -- mutmut has no native Windows support.

    cd services/mutation-testing
    python scripts/verify_corpus.py          # always run this first
    python scripts/collect_dataset.py 2>&1 | tee reports/collection_$(date +%F).log

Iterates corpus/MANIFEST.json and runs each module TWICE -- once against
tests_weak/ and once against tests_strong/ -- writing every mutant to the
dataset tagged with which suite produced it.

WHY BOTH SUITES
---------------
The pair is what produces ground-truth gap labels. A mutant that survives the
weak suite but is killed by the strong one is a REAL missing test; one that
survives both is likely equivalent. That label does not otherwise exist -- see
corpus/GENERATION.md section 5.

WHY THIS CALLS THE HARNESS DIRECTLY RATHER THAN THE HTTP API
------------------------------------------------------------
No server to start, no request timeouts on a run that legitimately takes an
hour, and no ambiguity about which process wrote which rows. The API exists for
the Orchestrator; bulk collection is a batch job.

IDEMPOTENCY
-----------
Every row is upserted against UNIQUE(dataset_schema_version, module_key, suite,
mutant_id), so re-running corrects rows in place instead of appending
near-duplicates. Dataset v1 had no such constraint: 155 stored rows were 58
distinct mutants, one of them present 8 times.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
for p in (str(REPO_ROOT), str(SERVICE_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from features.extractor import extract_features           # noqa: E402
from harness.executor import run_mutation_analysis        # noqa: E402
from mutators.taxonomy import OTHER, TAXONOMY_VERSION     # noqa: E402
from storage.db import DATASET_SCHEMA_VERSION, connection, insert_record  # noqa: E402
from triage.arid_filter import triage_mutant              # noqa: E402

CORPUS = SERVICE_ROOT / "corpus"
REPORTS = SERVICE_ROOT / "reports"

# Quality gates. If a collection run trips these, the dataset is not fit to
# train on and the run says so rather than quietly producing a model.
MAX_UNCLASSIFIED_RATE = 0.05
MAX_UNRESOLVED_RATE = 0.05


def git_sha(path: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def collect_one(entry: Dict, suite: str, run_id: str, shas: Dict,
                timeout: int) -> Dict:
    """Run one module against one suite and persist every mutant."""
    source = SERVICE_ROOT / entry["source"]
    tests_dir = SERVICE_ROOT / entry[f"tests_{suite}"]
    module_key = entry["module_key"]

    started = time.time()
    mutants, covered_lines, covering_counts = run_mutation_analysis(
        source, tests_dir, timeout=timeout
    )

    stats = {
        "module": entry["dir"], "suite": suite, "total": len(mutants),
        "killed": 0, "active_survivor": 0, "uncovered": 0,
        "equivalent_suspected": 0, "unclassified": 0, "unresolved_line": 0,
    }
    unclassified_rows: List[Dict] = []

    with connection() as conn:
        for m in mutants:
            if m.operator == OTHER:
                stats["unclassified"] += 1
                unclassified_rows.append({
                    "module_key": module_key, "suite": suite,
                    "mutant_id": m.mutant_id, "detail": m.classification_detail,
                    "diff": m.diff[:400],
                })
            if m.line_number is None:
                stats["unresolved_line"] += 1

            covered = m.line_number is not None and m.line_number in covered_lines
            features = extract_features(
                source, m.line_number, m.operator, covered,
                n_covering_tests=covering_counts.get(m.line_number, 0),
            )
            triage = triage_mutant(m.diff,
                                   function_name=m.qualified_name or m.function_name,
                                   operator=m.operator)

            if m.status != "survived":
                outcome = "killed"
            elif not covered:
                outcome = "uncovered"
            elif triage.status == "equivalent_suspected":
                outcome = "equivalent_suspected"
            else:
                outcome = "active_survivor"
            stats[outcome] += 1

            insert_record(
                run_id, module_key, m.mutant_id, features, outcome,
                module_slug=entry["dir"], suite=suite,
                function_name=m.function_name, class_name=m.class_name,
                line_resolution=m.resolution_method,
                triage_status=triage.status,
                triage_confidence=triage.confidence,
                triage_reason_code=triage.reason_code,
                taxonomy_version=TAXONOMY_VERSION,
                pipeline_git_sha=shas["pipeline"], corpus_git_sha=shas["corpus"],
                conn=conn,
            )

    total = stats["total"]
    stats["mutation_score"] = round(stats["killed"] / total, 3) if total else 0.0
    stats["elapsed_s"] = round(time.time() - started, 1)
    stats["_unclassified_rows"] = unclassified_rows
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suites", default="weak,strong",
                        help="comma-separated: weak,strong")
    parser.add_argument("--only", default="", help="substring filter on module dir")
    parser.add_argument("--timeout", type=int, default=600,
                        help="per-module mutmut timeout in seconds")
    args = parser.parse_args()

    if shutil.which("mutmut") is None:
        print("ERROR: mutmut is not on PATH.\n"
              "       It has no native Windows support -- run this under WSL/Linux.\n"
              "       See services/mutation-testing/README.md.")
        return 2

    manifest_path = CORPUS / "MANIFEST.json"
    if not manifest_path.exists():
        print("ERROR: corpus/MANIFEST.json missing. Run scripts/verify_corpus.py first.")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    entries = manifest["modules"]
    if args.only:
        entries = [e for e in entries if args.only in e["dir"]]
    suites = [s.strip() for s in args.suites.split(",") if s.strip()]

    run_id = str(uuid4())
    shas = {"pipeline": git_sha(REPO_ROOT), "corpus": git_sha(REPO_ROOT)}

    print(f"collection run {run_id}")
    print(f"  schema={DATASET_SCHEMA_VERSION} taxonomy={TAXONOMY_VERSION} "
          f"pipeline_sha={shas['pipeline'] or 'n/a'}")
    print(f"  {len(entries)} module(s) x {len(suites)} suite(s)\n")
    header = (f"{'module':<26} {'suite':<7} {'tot':>4} {'kill':>5} {'act':>4} "
              f"{'unc':>4} {'equiv':>6} {'?op':>4} {'?line':>6} {'score':>6} {'sec':>6}")
    print(header)
    print("-" * len(header))

    all_stats: List[Dict] = []
    all_unclassified: List[Dict] = []

    for entry in entries:
        for suite in suites:
            try:
                s = collect_one(entry, suite, run_id, shas, args.timeout)
            except Exception as exc:
                print(f"{entry['dir']:<26} {suite:<7} FAILED: {type(exc).__name__}: {exc}")
                continue
            all_unclassified.extend(s.pop("_unclassified_rows"))
            all_stats.append(s)
            print(f"{s['module']:<26} {suite:<7} {s['total']:>4} {s['killed']:>5} "
                  f"{s['active_survivor']:>4} {s['uncovered']:>4} "
                  f"{s['equivalent_suspected']:>6} {s['unclassified']:>4} "
                  f"{s['unresolved_line']:>6} {s['mutation_score']:>6.3f} "
                  f"{s['elapsed_s']:>6.1f}")

    if not all_stats:
        print("\nno modules collected")
        return 1

    total = sum(s["total"] for s in all_stats)
    unclassified = sum(s["unclassified"] for s in all_stats)
    unresolved = sum(s["unresolved_line"] for s in all_stats)
    unclassified_rate = unclassified / total if total else 0.0
    unresolved_rate = unresolved / total if total else 0.0

    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    (REPORTS / f"collection_{stamp}.json").write_text(
        json.dumps({"run_id": run_id, "shas": shas, "modules": all_stats,
                    "totals": {"mutants": total,
                               "unclassified_rate": round(unclassified_rate, 4),
                               "unresolved_rate": round(unresolved_rate, 4)}},
                   indent=2), encoding="utf-8")
    if all_unclassified:
        with (REPORTS / "unclassified_mutations.jsonl").open("w", encoding="utf-8") as fh:
            for row in all_unclassified:
                fh.write(json.dumps(row) + "\n")

    print(f"\n{'=' * 60}")
    print(f"total mutants:       {total}")
    print(f"unclassified (other): {unclassified} ({unclassified_rate:.1%})  "
          f"[v1 baseline: 73.5%]")
    print(f"unresolved lines:     {unresolved} ({unresolved_rate:.1%})")

    # --- pre-training gates ---
    failures = []
    if unclassified_rate > MAX_UNCLASSIFIED_RATE:
        failures.append(f"unclassified rate {unclassified_rate:.1%} exceeds "
                        f"{MAX_UNCLASSIFIED_RATE:.0%} -- inspect "
                        f"reports/unclassified_mutations.jsonl and extend the taxonomy "
                        f"BEFORE training")
    if unresolved_rate > MAX_UNRESOLVED_RATE:
        failures.append(f"unresolved-line rate {unresolved_rate:.1%} exceeds "
                        f"{MAX_UNRESOLVED_RATE:.0%} -- the line resolver is missing "
                        f"cases; coverage gating cannot be trusted until it is fixed")

    # measured score vs. the tier declared before measurement
    tier_bands = {"strong": (0.80, 1.01), "medium": (0.45, 0.85),
                  "weak": (0.10, 0.60), "untested_function": (0.0, 0.80)}
    by_dir = {e["dir"]: e for e in entries}
    print("\ndeclared tier vs. measured weak-suite score:")
    for s in all_stats:
        if s["suite"] != "weak":
            continue
        tier = by_dir[s["module"]]["intended_suite_tier"]
        lo, hi = tier_bands.get(tier, (0.0, 1.01))
        ok = lo <= s["mutation_score"] <= hi
        print(f"  {s['module']:<26} {tier:<18} {s['mutation_score']:.3f}  "
              f"{'ok' if ok else 'OUT OF BAND'}")
        if not ok:
            failures.append(f"{s['module']}: declared {tier}, measured "
                            f"{s['mutation_score']:.3f} (expected {lo:.2f}-{hi:.2f})")

    if failures:
        print("\nGATE FAILURES -- do not train on this dataset yet:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nall gates passed. next: python src/ml/train.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
