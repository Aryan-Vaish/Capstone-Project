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
from typing import Dict, List, Optional
from uuid import uuid4

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
for p in (str(REPO_ROOT), str(SERVICE_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from evaluation.stats import rank_order_summary, wilson_interval  # noqa: E402
from features.extractor import extract_features           # noqa: E402
from harness.executor import run_mutation_analysis        # noqa: E402
from mutators.taxonomy import OTHER, TAXONOMY_VERSION     # noqa: E402
from storage.db import (  # noqa: E402
    DATASET_SCHEMA_VERSION, connection, db_path, insert_record,
)
from triage.arid_filter import triage_mutant              # noqa: E402

CORPUS = SERVICE_ROOT / "corpus"
REPORTS = SERVICE_ROOT / "reports"

# Quality gates. If a collection run trips these, the dataset is not fit to
# train on and the run says so rather than quietly producing a model.
MAX_UNCLASSIFIED_RATE = 0.05
MAX_UNRESOLVED_RATE = 0.05

# Gate 4: the Tier-0 equivalence filter's measured precision. A mutant the test
# suite KILLED that the filter called unkillable is a false positive, observable
# without any oracle. This gate exists because `mutation_score_adjusted`
# subtracts the filter's output from its denominator -- so without it the
# pipeline would be grading itself, and an over-flagging filter would be
# rewarded with a higher score. The denominator is measured, not trusted.
MIN_TRIAGE_PRECISION = 0.85

# Suite-strength ordinal, lowest first. `untested_function` is deliberately
# absent: it is a coverage claim, not a point on this scale, and gets its own
# targeted check below.
TIER_ORDER = ("weak", "medium", "strong")

# Wide rails, justified by what they DETECT rather than by what we expect. These
# catch harness breakage (a module scoring 0.0 or 1.0 usually means the suite
# did not run), not tier miscalibration -- that is the trend test's job.
SANITY_MIN_SCORE = 0.05
SANITY_MAX_SCORE = 0.98
MAX_UNCOVERED_RATE_NON_08 = 0.5


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
        "equivalent_suspected": 0, "low_value": 0, "unclassified": 0, "unresolved_line": 0,
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
            elif triage.status == "low_value":
                outcome = "low_value"
            else:
                outcome = "active_survivor"
            stats[outcome] += 1

            insert_record(
                run_id, module_key, m.mutant_id, features, outcome,
                module_slug=entry["dir"], suite=suite,
                function_name=m.function_name, class_name=m.class_name,
                line_resolution=m.resolution_method,
                removed_line=m.removed_line, added_line=m.added_line,
                triage_status=triage.status,
                triage_confidence=triage.confidence,
                triage_reason_code=triage.reason_code,
                taxonomy_version=TAXONOMY_VERSION,
                pipeline_git_sha=shas["pipeline"], corpus_git_sha=shas["corpus"],
                conn=conn,
            )

    # All three scores, transcribed verbatim from main.py's definitions so the
    # two code paths can never report different numbers under the same name.
    # See shared/schemas/mutation_schema.py for what each one means.
    total = stats["total"]
    equiv = stats["equivalent_suspected"] + stats["low_value"]
    active = stats["active_survivor"]
    adjusted_denom = total - equiv
    strength_denom = stats["killed"] + active

    stats["mutation_score"] = round(stats["killed"] / total, 3) if total else 0.0
    stats["mutation_score_adjusted"] = (
        round(stats["killed"] / adjusted_denom, 3) if adjusted_denom > 0 else 0.0
    )
    stats["test_strength"] = (
        round(stats["killed"] / strength_denom, 3) if strength_denom > 0 else 0.0
    )
    stats["elapsed_s"] = round(time.time() - started, 1)
    stats["_unclassified_rows"] = unclassified_rows
    return stats


def _archive_existing(schema_version: str = DATASET_SCHEMA_VERSION) -> Optional[Path]:
    """Snapshot the database before a run that would overwrite its rows.

    Collection upserts on (schema_version, module_key, suite, mutant_id), so
    re-running over an existing dataset silently replaces its `triage_status`
    and `outcome` values. That is usually what you want -- except when the
    previous run is the only record of how a since-changed rule behaved.

    Losing it costs a 30-60 minute Linux-only re-run to notice and cannot be
    recovered at all if the rule has already changed, so the snapshot is
    automatic rather than a flag someone has to remember.
    """
    path = db_path()
    if not path.exists():
        return None
    try:
        with connection() as conn:
            existing = conn.execute(
                "SELECT COUNT(*) FROM mutants WHERE dataset_schema_version = ?",
                (schema_version,),
            ).fetchone()[0]
    except Exception:
        return None
    if not existing:
        return None

    # Beside the database itself, not under SERVICE_ROOT -- a redirected
    # MUTATION_DB_PATH (tests, a scratch run) must not drop artifacts in the repo.
    archive_dir = path.parent / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = archive_dir / f"mutants_pre_{stamp}.db"
    shutil.copy2(path, dest)
    try:
        shown = dest.relative_to(SERVICE_ROOT)
    except ValueError:
        shown = dest
    print(f"archived {existing} existing row(s) -> {shown}")
    print("  (this run will overwrite them; the snapshot preserves the before/after)")
    return dest


def _triage_precision(schema_version: str = DATASET_SCHEMA_VERSION) -> Dict:
    """Measured precision of the Tier-0 arid filter, via the strong suite.

    A mutant flagged arid by the weak suite that the STRONG suite then KILLED
    is a demonstrated false positive: it was observable after all.

    The same-suite version of this check was a tautology and is removed. The
    collector overwrites `outcome` to the triage status for flagged rows, so
    `flagged AND outcome = killed` is identically zero and precision is 1.000
    by construction. It reported 1.000 on the v2 dataset while the oracle
    refuted 32 of 39 flags. See ADR-005.

    Requires both suites. With --suites weak only, returns measurable=False
    rather than a number it cannot compute.
    """
    FLAGGED = "w.triage_status IN ('equivalent_suspected','low_value')"
    with connection() as conn:
        n_strong = conn.execute(
            "SELECT COUNT(*) FROM mutants WHERE dataset_schema_version = ? "
            "AND suite = 'strong'", (schema_version,)).fetchone()[0]
        if not n_strong:
            return {"flagged": 0, "killed_anyway": 0, "precision": None,
                    "ci": (0.0, 1.0), "measurable": False,
                    "note": "no strong-suite rows; filter precision unmeasurable"}
        row = conn.execute(
            f"""SELECT COUNT(*) AS flagged,
                       SUM(s.outcome = 'killed') AS refuted
                FROM mutants w
                JOIN mutants s
                  ON s.module_key = w.module_key AND s.mutant_id = w.mutant_id
                 AND s.suite = 'strong'
                 AND s.dataset_schema_version = w.dataset_schema_version
                WHERE w.dataset_schema_version = ? AND w.suite = 'weak'
                  AND {FLAGGED}""",
            (schema_version,),
        ).fetchone()
    flagged = row[0] or 0
    killed_anyway = row[1] or 0
    correct = flagged - killed_anyway
    precision = (correct / flagged) if flagged else 1.0
    return {
        "flagged": flagged,
        "killed_anyway": killed_anyway,
        "precision": precision,
        "ci": wilson_interval(correct, flagged) if flagged else (1.0, 1.0),
        "measurable": True,
    }


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

    _archive_existing()

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
                  f"{s['equivalent_suspected'] + s['low_value']:>6} {s['unclassified']:>4} "
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

    # --- Gate 4: arid-filter precision, measured against the STRONG suite ---
    triage_stats = _triage_precision()
    if not triage_stats.get("measurable", True):
        print(f"\ntriage precision:     UNMEASURABLE "
              f"({triage_stats.get('note', 'no oracle')})")
        failures.append(
            "triage precision is UNMEASURABLE -- no strong-suite rows, so no "
            "oracle can refute a flag. An unmeasured gate is not a passed gate; "
            "collect both suites (--suites weak,strong) before training")
    elif triage_stats["flagged"] == 0:
        print("\ntriage precision:     n/a (0 mutants flagged)")
    else:
        prec = triage_stats["precision"]
        lo, hi = triage_stats["ci"]
        refuted = triage_stats["killed_anyway"]
        flagged = triage_stats["flagged"]
        print(f"\ntriage precision:     {prec:.3f} "
              f"[{lo:.3f}, {hi:.3f}]  "
              f"({flagged - refuted}/{flagged} flags the ORACLE did not kill)")
        if prec < MIN_TRIAGE_PRECISION:
            failures.append(
                f"triage precision {prec:.3f} below {MIN_TRIAGE_PRECISION:.2f} -- the "
                f"arid filter flagged {refuted} mutant(s) the STRONG suite went on to "
                f"KILL, so they were demonstrably killable, not equivalent. "
                f"mutation_score_adjusted cannot be trusted until this is fixed; "
                f"run scripts/analyze_collection.py for the per-rule breakdown")

    # --- tier validation: pre-registered ordinal trend, not invented bands ---
    #
    # The previous gate compared each module against a hand-picked numeric band
    # per tier. Those bands overlapped (0.50 satisfied both `weak` 0.10-0.60 and
    # `medium` 0.45-0.85, so adjacent tiers were indistinguishable) and
    # contradicted the percentages pre-registered in corpus/GENERATION.md.
    #
    # This uses only what was genuinely pre-registered -- the ordinal tier label
    # -- and tests one prediction: scores increase across weak < medium < strong.
    # It invents no numbers, so nothing can be fitted to the data it validates,
    # and being rank-based it gives the same verdict under either score metric.
    by_dir = {e["dir"]: e for e in entries}
    weak_runs = [s for s in all_stats if s["suite"] == "weak"]

    print("\ndeclared tier vs. measured weak-suite scores:")
    print(f"  {'module':<26} {'tier':<18} {'raw':>7} {'adjusted':>9} {'strength':>9}")
    for s in sorted(weak_runs, key=lambda x: x["module"]):
        tier = by_dir[s["module"]]["intended_suite_tier"]
        print(f"  {s['module']:<26} {tier:<18} {s['mutation_score']:>7.3f} "
              f"{s['mutation_score_adjusted']:>9.3f} {s['test_strength']:>9.3f}")

    for metric in ("mutation_score", "mutation_score_adjusted"):
        labelled = [(s["module"], by_dir[s["module"]]["intended_suite_tier"], s[metric])
                    for s in weak_runs]
        trend = rank_order_summary(labelled, TIER_ORDER)
        if trend.get("p_value") is None:
            print(f"\ntrend test ({metric}): {trend['method']}")
            continue
        print(f"\ntrend test ({metric}): "
              f"p = {trend['p_value']:.4f} "
              f"(exact, {trend['n_partitions']} partitions, n={trend['n']})")
        for tier in TIER_ORDER:
            t = trend["per_tier"][tier]
            if t["n"]:
                print(f"    {tier:<8} n={t['n']}  mean={t['mean']:.3f}  "
                      f"scores={t['scores']}")
        if metric == "mutation_score" and trend["p_value"] > 0.05:
            failures.append(
                f"tier ordering not supported (p={trend['p_value']:.4f}): measured "
                f"scores do not increase across {' < '.join(TIER_ORDER)}. Either a "
                f"suite does not match its declared tier, or the pipeline is "
                f"mismeasuring. Do NOT relabel tiers to fix this")

    # --- wide sanity rails: harness breakage, not tier miscalibration ---
    for s in weak_runs:
        score = s["mutation_score_adjusted"]
        if not (SANITY_MIN_SCORE <= score <= SANITY_MAX_SCORE):
            failures.append(f"{s['module']}: adjusted score {score:.3f} outside "
                            f"[{SANITY_MIN_SCORE}, {SANITY_MAX_SCORE}] -- suspect the "
                            f"harness or the suite, not the tier")
        uncovered_rate = s["uncovered"] / s["total"] if s["total"] else 0.0
        tier = by_dir[s["module"]]["intended_suite_tier"]
        if tier != "untested_function" and uncovered_rate > MAX_UNCOVERED_RATE_NON_08:
            failures.append(f"{s['module']}: {uncovered_rate:.0%} of mutants uncovered "
                            f"but tier is {tier}, not untested_function")

    # --- module 08: a coverage claim, checked as one ---
    untested = [s for s in weak_runs
                if by_dir[s["module"]]["intended_suite_tier"] == "untested_function"]
    if untested and len(weak_runs) > 1:
        rates = {s["module"]: (s["uncovered"] / s["total"] if s["total"] else 0.0)
                 for s in weak_runs}
        highest = max(rates, key=rates.get)
        for s in untested:
            print(f"\nuntested_function check: {s['module']} uncovered rate "
                  f"{rates[s['module']]:.1%}; corpus max is {highest} "
                  f"({rates[highest]:.1%})")
            if highest != s["module"]:
                failures.append(
                    f"{s['module']} is tiered untested_function but {highest} has a "
                    f"higher uncovered rate -- the tier claim does not hold")

    if failures:
        print("\nGATE FAILURES -- do not train on this dataset yet:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nall gates passed. next: python src/ml/train.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
