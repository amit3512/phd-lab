from pathlib import Path
import joblib
import numpy as np
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.metrics import accuracy_score, classification_report

CSV = Path("network_metrics_20261015.csv")
MODEL_FILE = Path("congestion_rf.joblib")

# Deliberately low threshold for the initial experiment.
THRESHOLD_MBPS = 0.1

CATEGORICAL = ["switch", "port"]
NUMERIC = [
    "rx_mbps", "tx_mbps", "rx_pps", "tx_pps",
    "rx_drops", "tx_drops", "rx_errors", "tx_errors",
]
FEATURES = CATEGORICAL + NUMERIC

if not CSV.exists():
    raise FileNotFoundError(f"Dataset not found: {CSV.resolve()}")

df = pd.read_csv(CSV)
df["timestamp_utc"] = pd.to_datetime(
    df["timestamp_utc"], utc=True, errors="coerce"
)
df = df.dropna(subset=["timestamp_utc"])
df = df.sort_values(["switch", "port", "timestamp_utc"]).copy()

for col in NUMERIC:
    df[col] = pd.to_numeric(df[col], errors="coerce")

# Missing initial rate measurements are treated as zero for this prototype.
df[NUMERIC] = df[NUMERIC].replace(
    [np.inf, -np.inf], np.nan
).fillna(0)

# Label whether this port exceeds the threshold at the NEXT sample.
df["peak_mbps"] = df[["rx_mbps", "tx_mbps"]].max(axis=1)
df["next_peak_mbps"] = (
    df.groupby(["switch", "port"])["peak_mbps"].shift(-1)
)
df["target"] = (
    df["next_peak_mbps"] >= THRESHOLD_MBPS
).astype("Int64")

data = df.dropna(subset=["next_peak_mbps"]).copy()
data["target"] = data["target"].astype(int)

if len(data) < 100:
    raise RuntimeError(f"Too few training examples: {len(data)}")

print(f"Dataset: {CSV.resolve()}")
print(f"Usable examples: {len(data)}")
print(f"Congestion threshold: {THRESHOLD_MBPS} Mbps")
print("Label distribution (0=not congested, 1=congested):")
print(data["target"].value_counts().sort_index().to_string())

# Chronological split to avoid randomly mixing future observations
# into the training set.
cutoff = data["timestamp_utc"].quantile(0.8)
train = data[data["timestamp_utc"] < cutoff]
test = data[data["timestamp_utc"] >= cutoff]

if train.empty or test.empty:
    raise RuntimeError("Chronological train/test split is empty.")

preprocessor = ColumnTransformer(
    transformers=[
        ("switch_port", OneHotEncoder(handle_unknown="ignore"),
         CATEGORICAL),
        ("metrics", "passthrough", NUMERIC),
    ]
)

model = Pipeline([
    ("preprocess", preprocessor),
    ("rf", RandomForestClassifier(
        n_estimators=200,
        max_depth=16,
        min_samples_leaf=2,
        class_weight="balanced_subsample",
        random_state=42,
        n_jobs=-1,
    )),
])

X_train = train[FEATURES]
y_train = train["target"]
X_test = test[FEATURES]
y_test = test["target"]

model.fit(X_train, y_train)
pred = model.predict(X_test)

print(f"\nTraining examples: {len(train)}")
print(f"Test examples: {len(test)}")
print(f"Test accuracy: {accuracy_score(y_test, pred):.4f}")
print("\nClassification report:")
print(classification_report(y_test, pred, zero_division=0))

joblib.dump({
    "model": model,
    "threshold_mbps": THRESHOLD_MBPS,
    "features": FEATURES,
    "numeric_features": NUMERIC,
    "categorical_features": CATEGORICAL,
    "target_description":
        "Whether max(rx_mbps, tx_mbps) exceeds threshold at next sample",
}, MODEL_FILE)

print(f"\nSaved model: {MODEL_FILE.resolve()}")
print("Training complete.")
