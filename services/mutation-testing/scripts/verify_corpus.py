"""
Corpus integrity check + manifest builder.
=============================================
Run before every collection, and in CI once that exists:

    cd services/mutation-testing && python scripts/verify_corpus.py

Checks, in order of how badly you want to know:

  1. EVERY TEST SUITE IS GREEN. Mutation testing against a failing suite is
     meaningless -- every mutant would be "killed" by a test that was already
     broken, and the resulting mutation score is noise. This single check
     justifies the script on its own.
  2. The strong suite is a superset of the weak one in test count. If it is not,
     the weak/strong oracle labelling is inverted somewhere.
  3. Recorded sha256 hashes still match the files, so a silently edited module
     cannot invalidate an already-collected dataset without being noticed.
  4. Declared metadata is complete and internally consistent.

Also writes corpus/MANIFEST.json, the index scripts/collect_dataset.py iterates.

Runs on any platform -- it never invokes mutmut.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

SERVICE_ROOT = Path(__file__).resolve().parents[1]
CORPUS = SERVICE_ROOT / "corpus"

VALID_TIERS = {"strong", "medium", "weak", "untested_function"}
REQUIRED_META_KEYS = {
    "id", "slug", "module_key", "domain", "loc", "control_flow_features",
    "target_operator_families", "intended_suite_tier", "omitted_test_cases",
    "provenance", "files",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_suite(module_dir: Path, suite: str) -> tuple:
    """Returns (passed, n_tests, output_tail)."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=str(module_dir), capture_output=True, text=True,
    )
    tail = (result.stdout or result.stderr).strip().splitlines()
    last = tail[-1] if tail else ""
    n = 0
    for token in last.split():
        if token.isdigit():
            n = int(token)
            break
    return result.returncode == 0, n, last


def update_hashes(d: Path, meta: Dict, slug: str) -> bool:
    """Rewrite meta.json's recorded hashes from the files on disk.

    Deliberately opt-in (`--update-hashes`). Recording a hash is an assertion
    that the artifact behind an already-collected dataset has not moved; making
    it automatic would turn the check into a no-op.
    """
    paths = {
        "source": d / f"{slug}.py",
        "tests_weak": d / "tests_weak" / f"test_{slug}.py",
        "tests_strong": d / "tests_strong" / f"test_{slug}.py",
    }
    changed = False
    files = meta.setdefault("files", {})
    for key, path in paths.items():
        if not path.exists():
            continue
        current = sha256(path)
        entry = files.setdefault(key, {})
        if entry.get("sha256") != current:
            entry["sha256"] = current
            entry.setdefault("path", str(path.relative_to(d)).replace("\\", "/"))
            changed = True
    if changed:
        src = paths["source"]
        if src.exists():
            meta["loc"] = len(src.read_text(encoding="utf-8").splitlines())
        (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return changed


def verify_module(d: Path, errors: List[str], warnings: List[str],
                  refresh_hashes: bool = False) -> Dict:
    meta_path = d / "meta.json"
    if not meta_path.exists():
        errors.append(f"{d.name}: no meta.json")
        return {}

    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    missing = REQUIRED_META_KEYS - set(meta)
    if missing:
        errors.append(f"{d.name}: meta.json missing keys {sorted(missing)}")
    if meta.get("intended_suite_tier") not in VALID_TIERS:
        errors.append(f"{d.name}: bad tier {meta.get('intended_suite_tier')!r}")
    if not meta.get("omitted_test_cases"):
        warnings.append(f"{d.name}: omitted_test_cases is empty -- the weak suite "
                        f"has no recorded ground truth")

    slug = meta.get("slug", "")
    src = d / f"{slug}.py"
    weak_dir, strong_dir = d / "tests_weak", d / "tests_strong"

    for p in (src, weak_dir, strong_dir):
        if not p.exists():
            errors.append(f"{d.name}: missing {p.name}")
            return meta

    # 3. hashes
    if refresh_hashes and update_hashes(d, meta, slug):
        print(f"  {d.name}: hashes refreshed")
    files = meta.get("files", {})
    for key, path in (("source", src),
                      ("tests_weak", weak_dir / f"test_{slug}.py"),
                      ("tests_strong", strong_dir / f"test_{slug}.py")):
        recorded = files.get(key, {}).get("sha256")
        if not recorded:
            warnings.append(f"{d.name}: no recorded hash for {key}")
        elif path.exists() and sha256(path) != recorded:
            errors.append(f"{d.name}: {key} hash mismatch -- file changed since "
                          f"meta.json was written; re-run write_metas or revert")

    # 1 + 2. suites green, strong >= weak
    weak_ok, weak_n, weak_out = run_suite(d, "tests_weak")
    strong_ok, strong_n, strong_out = run_suite(d, "tests_strong")

    if not weak_ok:
        errors.append(f"{d.name}: WEAK SUITE IS RED -- {weak_out}")
    if not strong_ok:
        errors.append(f"{d.name}: STRONG SUITE IS RED -- {strong_out}")
    if weak_ok and strong_ok and strong_n < weak_n:
        errors.append(f"{d.name}: strong suite has fewer tests ({strong_n}) than "
                      f"weak ({weak_n}) -- oracle labelling looks inverted")

    meta["_test_counts"] = {"weak": weak_n, "strong": strong_n}
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-hashes", action="store_true",
                        help="rewrite meta.json hashes from disk after an "
                             "intentional corpus edit (re-collect afterwards)")
    args = parser.parse_args()

    module_dirs = sorted(d for d in CORPUS.iterdir()
                         if d.is_dir() and (d / "meta.json").exists())
    if not module_dirs:
        print("no corpus modules found")
        return 1

    errors: List[str] = []
    warnings: List[str] = []
    entries: List[Dict] = []

    print(f"verifying {len(module_dirs)} corpus modules\n")
    for d in module_dirs:
        meta = verify_module(d, errors, warnings, refresh_hashes=args.update_hashes)
        if not meta:
            continue
        counts = meta.pop("_test_counts", {})
        print(f"  {d.name:<26} tier={meta.get('intended_suite_tier','?'):<18} "
              f"weak={counts.get('weak', 0):>3} strong={counts.get('strong', 0):>3}")
        entries.append({
            "id": meta.get("id"),
            "slug": meta.get("slug"),
            "dir": d.name,
            "module_key": meta.get("module_key"),
            "source": f"corpus/{d.name}/{meta.get('slug')}.py",
            "tests_weak": f"corpus/{d.name}/tests_weak",
            "tests_strong": f"corpus/{d.name}/tests_strong",
            "intended_suite_tier": meta.get("intended_suite_tier"),
            "target_operator_families": meta.get("target_operator_families", []),
            "provenance_generator": meta.get("provenance", {}).get("generator"),
            "test_counts": counts,
        })

    by_gen: Dict[str, int] = {}
    by_tier: Dict[str, int] = {}
    for e in entries:
        by_gen[e["provenance_generator"]] = by_gen.get(e["provenance_generator"], 0) + 1
        by_tier[e["intended_suite_tier"]] = by_tier.get(e["intended_suite_tier"], 0) + 1

    manifest = {
        "corpus_version": "v2",
        "n_modules": len(entries),
        "by_provenance": by_gen,
        "by_intended_tier": by_tier,
        "modules": entries,
    }
    (CORPUS / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                          encoding="utf-8")

    print(f"\nprovenance: {by_gen}")
    print(f"tiers:      {by_tier}")

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")

    if errors:
        print(f"\nFAILED with {len(errors)} error(s)")
        return 1
    print(f"\nOK -- wrote {CORPUS / 'MANIFEST.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
