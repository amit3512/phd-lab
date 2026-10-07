import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import deque

# Keep track of the last 180 pressure readings for trend analysis
HISTORY_WINDOW_SIZE = 180
pressure_history = deque(maxlen=HISTORY_WINDOW_SIZE)

def evaluate_alerts(temperature, pressure_diff):
    """Generates user-friendly alerts based on environmental thresholds and trends."""
    alerts = {
        "alert_level": "NORMAL",
        "alert_message": "All environmental readings are normal."
    }
    
    # Check for rapid barometric drops (Storm warning)
    if pressure_diff < -1.5:
        alerts["alert_level"] = "WARNING"
        alerts["alert_message"] = "Severe pressure drop detected! Expect heavy rain, storms, or a sharp weather change."
    elif pressure_diff > 1.5:
        alerts["alert_level"] = "INFO"
        alerts["alert_message"] = "Barometric pressure rising rapidly. Weather is clearing up."
        
    # Check temperature thresholds (optional smart home safeguards)
    if temperature is not None:
        if temperature > 35.0:
            alerts["alert_level"] = "CRITICAL"
            alerts["alert_message"] = "HIGH TEMPERATURE ALERT! Indoor environment is dangerously hot."
        elif temperature < 10.0:
            alerts["alert_level"] = "WARNING"
            alerts["alert_message"] = "Low temperature alert! Risk of cold drafts or freezing."
            
    return alerts

def analyze_trend(history):
    if len(history) < 5:
        return "Collecting data...", 0.0
    
    current_pressure = history[-1]
    past_pressure = history[0]
    diff = current_pressure - past_pressure
    
    if diff > 1.5:
        forecast = "Improving rapidly / Clearing skies"
    elif diff < -1.5:
        forecast = "Storm or heavy precipitation approaching rapidly!"
    elif diff > 0.5:
        forecast = "Pressure rising, weather improving"
    elif diff < -0.5:
        forecast = "Pressure dropping, weather worsening"
    else:
        forecast = "Stable conditions"
        
    return forecast, diff

class AnalyticsHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            # Parse incoming JSON data from sensor client
            data = json.loads(post_data.decode('utf-8'))
            pressure = data.get("pressure")
            temperature = data.get("temperature")
            
            if pressure is not None:
                pressure_history.append(float(pressure))
                
            # Perform analytics & evaluate alerts
            forecast, pressure_diff = analyze_trend(pressure_history)
            alert_data = evaluate_alerts(temperature, pressure_diff)
            
            # Construct comprehensive response payload
            response_payload = {
                "status": "success",
                "temperature_c": temperature,
                "pressure_hpa": pressure,
                "weather_forecast": forecast,
                "alert_level": alert_data["alert_level"],      # NORMAL, INFO, WARNING, CRITICAL
                "alert_message": alert_data["alert_message"],  # Clear message for the user
                "history_samples_count": len(pressure_history)
            }
            
            response_body = json.dumps(response_payload).encode('utf-8')
            
            # Send HTTP response back to the user/client
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(response_body))
            self.end_headers()
            self.wfile.write(response_body)
            
            # Also log it inside the Kubernetes pod logs
            print(json.dumps(response_payload))
            
        except Exception as e:
            error_response = json.dumps({"status": "error", "message": str(e)}).encode('utf-8')
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(error_response))
            self.end_headers()
            self.wfile.write(error_response)

def run(server_class=HTTPServer, handler_class=AnalyticsHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"Analytics microservice with alerting running on port {port}...")
    httpd.serve_forever()

if __name__ == '__main__':
    run()