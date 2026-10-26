import collections
import pandas as pd
import joblib
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline

class RealTimeTrainingComponent:
    # def __init__(self, logger, max_buffer_size=2000, retrain_interval=30, model_path="model_built_oct19.joblib"):
    def __init__(self, logger, max_buffer_size=2000, retrain_interval=30, model_path="model_built_updated.joblib"):
        self.logger = logger
        self.training_buffer = collections.deque(maxlen=max_buffer_size)
        self.retrain_interval = retrain_interval  # retrain every N data cycles
        self.cycle_counter = 0
        self.model_path = Path(model_path)

    def ingest_telemetry(self, stats_dict, features):
        """
        Captures live switch statistics and auto-labels them based on performance heuristics.
        - Label 0: Normal operation
        - Label 1: Congestion / High Drop / Heavy Load
        """
        rx_dropped = stats_dict.get("rx_drops", stats_dict.get("rx_dropped", 0))
        tx_dropped = stats_dict.get("tx_drops", stats_dict.get("tx_dropped", 0))
        
        # Heuristic auto-labeling condition matching your system profile
        is_congested = (rx_dropped > 2 or tx_dropped > 2) or (stats_dict.get("rx_bytes", 0) > 10000000)
        label = 1 if is_congested else 0

        # Build feature row matching the model's expected input columns (categorical + numeric)
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
        Retrains the full scikit-learn Pipeline (preprocessor + RandomForest) on accumulated live buffer data.
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

            # Extract the preprocessor from the existing pipeline to maintain consistent encoding
            preprocessor = current_pipeline.named_steps["preprocess"]

            # Initialize a fresh model pipeline mirroring your training script structure
            new_pipeline = Pipeline([
                ("preprocess", preprocessor),
                ("rf", RandomForestClassifier(
                    n_estimators=200,
                    max_depth=16,
                    min_samples_leaf=2,
                    class_weight="balanced_subsample",
                    random_state=42,
                    # n_jobs=-1,
                    n_jobs=1,
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