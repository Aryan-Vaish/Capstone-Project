"""
Collection diagnostic -- read-only, no mutmut, any platform.
===============================================================

    python scripts/analyze_collection.py                 # print report
    python scripts/analyze_collection.py --json-out      # also write reports/analysis_<date>.json

Answers the questions a raw collection log cannot:

  * is the dataset internally coherent enough to draw conclusions from at all?
  * how often is the Tier-0 equivalence filter WRONG, measured rather than assumed?
  * is the strong suite actually an adequate oracle, or does it have blind spots?
  * how many genuine test gaps does the weak/strong pairing actually yield?

Opens the database read-only and, unless `--json-out` is given, writes nothing.
Depends only on `sqlite3` and `math` -- the collection runs on WSL and this is
often run on Windows, so every extra dependency is one more thing to fail.

WHY THIS EXISTS
---------------
The first real collection (670 mutants, 10 modules x 2 suites) passed the
operator-classification and line-resolution gates convincingly (0.1% and 0%),
but failed tier validation for 6 of 10 modules. Diagnosing that from the log
alone was impossible: the log reports scores, and the question was whether the
scores meant anything. Section 2 below is the query that settles it.
"""
import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SERVICE_ROOT = Path(__file__).resolve().parents[1]
for p in (str(SERVICE_ROOT.parents[1]), str(SERVICE_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from evaluation.stats import rank_order_summary, wilson_interval  # noqa: E402
from storage.db import DATASET_SCHEMA_VERSION, db_path            # noqa: E402

TIER_ORDER = ("weak", "medium", "strong")
REPORTS = SERVICE_ROOT / "reports"
CORPUS = SERVICE_ROOT / "corpus"

SURVIVED = ("active_survivor", "uncovered", "equivalent_suspected")


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def open_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(
            f"ERROR: no database at {path}\n"
            f"       Set MUTATION_DB_PATH, or run scripts/collect_dataset.py first."
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_tiers() -> Dict[str, str]:
    """module_slug -> intended_suite_tier, from the pre-registered meta.json."""
    tiers = {}
    for meta_path in sorted(CORPUS.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        tiers[meta_path.parent.name] = meta.get("intended_suite_tier", "unknown")
    return tiers


# --------------------------------------------------------------------------
# 0. Integrity -- everything downstream is conditional on this
# --------------------------------------------------------------------------

def section_integrity(conn, version: str) -> Dict:
    rule("0. INTEGRITY  (all later sections are conditional on this)")
    out: Dict = {"warnings": []}

    scope = conn.execute(
        """SELECT dataset_schema_version AS v, COUNT(*) AS n,
                  COUNT(DISTINCT run_id) AS runs,
                  MIN(created_at) AS first, MAX(created_at) AS last
           FROM mutants GROUP BY 1""").fetchall()
    print(f"  {'schema':<8} {'rows':>6} {'runs':>6}  collected")
    for r in scope:
        print(f"  {r['v']:<8} {r['n']:>6} {r['runs']:>6}  {r['first']} .. {r['last']}")
    out["scope"] = [dict(r) for r in scope]

    # --- cross-suite symmetry -------------------------------------------------
    # generator.py matches only `(killed|survived)`, so mutmut's timeout /
    # suspicious / skipped statuses are silently dropped. A mutant may therefore
    # be present under one suite and absent under the other, which would make
    # every weak->strong join quietly wrong rather than obviously wrong.
    rows = conn.execute(
        """SELECT module_slug, suite, mutant_id FROM mutants
           WHERE dataset_schema_version = ?""", (version,)).fetchall()
    ids = defaultdict(lambda: defaultdict(set))
    for r in rows:
        ids[r["module_slug"]][r["suite"]].add(r["mutant_id"])

    print(f"\n  cross-suite mutant sets:")
    print(f"  {'module':<26} {'weak':>6} {'strong':>7} {'both':>6} {'weak-only':>10} {'strong-only':>12}")
    asymmetric = []
    sym = {}
    for slug in sorted(ids):
        w, s = ids[slug].get("weak", set()), ids[slug].get("strong", set())
        both, wo, so = len(w & s), len(w - s), len(s - w)
        sym[slug] = {"weak": len(w), "strong": len(s), "both": both,
                     "weak_only": wo, "strong_only": so}
        flag = "" if (wo == 0 and so == 0) else "  <-- ASYMMETRIC"
        print(f"  {slug:<26} {len(w):>6} {len(s):>7} {both:>6} {wo:>10} {so:>12}{flag}")
        if wo or so:
            asymmetric.append(slug)
    out["symmetry"] = sym
    out["asymmetric_modules"] = asymmetric
    if asymmetric:
        out["warnings"].append(
            f"{len(asymmetric)} module(s) have different mutant sets across suites "
            f"({', '.join(asymmetric)}). mutmut statuses other than killed/survived "
            f"are dropped by generator.py. Join-based sections are UNRELIABLE for these.")

    # --- killed but not covered: a logical contradiction -----------------------
    contra = conn.execute(
        """SELECT module_slug, suite, COUNT(*) AS n FROM mutants
           WHERE dataset_schema_version = ? AND outcome = 'killed' AND covered = 0
           GROUP BY 1, 2""", (version,)).fetchall()
    total_contra = sum(r["n"] for r in contra)
    print(f"\n  killed-but-uncovered (must be 0): {total_contra}")
    out["killed_but_uncovered"] = total_contra
    if total_contra:
        for r in contra:
            print(f"    {r['module_slug']:<26} {r['suite']:<7} {r['n']}")
        out["warnings"].append(
            f"{total_contra} mutant(s) are marked killed on a line coverage says was "
            f"never executed. A test cannot kill a mutant it does not run, so the line "
            f"resolver is mapping some mutants to the wrong line -- which corrupts the "
            f"uncovered bucket and every score derived from it.")

    # --- provenance homogeneity ----------------------------------------------
    prov = conn.execute(
        """SELECT COUNT(DISTINCT pipeline_git_sha) AS shas,
                  COUNT(DISTINCT taxonomy_version) AS taxes
           FROM mutants WHERE dataset_schema_version = ?""", (version,)).fetchone()
    print(f"  distinct pipeline SHAs: {prov['shas']}   taxonomy versions: {prov['taxes']}")
    out["provenance"] = dict(prov)
    if (prov["shas"] or 0) > 1 or (prov["taxes"] or 0) > 1:
        out["warnings"].append(
            "Rows were collected under more than one pipeline version. Comparing or "
            "joining them compares different mutant semantics.")

    # --- line resolution mix --------------------------------------------------
    res = conn.execute(
        """SELECT line_resolution, COUNT(*) AS n FROM mutants
           WHERE dataset_schema_version = ? GROUP BY 1 ORDER BY n DESC""",
        (version,)).fetchall()
    print(f"\n  line resolution:")
    for r in res:
        print(f"    {r['line_resolution'] or '(none)':<26} {r['n']:>5}")
    out["line_resolution"] = {r["line_resolution"]: r["n"] for r in res}
    return out


# --------------------------------------------------------------------------
# 1. Per module x suite ledger
# --------------------------------------------------------------------------

def section_ledger(conn, version: str) -> Dict:
    rule("1. PER-MODULE LEDGER")
    rows = conn.execute(
        """SELECT module_slug, suite, COUNT(*) AS total,
                  SUM(outcome = 'killed')               AS killed,
                  SUM(outcome = 'active_survivor')      AS active,
                  SUM(outcome = 'uncovered')            AS uncovered,
                  SUM(outcome = 'equivalent_suspected') AS equiv_outcome,
                  SUM(triage_status = 'equivalent_suspected') AS flagged_all
           FROM mutants WHERE dataset_schema_version = ?
           GROUP BY 1, 2 ORDER BY 1, 2""", (version,)).fetchall()

    print(f"  {'module':<26} {'suite':<7} {'tot':>4} {'kill':>5} {'act':>4} {'unc':>4} "
          f"{'equiv':>6} {'flag':>5} {'raw':>7} {'adj':>7} {'str':>7}")
    ledger = {}
    for r in rows:
        total, killed = r["total"], r["killed"] or 0
        equiv, active = r["equiv_outcome"] or 0, r["active"] or 0
        # Formulas transcribed verbatim from shared/schemas/mutation_schema.py
        # so the two code paths cannot report different numbers under one name.
        raw = killed / total if total else 0.0
        adj_denom = total - equiv
        adj = killed / adj_denom if adj_denom > 0 else 0.0
        strength_denom = killed + active
        strength = killed / strength_denom if strength_denom > 0 else 0.0
        print(f"  {r['module_slug']:<26} {r['suite']:<7} {total:>4} {killed:>5} "
              f"{active:>4} {r['uncovered'] or 0:>4} {equiv:>6} {r['flagged_all'] or 0:>5} "
              f"{raw:>7.3f} {adj:>7.3f} {strength:>7.3f}")
        ledger[f"{r['module_slug']}/{r['suite']}"] = {
            "module": r["module_slug"], "suite": r["suite"], "total": total,
            "killed": killed, "active_survivor": active,
            "uncovered": r["uncovered"] or 0, "equivalent_suspected": equiv,
            "flagged_all": r["flagged_all"] or 0,
            "mutation_score": round(raw, 4),
            "mutation_score_adjusted": round(adj, 4),
            "test_strength": round(strength, 4),
        }
    print("\n  equiv = flagged AND a covered survivor (what the adjusted score removes)")
    print("  flag  = flagged regardless of outcome (includes killed and uncovered)")
    return ledger


# --------------------------------------------------------------------------
# 2. Triage false positives -- the headline measurement
# --------------------------------------------------------------------------

def section_triage_fp(conn, version: str) -> Dict:
    rule("2. EQUIVALENCE-FILTER PRECISION  (measured, no oracle needed)")
    print("  A mutant the filter called unkillable that the SAME suite then KILLED is a")
    print("  demonstrated false positive. This matters because mutation_score_adjusted")
    print("  subtracts the filter's output from its denominator -- an over-flagging")
    print("  filter launders real test failures into an uncounted bucket.\n")

    rows = conn.execute(
        """SELECT triage_reason_code AS reason, COUNT(*) AS flagged,
                  SUM(outcome = 'killed') AS killed_anyway
           FROM mutants
           WHERE dataset_schema_version = ? AND suite = 'weak'
             AND triage_status = 'equivalent_suspected'
           GROUP BY 1 ORDER BY flagged DESC""", (version,)).fetchall()

    if not rows:
        print("  no mutants flagged equivalent under the weak suite")
        return {"flagged": 0, "by_reason": {}}

    print(f"  {'reason code':<26} {'flagged':>8} {'killed':>7} {'precision':>10}  95% CI")
    by_reason, tot_flag, tot_killed = {}, 0, 0
    for r in rows:
        flagged, killed = r["flagged"], r["killed_anyway"] or 0
        correct = flagged - killed
        prec = correct / flagged if flagged else 1.0
        lo, hi = wilson_interval(correct, flagged)
        print(f"  {r['reason'] or '(none)':<26} {flagged:>8} {killed:>7} "
              f"{prec:>10.3f}  [{lo:.3f}, {hi:.3f}]")
        by_reason[r["reason"]] = {"flagged": flagged, "killed_anyway": killed,
                                  "precision": round(prec, 4),
                                  "ci": [round(lo, 4), round(hi, 4)]}
        tot_flag += flagged
        tot_killed += killed

    correct = tot_flag - tot_killed
    prec = correct / tot_flag if tot_flag else 1.0
    lo, hi = wilson_interval(correct, tot_flag)
    print(f"  {'-' * 66}")
    print(f"  {'POOLED':<26} {tot_flag:>8} {tot_killed:>7} {prec:>10.3f}  [{lo:.3f}, {hi:.3f}]")

    # Eyeball evidence: which literals is the filter actually calling arid?
    samples = conn.execute(
        """SELECT module_slug, function_name, line_number, outcome, removed_line
           FROM mutants
           WHERE dataset_schema_version = ? AND suite = 'weak'
             AND triage_status = 'equivalent_suspected'
             AND removed_line IS NOT NULL
           ORDER BY (outcome = 'killed') DESC LIMIT 15""", (version,)).fetchall()
    if samples:
        print(f"\n  sample of flagged mutants (killed ones first -- these are the errors):")
        for r in samples:
            src = (r["removed_line"] or "").strip()[:52]
            print(f"    [{r['outcome']:<20}] {r['module_slug']:<22} "
                  f"{(r['function_name'] or '?'):<20} {src}")
    elif tot_flag:
        print("\n  (removed_line not stored in this dataset -- re-collect to enable "
              "the sample dump and offline triage replay)")

    return {"flagged": tot_flag, "killed_anyway": tot_killed,
            "precision": round(prec, 4), "ci": [round(lo, 4), round(hi, 4)],
            "by_reason": by_reason}


# --------------------------------------------------------------------------
# 3 + 4. Oracle adequacy, then oracle-refuted flags
# --------------------------------------------------------------------------

def section_oracle(conn, version: str, asymmetric: List[str]) -> Dict:
    rule("3. ORACLE ADEQUACY  (does the strong suite earn the name?)")
    print("  Every gap label rests on the strong suite killing what the weak one misses.")
    print("  A mutant the STRONG suite never even executes is a blind spot: its")
    print("  'survived' verdict carries no information, so it cannot be labelled.\n")

    rows = conn.execute(
        """SELECT module_slug, COUNT(*) AS total,
                  SUM(outcome = 'killed')          AS killed,
                  SUM(outcome = 'active_survivor') AS active,
                  SUM(outcome = 'uncovered')       AS blind
           FROM mutants WHERE dataset_schema_version = ? AND suite = 'strong'
           GROUP BY 1 ORDER BY 1""", (version,)).fetchall()

    print(f"  {'module':<26} {'total':>6} {'killed':>7} {'survived':>9} {'blind':>6} {'kill rate':>10}")
    adequacy = {}
    for r in rows:
        total = r["total"]
        kr = (r["killed"] or 0) / total if total else 0.0
        mark = "  <-- weak oracle" if kr < 0.6 else ""
        print(f"  {r['module_slug']:<26} {total:>6} {r['killed'] or 0:>7} "
              f"{r['active'] or 0:>9} {r['blind'] or 0:>6} {kr:>10.3f}{mark}")
        adequacy[r["module_slug"]] = {
            "total": total, "killed": r["killed"] or 0,
            "active_survivor": r["active"] or 0, "blind_spots": r["blind"] or 0,
            "kill_rate": round(kr, 4)}

    rule("4. ORACLE-REFUTED FLAGS")
    print("  Flagged equivalent under weak, but KILLED by the strong suite.")
    print("  A second, independent measurement of filter error.\n")

    joined = conn.execute(
        """SELECT w.module_slug AS module, w.triage_reason_code AS reason,
                  COUNT(*) AS flagged,
                  SUM(s.outcome = 'killed') AS refuted,
                  SUM(s.mutant_id IS NULL)  AS no_counterpart
           FROM mutants w
           LEFT JOIN mutants s
                  ON s.module_key = w.module_key AND s.mutant_id = w.mutant_id
                 AND s.suite = 'strong'
                 AND s.dataset_schema_version = w.dataset_schema_version
           WHERE w.suite = 'weak' AND w.dataset_schema_version = ?
             AND w.triage_status = 'equivalent_suspected'
             AND w.outcome != 'killed'
           GROUP BY 1, 2 ORDER BY flagged DESC""", (version,)).fetchall()

    refuted_out = {}
    if not joined:
        print("  nothing flagged equivalent survived the weak suite")
    else:
        print(f"  {'module':<26} {'reason':<24} {'flagged':>8} {'refuted':>8} {'no pair':>8}")
        for r in joined:
            note = "  UNRELIABLE" if r["module"] in asymmetric else ""
            print(f"  {r['module']:<26} {(r['reason'] or '')[:24]:<24} "
                  f"{r['flagged']:>8} {r['refuted'] or 0:>8} "
                  f"{r['no_counterpart'] or 0:>8}{note}")
            refuted_out[f"{r['module']}/{r['reason']}"] = {
                "flagged": r["flagged"], "refuted_by_oracle": r["refuted"] or 0,
                "no_counterpart": r["no_counterpart"] or 0}
    print("\n  NOTE: this measures PRECISION only. Recall is unmeasurable -- there is no")
    print("  ground truth for 'truly equivalent', and surviving both suites is evidence")
    print("  of equivalence, not proof of it.")
    return {"adequacy": adequacy, "refuted": refuted_out}


# --------------------------------------------------------------------------
# 5. Operators
# --------------------------------------------------------------------------

def section_operators(conn, version: str) -> Dict:
    rule("5. OPERATOR DISTRIBUTION AND KILL RATES")
    rows = conn.execute(
        """SELECT operator, suite, COUNT(*) AS n,
                  SUM(outcome = 'killed') AS killed,
                  SUM(triage_status = 'equivalent_suspected') AS flagged
           FROM mutants WHERE dataset_schema_version = ?
           GROUP BY 1, 2""", (version,)).fetchall()

    merged: Dict[str, Dict] = defaultdict(lambda: {"weak": None, "strong": None, "n": 0})
    for r in rows:
        e = merged[r["operator"]]
        e[r["suite"]] = {"n": r["n"], "killed": r["killed"] or 0,
                         "flagged": r["flagged"] or 0}
        e["n"] += r["n"]

    print(f"  {'operator':<28} {'n':>5} {'weak kill':>10} {'strong kill':>12} {'flagged':>8}")
    out = {}
    for op, e in sorted(merged.items(), key=lambda kv: -kv[1]["n"]):
        w, s = e["weak"], e["strong"]
        wk = f"{w['killed'] / w['n']:.3f}" if w and w["n"] else "  -  "
        sk = f"{s['killed'] / s['n']:.3f}" if s and s["n"] else "  -  "
        flagged = (w or {}).get("flagged", 0)
        print(f"  {op:<28} {e['n']:>5} {wk:>10} {sk:>12} {flagged:>8}")
        out[op] = {"n": e["n"], "weak": w, "strong": s}

    fam = conn.execute(
        """SELECT operator_family, COUNT(DISTINCT module_slug) AS modules, COUNT(*) AS n
           FROM mutants WHERE dataset_schema_version = ? GROUP BY 1 ORDER BY n DESC""",
        (version,)).fetchall()
    print(f"\n  family coverage (GENERATION.md requires >= 3 modules per family):")
    thin = []
    for r in fam:
        mark = "  <-- THIN" if r["modules"] < 3 else ""
        print(f"    {r['operator_family']:<20} {r['modules']:>3} modules  {r['n']:>5} mutants{mark}")
        if r["modules"] < 3:
            thin.append(r["operator_family"])
    return {"operators": out, "thin_families": thin}


# --------------------------------------------------------------------------
# 6. Oracle gap labels
# --------------------------------------------------------------------------

def section_gap_labels(conn, version: str, asymmetric: List[str]) -> Dict:
    rule("6. ORACLE GAP LABELS  (the ground truth precision@k will need)")
    rows = conn.execute(
        """SELECT w.module_slug AS module, w.outcome AS weak_outcome,
                  s.outcome AS strong_outcome, COUNT(*) AS n
           FROM mutants w
           JOIN mutants s
             ON s.module_key = w.module_key AND s.mutant_id = w.mutant_id
            AND s.suite = 'strong' AND s.dataset_schema_version = w.dataset_schema_version
           WHERE w.suite = 'weak' AND w.dataset_schema_version = ?
           GROUP BY 1, 2, 3""", (version,)).fetchall()

    per_module: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        m = per_module[r["module"]]
        if r["weak_outcome"] == "active_survivor" and r["strong_outcome"] == "killed":
            m["true_gap_covered"] += r["n"]
        elif r["weak_outcome"] == "uncovered" and r["strong_outcome"] == "killed":
            m["true_gap_coverage"] += r["n"]
        elif r["weak_outcome"] == "equivalent_suspected" and r["strong_outcome"] == "killed":
            m["triage_fp"] += r["n"]
        elif r["weak_outcome"] in SURVIVED and r["strong_outcome"] != "killed":
            m["unlabelable"] += r["n"]
        elif r["weak_outcome"] == "killed":
            m["not_a_gap"] += r["n"]

    print(f"  {'module':<26} {'TRUE GAP':>9} {'cov gap':>8} {'triage FP':>10} "
          f"{'unlabel':>8} {'not gap':>8}")
    totals = defaultdict(int)
    for slug in sorted(per_module):
        m = per_module[slug]
        note = "  UNRELIABLE" if slug in asymmetric else ""
        print(f"  {slug:<26} {m['true_gap_covered']:>9} {m['true_gap_coverage']:>8} "
              f"{m['triage_fp']:>10} {m['unlabelable']:>8} {m['not_a_gap']:>8}{note}")
        for k, v in m.items():
            totals[k] += v

    n_pos = totals["true_gap_covered"]
    print(f"  {'-' * 74}")
    print(f"  {'TOTAL':<26} {n_pos:>9} {totals['true_gap_coverage']:>8} "
          f"{totals['triage_fp']:>10} {totals['unlabelable']:>8} {totals['not_a_gap']:>8}")
    print(f"\n  TRUE GAP = survives the weak suite, killed by the oracle, and was")
    print(f"  reachable. This is the ONLY positive class the ranker can be scored on:")
    print(f"  n = {n_pos}. Coverage gaps are excluded deliberately -- their label is")
    print(f"  readable from the `covered` feature alone, which is the tautology")
    print(f"  db.fetch_training_population exists to keep out of training.")
    if n_pos < 30:
        print(f"\n  WARNING: {n_pos} positives is a small basis for precision@k or NDCG.")
        print(f"  Any ranking claim must state this sample size explicitly.")
    return {"per_module": {k: dict(v) for k, v in per_module.items()},
            "totals": dict(totals), "n_positives": n_pos}


# --------------------------------------------------------------------------
# 7. Tier validation replay
# --------------------------------------------------------------------------

def section_tiers(ledger: Dict, tiers: Dict[str, str]) -> Dict:
    rule("7. TIER VALIDATION  (old bands vs pre-registered ordinal trend)")

    # The original bands, kept only to show what they did. They overlap --
    # a 0.50 score satisfies both `weak` and `medium` -- so they could never
    # discriminate adjacent tiers.
    old_bands = {"strong": (0.80, 1.01), "medium": (0.45, 0.85),
                 "weak": (0.10, 0.60), "untested_function": (0.0, 0.80)}

    weak_rows = [v for k, v in ledger.items() if v["suite"] == "weak"]
    print(f"  {'module':<26} {'tier':<18} {'raw':>7} {'verdict (old band)':<20} {'adjusted':>9}")
    verdicts, labelled_raw, labelled_adj = {}, [], []
    for v in sorted(weak_rows, key=lambda x: x["module"]):
        tier = tiers.get(v["module"], "unknown")
        lo, hi = old_bands.get(tier, (0.0, 1.01))
        raw = v["mutation_score"]
        if raw < lo:
            verdict = "BELOW_FLOOR"
        elif raw > hi:
            verdict = "ABOVE_CEILING"
        else:
            verdict = "OK"
        verdicts[v["module"]] = verdict
        print(f"  {v['module']:<26} {tier:<18} {raw:>7.3f} {verdict:<20} "
              f"{v['mutation_score_adjusted']:>9.3f}")
        # Pass EVERY module through, including untested_function. rank_order_summary
        # drops the ones off the ordinal and records them under `excluded`, so the
        # exclusion is reported rather than silently applied here.
        labelled_raw.append((v["module"], tier, raw))
        labelled_adj.append((v["module"], tier, v["mutation_score_adjusted"]))

    below = [m for m, x in verdicts.items() if x == "BELOW_FLOOR"]
    above = [m for m, x in verdicts.items() if x == "ABOVE_CEILING"]
    print(f"\n  old-band failures: {len(below)} below floor, {len(above)} above ceiling")
    print(f"  PRE-REGISTERED PREDICTION: every failure is BELOW_FLOOR.")
    if above:
        print(f"  ** REFUTED for {', '.join(above)} -- a ceiling failure is not explained")
        print(f"  ** by equivalent-mutant deflation, and the adjusted metric makes it worse.")
    elif below:
        print(f"  Prediction HOLDS: all {len(below)} failures were below floor.")

    out = {"verdicts": verdicts, "below_floor": below, "above_ceiling": above}
    for name, labelled in (("mutation_score", labelled_raw),
                           ("mutation_score_adjusted", labelled_adj)):
        trend = rank_order_summary(labelled, TIER_ORDER)
        out[f"trend_{name}"] = trend
        if trend.get("p_value") is None:
            print(f"\n  trend ({name}): {trend['method']}")
            continue
        print(f"\n  trend ({name}): p = {trend['p_value']:.4f} "
              f"({trend['method']}, {trend['n_partitions']} partitions)")
        for tier in TIER_ORDER:
            t = trend["per_tier"][tier]
            if t["n"]:
                print(f"    {tier:<8} n={t['n']} mean={t['mean']:.3f} scores={t['scores']}")
    print("\n  The trend test uses only the pre-registered ORDINAL labels, so nothing")
    print("  can be fitted to the data it validates. Being rank-based, it returns the")
    print("  same verdict under either metric unless the metric reorders modules.")
    return out


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="path to mutants.db")
    ap.add_argument("--schema-version", default=DATASET_SCHEMA_VERSION)
    ap.add_argument("--json-out", action="store_true",
                    help="also write reports/analysis_<date>.json (the only write)")
    args = ap.parse_args()

    path = Path(args.db) if args.db else db_path()
    conn = open_ro(path)
    version = args.schema_version

    n = conn.execute("SELECT COUNT(*) FROM mutants WHERE dataset_schema_version = ?",
                     (version,)).fetchone()[0]
    print(f"database: {path}")
    print(f"schema:   {version}   rows: {n}")
    if n == 0:
        print(f"\nNo rows at schema {version}. Run scripts/collect_dataset.py first.")
        return 1

    tiers = load_tiers()
    report: Dict = {"db": str(path), "schema_version": version, "n_rows": n,
                    "generated_at": datetime.now(timezone.utc).isoformat()}

    integrity = section_integrity(conn, version)
    report["integrity"] = integrity
    asym = integrity["asymmetric_modules"]

    report["ledger"] = section_ledger(conn, version)
    report["triage_precision"] = section_triage_fp(conn, version)
    report["oracle"] = section_oracle(conn, version, asym)
    report["operators"] = section_operators(conn, version)
    report["gap_labels"] = section_gap_labels(conn, version, asym)
    report["tiers"] = section_tiers(report["ledger"], tiers)
    conn.close()

    rule("SUMMARY")
    tp = report["triage_precision"]
    if tp.get("flagged"):
        print(f"  equivalence-filter precision : {tp['precision']:.3f} "
              f"({tp['killed_anyway']} of {tp['flagged']} flags were killed anyway)")
        if tp["precision"] < 0.85:
            print(f"  -> BELOW the 0.85 gate. mutation_score_adjusted is NOT trustworthy;")
            print(f"     narrow the filter before using it to judge anything.")
    print(f"  true gaps available          : {report['gap_labels']['n_positives']}")
    tr = report["tiers"].get("trend_mutation_score", {})
    if tr.get("p_value") is not None:
        print(f"  tier ordering (raw score)    : p = {tr['p_value']:.4f}")

    if integrity["warnings"]:
        print(f"\n  INTEGRITY WARNINGS ({len(integrity['warnings'])}):")
        for w in integrity["warnings"]:
            print(f"   ! {w}")

    if args.json_out:
        REPORTS.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        out = REPORTS / f"analysis_{stamp}.json"
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {out}")
        print("Send this file back -- it is the whole diagnosis in one artifact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
