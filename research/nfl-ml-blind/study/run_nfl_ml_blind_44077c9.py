"""External runner. Does not modify the locked tree.

Logs the coverage and selection records the locked script would otherwise
drop on a raised failure, then calls the locked run_locked_eval unchanged.
"""
import json
import os
import sys
import traceback

sys.path.insert(0, "/agent/repos/nfl-app/backend")
os.chdir("/agent/repos/nfl-app/backend")
os.environ["NFL_ML_BLIND_LOCKED_SHA"] = "44077c965fc4e430f88aa859be8ad2536811554c"

import scripts.eval_nfl_ml_blind as ev


def _dump(label, payload):
    print(label + " " + json.dumps(payload, default=str), flush=True)


_coverage = ev.assert_prefit_coverage


def _coverage_logged(*args, **kwargs):
    try:
        result = _coverage(*args, **kwargs)
    except Exception:
        print("COVERAGE_RECORD_FAILED", flush=True)
        raise
    _dump("COVERAGE_RECORD", result)
    return result


_selection = ev._selection_decision


def _selection_logged(rows, sched):
    n_rows = len(rows)
    n_close = sum(1 for row in rows if row.get("close") is not None)
    clusters = sorted({str(row.get("cluster")) for row in rows})
    try:
        decision = _selection(rows, sched)
    except Exception:
        _dump("SELECTION_RECORD_FAILED", {
            "n_rows": n_rows,
            "n_with_close": n_close,
            "n_clusters_in_rows": len(clusters),
            "clusters": clusters,
        })
        raise
    _dump("SELECTION_RECORD", {
        "n_rows": n_rows,
        "n_with_close": n_close,
        "n_clusters_in_rows": len(clusters),
        "decision": decision,
    })
    return decision


ev.assert_prefit_coverage = _coverage_logged
ev._selection_decision = _selection_logged

try:
    summary = ev.run_locked_eval()
except Exception:
    traceback.print_exc()
    raise SystemExit(1)
_dump("FINAL_RECORD", summary)
print("EXIT:0", flush=True)
