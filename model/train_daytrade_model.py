"""
Weekly stage: retrains the day-trade RSI-alone model (attempt 5, see
model/README.md and model/daytrade_live_shortlist.py's build_shortlist()
for the live local scoring counterpart this used to be inline in) and
publishes a lookup-table artifact to GitHub.

Exported as a lookup table (predicted probability at a fine grid of
rsi_14 values), not a pickled sklearn model, deliberately: this is a
single-feature model, so a fine grid is a complete, exact
representation of it, and it lets the daily cloud routine
(cloud/daytrade_shortlist.py) score fresh data with only numpy's
np.interp -- no scikit-learn dependency or model-deserialization/
version-compatibility risk in that environment.
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from config import PROJECT_DIR  # noqa: E402
from daytrade_features import build_feature_panel  # noqa: E402
from publish_to_github import commit_and_push  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler

HORIZON_DAYS = 5
GAIN_THRESHOLD = 0.0125
LABEL_COL = f"label_{GAIN_THRESHOLD * 100:g}pct_{HORIZON_DAYS}d"
BUCKETS = ["large", "mid", "small"]
RSI_GRID = [round(x * 0.5, 1) for x in range(0, 201)]  # 0.0, 0.5, ..., 100.0

MODEL_SYNC_DIR = PROJECT_DIR / "data" / "github_sync" / "daytrade_model"


def train_and_export():
    print("[train_daytrade_model] building training panel...", flush=True)
    train_panel = build_feature_panel(horizon_days=HORIZON_DAYS, gain_threshold=GAIN_THRESHOLD)
    print(f"[train_daytrade_model] panel: {len(train_panel)} rows, "
          f"latest date {train_panel['feature_date'].max().date()}", flush=True)

    result = {"as_of_date": date.today().isoformat(), "buckets": {}}
    grid = np.array(RSI_GRID).reshape(-1, 1)

    for bucket in BUCKETS:
        bucket_train = train_panel[train_panel["cap_bucket"] == bucket]
        X_train = bucket_train[["rsi_14"]].values
        y_train = bucket_train[LABEL_COL].values
        scaler = StandardScaler().fit(X_train)
        model = HistGradientBoostingClassifier(max_depth=4, max_iter=150)
        model.fit(scaler.transform(X_train), y_train)

        proba_grid = model.predict_proba(scaler.transform(grid))[:, 1]

        q = bucket_train["rsi_14"].quantile([1 / 12, 2 / 12, 0.25])
        result["buckets"][bucket] = {
            "tertile_lo": round(float(q.iloc[0]), 4),
            "tertile_mid": round(float(q.iloc[1]), 4),
            "q1_cutoff": round(float(q.iloc[2]), 4),
            "rsi_grid": RSI_GRID,
            "proba_grid": [round(float(p), 6) for p in proba_grid],
        }
        print(f"[train_daytrade_model] {bucket}: {len(bucket_train)} training rows, "
              f"q1_cutoff={result['buckets'][bucket]['q1_cutoff']}", flush=True)

    MODEL_SYNC_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MODEL_SYNC_DIR / f"{result['as_of_date']}.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(f"[train_daytrade_model] wrote {out_path}", flush=True)
    return out_path


def run_train_daytrade_model_stage():
    out_path = train_and_export()
    status = commit_and_push([out_path], f"Day-trade model: {out_path.stem}")
    print(f"[train_daytrade_model] publish status: {status}", flush=True)
    return {"status": status, "path": str(out_path)}


if __name__ == "__main__":
    print(run_train_daytrade_model_stage())
