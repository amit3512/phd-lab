# Save as traffic_generator.py and run: sudo ip netns exec sdnA python3 traffic_generator.py
import time
import random
from scapy.all import send, IP, ICMP

target_ip = "192.168.100.2"
print(f"Starting variable frequency packet generator targeting {target_ip}...")

try:
    while True:
        # Generate a random burst size (e.g., between 10 and 100 packets)
        burst_count = random.randint(10, 100)
        
        # Generate a random packet size (64 to 1400 bytes)
        payload_size = random.randint(64, 12288)
        payload = "X" * payload_size
        
        # Send burst
        pkt = IP(dst=target_ip)/ICMP()/payload
        send(pkt, count=burst_count, verbose=False)
        
        # Random sleep interval between bursts (0.1 to 1.5 seconds)
        sleep_interval = random.uniform(0.1, 1.5)
        print(f"Sent burst of {burst_count} pkts ({payload_size}B). Waiting {sleep_interval:.2f}s...")
        time.sleep(sleep_interval)
except KeyboardInterrupt:
    print("Traffic generation stopped.")