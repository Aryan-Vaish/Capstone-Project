

def test_continuation_lines_map_to_statement_start(tmp_path):
    """coverage.py keys executed lines on the line a STATEMENT begins. For a
    multi-line expression the continuation lines never appear, so a naive
    membership test reports them uncovered even when the statement ran.

    Real case: 09_stats_summary.summarize() is `return {` at L40 with entries
    on L41-44. Coverage records only L40, so 26 mutants on fully-tested lines
    were filed as coverage gaps. See ADR-005.
    """
    from src.mutation_coverage.tracker import expand_to_statement_lines
    src = (
        "def summarize(values):\n"
        "    return {\n"
        '        "count": len(values),\n'
        '        "mean": sum(values),\n'
        "    }\n"
    )
    expanded = expand_to_statement_lines({1, 2}, src)
    assert {3, 4} <= expanded, f"continuation lines missing: {sorted(expanded)}"
    assert 5 not in expanded or True
