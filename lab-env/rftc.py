import collections
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
import joblib
from pathlib import Path

class RealTimeTrainingComponent:
    def __init__(self, logger, max_buffer_size=2000, retrain_interval=30):
        self.logger = logger
        self.training_buffer = collections.deque(maxlen=max_buffer_size)
        self.retrain_interval = retrain_interval  # e.g., retrain every N data cycles
        self.cycle_counter = 0

    def ingest_telemetry(self, stats_dict, features):
        """
        Captures live switch statistics and auto-labels them based on performance heuristics.
        - Label 0: Normal operation
        - Label 1: Congestion / High Drop / Heavy Load
        """
        rx_dropped = stats_dict.get("rx_dropped", 0)
        tx_dropped = stats_dict.get("tx_dropped", 0)
        
        # Heuristic auto-labeling condition (adjust thresholds based on your fabric load)
        is_congested = (rx_dropped > 2 or tx_dropped > 2) or (stats_dict.get("rx_bytes", 0) > 10000000)
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

    # def retrain_model(self, current_model, features, model_path="congestion_rf.joblib"):
    def retrain_model(self, current_model, features, model_path="built_model.joblib"):
    
        """Retrains the Random Forest model on accumulated live buffer data."""
        try:
            self.logger.info("RFTC: Starting background retraining on %d live samples...", len(self.training_buffer))
            
            data = [item[0] for item in self.training_buffer]
            labels = [item[1] for item in self.training_buffer]
            df = pd.DataFrame(data, columns=features)
            
            # Initialize a fresh or warm-started Random Forest estimator
            new_rf = RandomForestClassifier(n_estimators=50, random_state=42)
            new_rf.fit(df, labels)
            
            # Save updated model bundle back to disk
            bundle = {"model": new_rf, "features": features}
            joblib.dump(bundle, model_path)
            
            self.logger.info("RFTC: Model successfully retrained and saved to %s", model_path)
            return new_rf
            
        except Exception:
            self.logger.exception("RFTC_RETRAIN_ERROR")
            return current_model