from pathlib import Path
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import StandardScaler, OneHotEncoder

# Define your file paths using Pathlib as requested
CSV = Path("network_traffic_metrics_updated.csv")
MODEL_FILE = Path("model_built_updated.joblib")

def train_model_from_csv():
    print(f"Loading telemetry data from {CSV}...")
    
    if not CSV.exists():
        print(f"Error: {CSV} does not exist in the current directory. Please check the file path.")
        return
        
    df = pd.read_csv(CSV)
    
    # 1. Define the 14 official features specified in Section 4[cite: 2]
    categorical_features = ["switch", "port"]
    numeric_features = [
        "rx_packets", "tx_packets", 
        "rx_bytes", "tx_bytes", 
        "rx_mbps", "tx_mbps", 
        "rx_pps", "tx_pps",          # Packets per second included[cite: 2]
        "rx_drops", "tx_drops", 
        "rx_errors", "tx_errors"
    ]
    features = categorical_features + numeric_features

    # 2. Target Labeling Check
    # If your CSV already has a 'target' column, it uses it. 
    # Otherwise, it applies the program formulation threshold rules[cite: 2].
    if "target" not in df.columns:
        print("Target column not found in CSV. Applying Program Formulation threshold rules[cite: 2]...")
        
        link_capacity_mbps = 1000.0  # Adjust if your link capacity differs (e.g., 1000 Mbps = 1 Gbps)

        df["rx_link_utilisation"] = (df["rx_mbps"] / link_capacity_mbps) * 100.0
        df["tx_link_utilisation"] = (df["tx_mbps"] / link_capacity_mbps) * 100.0

        df["rx_drop_rate"] = (df["rx_drops"] / (df["rx_packets"] + 1)) * 100.0
        df["tx_drop_rate"] = (df["tx_drops"] / (df["tx_packets"] + 1)) * 100.0

        df["rx_error_rate"] = (df["rx_errors"] / (df["rx_packets"] + 1)) * 100.0
        df["tx_error_rate"] = (df["tx_errors"] / (df["tx_packets"] + 1)) * 100.0

        high_pps_threshold = 4000.0  # Packet-rate strain threshold

        # Congestion trigger: Utilisation >= 80%, Drops >= 0.1%, Errors >= 0.1%, or PPS >= 4000[cite: 2]
        df["target"] = (
            (df["rx_link_utilisation"] >= 80.0) | (df["tx_link_utilisation"] >= 80.0) |
            (df["rx_drop_rate"] >= 0.1) | (df["tx_drop_rate"] >= 0.1) |
            (df["rx_error_rate"] >= 0.1) | (df["tx_error_rate"] >= 0.1) |
            (df["rx_pps"] >= high_pps_threshold) | (df["tx_pps"] >= high_pps_threshold)
        ).astype(int)
    else:
        print("Using pre-existing 'target' column from CSV.")

    X = df[features]
    y = df["target"]

    print(f"Dataset loaded. Total samples: {len(df)}, Congested samples: {y.sum()} / Non-congested samples: {len(df) - y.sum()}")

    # 3. Preprocessing Pipeline for Categorical and Numeric Features
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical_features),
            ("num", StandardScaler(), numeric_features)
        ]
    )

    # 4. Random Forest Classifier (Single-threaded for Ryu eventlet safety)[cite: 2]
    classifier = RandomForestClassifier(
        n_estimators=100, 
        random_state=42, 
        n_jobs=1
    )

    model_pipeline = Pipeline(steps=[
        ("preprocessor", preprocessor),
        ("classifier", classifier)
    ])

    print("Training model on your telemetry data...")
    model_pipeline.fit(X, y)
    print("Model training completed successfully.")

    # 5. Package and Save Bundle matching your Ryu application expectations
    model_bundle = {
        "model": model_pipeline,
        "features": features,
        "numeric_features": numeric_features,
        "categorical_features": categorical_features
    }

    joblib.dump(model_bundle, MODEL_FILE)
    print(f"Model bundle successfully saved to {MODEL_FILE}")

if __name__ == "__main__":
    train_model_from_csv()