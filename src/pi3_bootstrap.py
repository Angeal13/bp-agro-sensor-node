# File: src/pi3_bootstrap.py
# Runs on Pi 3 startup - Auto-discovers Pi 4 gateway with caching and backoff
import socket
import requests
import time
import logging
import subprocess
import re
import json
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Pi3Bootstrap:
    def __init__(self):
        self.config_file = 'config/Config.py'
        self.cache_file = 'config/gateway_cache.json'
        self.fallback_ip = '192.168.1.100'
        self.last_successful_ip = None
        self.discovery_attempts = 0
        self.load_cache()
        
    def load_cache(self):
        """Load cached gateway information"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    cache = json.load(f)
                    self.last_successful_ip = cache.get('gateway_ip')
                    self.discovery_attempts = cache.get('discovery_attempts', 0)
                    cache_age = time.time() - cache.get('timestamp', 0)
                    
                    # Only use cache if less than 24 hours old
                    if cache_age < 86400:  # 24 hours in seconds
                        logging.info(f"Loaded cached gateway IP: {self.last_successful_ip}")
                    else:
                        logging.info("Cache expired, performing fresh discovery")
                        self.last_successful_ip = None
        except Exception as e:
            logging.warning(f"Could not load cache: {e}")
    
    def save_cache(self, gateway_ip):
        """Save successful gateway discovery to cache"""
        try:
            cache = {
                'gateway_ip': gateway_ip,
                'discovery_attempts': self.discovery_attempts,
                'timestamp': time.time(),
                'hostname': socket.gethostname()
            }
            with open(self.cache_file, 'w') as f:
                json.dump(cache, f)
            logging.debug(f"Saved gateway cache: {gateway_ip}")
        except Exception as e:
            logging.warning(f"Could not save cache: {e}")
    
    def enhanced_discover_gateway(self):
        """Enhanced discovery with caching and backoff"""
        # Try cached IP first (if available and recent)
        if self.last_successful_ip:
            logging.info(f"Testing cached gateway IP: {self.last_successful_ip}")
            if self.test_gateway(self.last_successful_ip):
                logging.info(f"✅ Cached gateway still responsive: {self.last_successful_ip}")
                self.discovery_attempts = 0
                return self.last_successful_ip
            else:
                logging.info("❌ Cached gateway not responding, performing full discovery")
        
        # Perform full discovery
        methods = [
            self.try_fallback_ip,
            self.try_mdns,
            self.scan_local_network,
            self.try_dhcp_hostname,
            self.ask_aws_if_online
        ]
        
        for method in methods:
            gateway_ip = method()
            if gateway_ip and self.test_gateway(gateway_ip):
                logging.info(f"✅ Found gateway at: {gateway_ip}")
                self.last_successful_ip = gateway_ip
                self.discovery_attempts = 0
                self.save_cache(gateway_ip)
                return gateway_ip
        
        # No gateway found
        self.discovery_attempts += 1
        logging.error("❌ No gateway found after all discovery methods")
        
        # Calculate backoff for next attempt
        backoff_hours = min(24, 1 * (2 ** min(self.discovery_attempts, 5)))  # Exponential max 24h
        logging.warning(f"Next discovery attempt in {backoff_hours} hours")
        
        return None
    
    def try_fallback_ip(self):
        """Try pre-configured fallback IP"""
        return self.fallback_ip if self.test_gateway(self.fallback_ip, fast=True) else None
    
    def try_mdns(self):
        """Try mDNS discovery (gateway.local)"""
        try:
            # Try to resolve gateway.local
            ip = socket.gethostbyname('gateway.local')
            if ip and ip != 'gateway.local':
                logging.info(f"mDNS resolved: gateway.local -> {ip}")
                return ip
        except:
            pass
        return None
    
    def scan_local_network(self):
        """Scan common Pi IP ranges"""
        base_ips = [
            '192.168.1.',  # Common home networks
            '192.168.0.',  # Common home networks  
            '10.0.0.',     # Business networks
            '172.20.10.'   # iPhone hotspots
        ]
        
        for base in base_ips:
            logging.info(f"Scanning network: {base}0/24")
            for i in range(100, 150):  # Scan .100 to .149
                ip = f"{base}{i}"
                if self.test_gateway(ip, fast=True):
                    logging.info(f"Found potential gateway at {ip}")
                    return ip
        return None
    
    def try_dhcp_hostname(self):
        """Try common Raspberry Pi hostnames"""
        hostnames = ['raspberrypi', 'raspberrypi.local', 'gateway', 'pi4', 'soil-gateway']
        
        for hostname in hostnames:
            try:
                ip = socket.gethostbyname(hostname)
                if ip and ip != hostname:
                    logging.info(f"Hostname resolved: {hostname} -> {ip}")
                    return ip
            except:
                continue
        return None
    
    def ask_aws_if_online(self):
        """If Pi 3 can reach the gateway, ask AWS which gateway IP to use.

        Fix (v2.1): The internet-reachability check previously pinged
        google.com, which fails on farm LANs that have no internet routing
        even when the gateway is up and healthy. We now test the gateway
        itself (fallback IP) before attempting the AWS lookup. This means:

          - LAN up, no internet  → fallback_ip ping succeeds, gateway found
            via earlier discovery methods. ask_aws_if_online is skipped.
          - LAN up, internet up  → gateway already found above; if somehow
            we reach here, we ping the fallback IP as the reachability probe
            instead of google.com.
          - LAN down, internet up → no gateway anywhere, offline mode.

        The google.com dependency is removed entirely.
        """
        try:
            # Probe the farm LAN gateway IP directly instead of google.com.
            # If we can reach it, the LAN is up and we can try the AWS lookup.
            probe_ip = self.last_successful_ip or self.fallback_ip
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '2', probe_ip],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                logging.debug("ask_aws_if_online: gateway not reachable, skipping AWS lookup")
                return None

            # LAN is up — try the cloud API for the authoritative gateway IP.
            # This handles the edge case where the gateway IP has changed
            # (e.g. DHCP reassignment) and the cache is stale.
            machine_id = str(self.get_machine_id())
            response = requests.get(
                'https://your-domain.com/api/global/gateway/for_sensor',
                params={'machine_id': machine_id},
                timeout=5
            )
            if response.ok:
                data = response.json()
                gateway_ip = data.get('gateway_ip')
                if gateway_ip:
                    logging.info(f"AWS provided gateway IP: {gateway_ip}")
                    return gateway_ip
        except Exception as e:
            logging.debug(f"ask_aws_if_online: {e}")
        return None
    
    def get_machine_id(self):
        """Get unique machine ID for this Pi 3"""
        try:
            # Get CPU serial
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Serial'):
                        return line.split(':')[1].strip()
        except:
            pass
        # Fallback to MAC address
        try:
            import uuid
            return str(uuid.getnode())
        except:
            return "unknown"
    
    def test_gateway(self, ip, fast=False):
        """Test if gateway is responsive"""
        try:
            if fast:
                # Fast ping test
                result = subprocess.run(
                    ['ping', '-c', '1', '-W', '1', ip],
                    capture_output=True,
                    text=True
                )
                return result.returncode == 0
            else:
                # HTTP test (more reliable) - test mobile API endpoint
                response = requests.get(f'http://{ip}:5001/api/local/farm/info', timeout=2)
                if response.ok:
                    data = response.json()
                    # Check if it's actually a soil gateway
                    return data.get('status') in ['configured', 'unconfigured']
                return False
        except:
            return False
    
    def update_config(self, gateway_ip):
        """Update Config.py with discovered gateway IP"""
        try:
            with open(self.config_file, 'r') as f:
                content = f.read()
            
            # Update host in DB_CONFIG
            new_content = re.sub(
                r"'host':\s*[\"'][^\"']*[\"']",
                f"'host': \"{gateway_ip}\"",
                content
            )
            
            # Also update GATEWAY_IP if present
            new_content = re.sub(
                r"GATEWAY_IP\s*=\s*[\"'][^\"']*[\"']",
                f"GATEWAY_IP = \"{gateway_ip}\"",
                new_content
            )
            
            # Update GATEWAY_API
            new_content = re.sub(
                r"GATEWAY_API\s*=\s*[\"'][^\"']*[\"']",
                f"GATEWAY_API = \"http://{gateway_ip}:5001\"",
                new_content
            )
            
            with open(self.config_file, 'w') as f:
                f.write(new_content)
            
            logging.info(f"📝 Updated Config.py with gateway IP: {gateway_ip}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to update config: {e}")
            return False
    
    def register_with_gateway(self, gateway_ip):
        """Register Pi 3 sensor with gateway"""
        try:
            machine_id = self.get_machine_id()
            
            response = requests.post(
                f'http://{gateway_ip}:5001/api/local/sensor/register',
                json={
                    'machine_id': machine_id,
                    'sensor_type': 'soil_sensor_v1',
                    'firmware_version': '2.0',
                    'hostname': socket.gethostname()
                },
                timeout=5
            )
            
            if response.ok:
                logging.info(f"✅ Registered with gateway: {machine_id}")
                return True
            else:
                logging.warning(f"Gateway registration failed: {response.text}")
                return False
                
        except Exception as e:
            logging.error(f"Registration error: {e}")
            return False
    
    def run(self):
        """Main bootstrap sequence - designed to run on every boot"""
        logging.info("=" * 50)
        logging.info("🚀 Pi 3 Bootstrap Starting...")
        logging.info(f"🏠 Hostname: {socket.gethostname()}")
        logging.info(f"📱 Machine ID: {self.get_machine_id()}")
        logging.info("=" * 50)
        
        # Discover gateway
        gateway_ip = self.enhanced_discover_gateway()
        
        if gateway_ip:
            # Update config
            if self.update_config(gateway_ip):
                # Register with gateway
                self.register_with_gateway(gateway_ip)
                
                logging.info("✅ Pi 3 Bootstrap Complete!")
                logging.info(f"📡 Gateway: {gateway_ip}")
                logging.info(f"📊 Config updated: {self.config_file}")
                
                # Start main sensor controller
                self.start_sensor_controller()
            else:
                logging.error("❌ Failed to update config - running with existing configuration")
                self.start_sensor_controller()
        else:
            logging.error("❌ No gateway found - running in offline mode")
            # Still start controller with existing config
            self.start_sensor_controller()
    
    def start_sensor_controller(self):
        """Start the main sensor controller"""
        try:
            # Check if controller is already running
            result = subprocess.run(['pgrep', '-f', 'MainController.py'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                logging.info("🌱 Sensor Controller already running")
                return
            
            # Start controller in background
            controller_path = os.path.join(os.path.dirname(__file__), 'MainController.py')
            subprocess.Popen(['python3', controller_path])
            logging.info("🌱 Starting Sensor Controller...")
            
        except Exception as e:
            logging.error(f"Failed to start controller: {e}")

if __name__ == '__main__':
    bootstrap = Pi3Bootstrap()
    bootstrap.run()
    
    # Keep bootstrap running to monitor network changes
    try:
        check_interval = 300  # Check every 5 minutes
        while True:
            time.sleep(check_interval)
            
            # Periodically test gateway connection
            if bootstrap.last_successful_ip:
                if not bootstrap.test_gateway(bootstrap.last_successful_ip):
                    logging.warning("⚠️  Gateway connection lost, re-discovering...")
                    bootstrap.run()  # Re-run bootstrap
    except KeyboardInterrupt:
        logging.info("👋 Bootstrap monitor shutting down...")