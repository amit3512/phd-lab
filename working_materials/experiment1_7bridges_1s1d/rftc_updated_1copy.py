import collections
import pandas as pd
import joblib
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline

class RealTimeTrainingComponent:
    def __init__(self, logger, max_buffer_size=2000, retrain_interval=30, model_path="model_built_updated.joblib"):
        self.logger = logger
        self.training_buffer = collections.deque(maxlen=max_buffer_size)
        self.retrain_interval = retrain_interval  # retrain every N data cycles
        self.cycle_counter = 0
        self.model_path = Path(model_path)

    def ingest_telemetry(self, stats_dict, features):
        """
        Captures live switch statistics and auto-labels them based on the 
        Program Formulation multi-metric thresholds (utilization, drop rates, error rates, PPS).
        - Label 0: Normal operation
        - Label 1: Congestion / Threshold Violation
        """
        rx_packets = stats_dict.get("rx_packets", 0)
        tx_packets = stats_dict.get("tx_packets", 0)
        rx_drops = stats_dict.get("rx_drops", stats_dict.get("rx_dropped", 0))
        tx_drops = stats_dict.get("tx_drops", stats_dict.get("tx_dropped", 0))
        rx_errors = stats_dict.get("rx_errors", 0)
        tx_errors = stats_dict.get("tx_errors", 0)
        rx_mbps = stats_dict.get("rx_mbps", 0.0)
        tx_mbps = stats_dict.get("tx_mbps", 0.0)
        rx_pps = stats_dict.get("rx_pps", 0.0)
        tx_pps = stats_dict.get("tx_pps", 0.0)

        # Derived operational metrics matching Program Formulation thresholds
        link_capacity_mbps = 1000.0  # Default link capacity assumption (1 Gbps)
        rx_util = (rx_mbps / link_capacity_mbps) * 100.0
        tx_util = (tx_mbps / link_capacity_mbps) * 100.0

        rx_drop_rate = (rx_drops / (rx_packets + 1)) * 100.0
        tx_drop_rate = (tx_drops / (tx_packets + 1)) * 100.0

        rx_error_rate = (rx_errors / (rx_packets + 1)) * 100.0
        tx_error_rate = (tx_errors / (tx_packets + 1)) * 100.0

        high_pps_threshold = 4000.0
        self.logger.info('rx_util: %d, tx_util: %d, rx_drop_rate: %d, tx_drop_rate: %d', rx_util, tx_util, rx_drop_rate, tx_drop_rate)

        # Multi-metric heuristic auto-labeling rule[cite: 2]
        is_congested = (
            rx_util >= 80.0 or tx_util >= 80.0 or
            rx_drop_rate >= 0.1 or tx_drop_rate >= 0.1 or
            rx_error_rate >= 0.1 or tx_error_rate >= 0.1 or
            rx_pps >= high_pps_threshold or tx_pps >= high_pps_threshold
        )
        label = 1 if is_congested else 0

        # Build feature row matching the model's expected input columns
        row = {f: stats_dict.get(f, 0.0) for f in features}
        self.training_buffer.append((row, label))

    def should_retrain(self):
        self.cycle_counter += 1
        if self.cycle_counter >= self.retrain_interval and len(self.training_buffer) >= 100:
            self.cycle_counter = 0
            return True
        return False

    def retrain_model(self, current_pipeline, features, categorical_features, numeric_features):
        """
        Retrains the full scikit-learn Pipeline on accumulated live buffer data.
        """
        try:
            self.logger.info("RFTC: Starting background retraining on %d live samples...", len(self.training_buffer))
            
            data = [item[0] for item in self.training_buffer]
            labels = [item[1] for item in self.training_buffer]
            df = pd.DataFrame(data, columns=features)
            
            # Ensure numeric columns are properly typed
            for col in numeric_features:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

            X = df[features]
            y = labels

            # Extract the preprocessor from the existing pipeline safely
            preprocessor_step_name = "preprocessor" if "preprocessor" in current_pipeline.named_steps else "preprocess"
            preprocessor = current_pipeline.named_steps[preprocessor_step_name]

            # Initialize a fresh model pipeline mirroring your offline training script structure
            new_pipeline = Pipeline([
                ("preprocessor", preprocessor),
                ("classifier", RandomForestClassifier(
                    n_estimators=100,
                    random_state=42,
                    n_jobs=1,  # Single-threaded for Ryu eventlet safety[cite: 2]
                )),
            ])

            new_pipeline.fit(X, y)
            
            # Save updated model bundle back to disk matching your artifact structure
            bundle = {
                "model": new_pipeline,
                "features": features,
                "numeric_features": numeric_features,
                "categorical_features": categorical_features,
            }
            joblib.dump(bundle, self.model_path)
            
            self.logger.info("RFTC: Model successfully retrained and saved to %s", self.model_path)
            return new_pipeline
            
        except Exception:
            self.logger.exception("RFTC_RETRAIN_ERROR")
            return current_pipeline