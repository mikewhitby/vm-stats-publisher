#!/usr/bin/env python3
"""
VM Stats Fetch / Publish Tool
Fetches cable modem statistics from SuperHub 5 and publishes to Prometheus/VictoriaMetrics
"""

import argparse
import concurrent.futures
import json
import logging
import subprocess
import sys
import time
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple


# Modulation mapping: code -> numeric order
MODULATION_MAP = {
    "qpsk": 4,
    "qam_8": 8,
    "qam_16": 16,
    "qam_32": 32,
    "qam_64": 64,
    "qam_128": 128,
    "qam_256": 256,
    "qam_512": 512,
    "qam_1024": 1024,
    "qam_2048": 2048,
    "qam_4096": 4096,
}

# FFT type mapping: string -> numeric size
FFT_SIZE_MAP = {
    "2K": 2048,
    "4K": 4096,
    "8K": 8192,
}


class VMStatsPublisher:
    """Main class for fetching and publishing VM stats"""

    def __init__(
        self,
        superhub_url: str,
        prometheus_url: str,
        interval: int = 10,
        verbose: bool = False,
        dry_run: bool = False,
        ping_targets: Optional[List[str]] = None,
        ping_count: int = 3,
        ping_timeout: int = 2,
        superhub_timeout: int = 10,
    ):
        self.superhub_url = superhub_url.rstrip("/")
        self.prometheus_url = prometheus_url
        self.interval = interval
        self.dry_run = dry_run
        self.ping_targets = ping_targets or []
        self.ping_count = ping_count
        self.ping_timeout = ping_timeout
        self.superhub_timeout = superhub_timeout

        # Setup logging
        log_level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self.logger = logging.getLogger(__name__)

        # Log startup configuration
        self.logger.info(f"Starting VM Stats Publisher")
        self.logger.info(f"SuperHub URL: {self.superhub_url}")
        self.logger.info(f"Prometheus URL: {self.prometheus_url if self.prometheus_url else 'None (dry-run mode)'}")
        self.logger.info(f"Polling interval: {self.interval}s")
        self.logger.info(f"Mode: {'DRY-RUN' if self.dry_run else 'NORMAL'}")
        if self.ping_targets:
            self.logger.info(f"Ping targets: {', '.join(self.ping_targets)}")
            self.logger.info(f"Ping count: {self.ping_count}, timeout: {self.ping_timeout}s")

        # Create SSL context (always insecure for self-signed certificates)
        import ssl

        self.ssl_context = ssl._create_unverified_context()

    def fetch_json(self, endpoint: str) -> Tuple[Optional[Dict], Optional[int], bool]:
        """Fetch JSON data from SuperHub endpoint. Returns (data, status_code, success)"""
        url = f"{self.superhub_url}{endpoint}"
        self.logger.debug(f"Fetching from {url}")

        try:
            start_time = time.time()
            req = urllib.request.Request(url)
            response = urllib.request.urlopen(req, context=self.ssl_context, timeout=self.superhub_timeout)
            elapsed = time.time() - start_time

            data = json.loads(response.read().decode("utf-8"))
            self.logger.debug(f"Successfully fetched data from {endpoint} (HTTP {response.status}) in {elapsed:.2f}s")
            return data, response.status, True
        except urllib.error.HTTPError as e:
            status_code = e.code if hasattr(e, 'code') else None
            self.logger.warning(f"Failed to fetch from {url}: HTTP {status_code}")
            return None, status_code, False
        except urllib.error.URLError as e:
            self.logger.error(f"Failed to fetch from {url}: {e}")
            return None, None, False
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON from {url}: {e}")
            return None, None, False
        except Exception as e:
            self.logger.error(f"Unexpected error fetching from {url}: {e}")
            return None, None, False

    def map_modulation(self, modulation: str) -> Optional[int]:
        """Map modulation code to numeric order, return None for unknown/unsupported"""
        return MODULATION_MAP.get(modulation)

    def map_fft_size(self, fft_type: str) -> Optional[int]:
        """Map FFT type string to numeric size, return None for unknown/unsupported"""
        return FFT_SIZE_MAP.get(fft_type)

    def normalize_power(self, power: float, channel_type: str) -> float:
        """Normalize power value - DOCSIS 3.1 channels return 10x values"""
        if channel_type in ["ofdm", "ofdma"]:
            return power / 10.0
        return power

    def format_metric(self, name: str, value: float, labels: Dict[str, str]) -> str:
        """Format a metric in Prometheus exposition format"""
        if labels:
            label_str = ",".join([f'{k}="{v}"' for k, v in labels.items()])
            return f'{name}{{{label_str}}} {value}'
        return f"{name} {value}"

    def parse_downstream(self, data: Dict) -> List[str]:
        """Parse downstream channels and return metrics"""
        metrics = []
        channels = data.get("downstream", {}).get("channels", [])

        for channel in channels:
            channel_id = channel.get("channelId")
            channel_type = channel.get("channelType", "")
            modulation_order = self.map_modulation(channel.get("modulation", ""))

            # Determine scheme label
            if channel_type == "ofdm":
                scheme = "OFDM"
            elif channel_type == "sc_qam":
                scheme = "SC-QAM"
            else:
                scheme = channel_type.upper()

            labels = {
                "channel": f"{channel_id:02d}",
                "scheme": scheme,
            }

            # Modulation order metric (numeric, not a label)
            if modulation_order is not None:
                metrics.append(
                    self.format_metric(
                        "cablemodem_downstream_modulation_order",
                        modulation_order,
                        labels,
                    )
                )
            else:
                self.logger.warning(
                    f"Unknown modulation '{channel.get('modulation')}' for downstream channel {channel_id}, skipping modulation metric"
                )

            # Basic metrics
            frequency = channel.get("frequency", 0)
            if frequency != 0 or channel_type != "ofdm":
                metrics.append(
                    self.format_metric(
                        "cablemodem_downstream_frequency",
                        frequency,
                        labels,
                    )
                )

            # SNR (use rxMer for OFDM, snr for SC-QAM)
            snr_value = channel.get("rxMer") if channel_type == "ofdm" else channel.get("snr", 0)
            if snr_value != 0 or channel_type != "ofdm":
                metrics.append(self.format_metric("cablemodem_downstream_snr_db", snr_value, labels))

            # Power (normalize for DOCSIS 3.1)
            power = self.normalize_power(channel.get("power", 0), channel_type)
            metrics.append(
                self.format_metric("cablemodem_downstream_power_dbmv", power, labels)
            )

            # Error counts (counters with _total suffix)
            metrics.append(
                self.format_metric(
                    "cablemodem_downstream_corrected_errors_total",
                    channel.get("correctedErrors", 0),
                    labels,
                )
            )
            metrics.append(
                self.format_metric(
                    "cablemodem_downstream_uncorrected_errors_total",
                    channel.get("uncorrectedErrors", 0),
                    labels,
                )
            )

            # Lock status (1=locked, 0=unlocked)
            lock_status = 1 if channel.get("lockStatus", False) else 0
            metrics.append(
                self.format_metric("cablemodem_downstream_lock_status", lock_status, labels)
            )

            # OFDM-specific metrics
            if channel_type == "ofdm":
                channel_width = channel.get("channelWidth", 0)
                if channel_width != 0:
                    metrics.append(
                        self.format_metric(
                            "cablemodem_downstream_channel_width",
                            channel_width,
                            labels,
                        )
                    )
                fft_size = self.map_fft_size(channel.get("fftType", ""))
                if fft_size is not None:
                    metrics.append(
                        self.format_metric(
                            "cablemodem_downstream_fft_size",
                            fft_size,
                            labels,
                        )
                    )
                elif channel.get("fftType"):
                    self.logger.warning(
                        f"Unknown FFT type '{channel.get('fftType')}' for downstream channel {channel_id}, skipping FFT size metric"
                    )
                active_subcarriers = channel.get("numberOfActiveSubCarriers", 0)
                if active_subcarriers != 0:
                    metrics.append(
                        self.format_metric(
                            "cablemodem_downstream_active_subcarriers",
                            active_subcarriers,
                            labels,
                        )
                    )

        self.logger.debug(f"Parsed {len(metrics)} downstream metrics")
        return metrics

    def parse_upstream(self, data: Dict) -> List[str]:
        """Parse upstream channels and return metrics"""
        metrics = []
        channels = data.get("upstream", {}).get("channels", [])

        for channel in channels:
            channel_id = channel.get("channelId")
            channel_type = channel.get("channelType", "")
            modulation_order = self.map_modulation(channel.get("modulation", ""))

            # Determine scheme label
            if channel_type == "ofdma":
                scheme = "OFDMA"
            elif channel_type == "atdma":
                scheme = "ATDMA"
            else:
                scheme = channel_type.upper()

            labels = {
                "channel": f"{channel_id:02d}",
                "scheme": scheme,
            }

            # Modulation order metric (numeric, not a label)
            if modulation_order is not None:
                metrics.append(
                    self.format_metric(
                        "cablemodem_upstream_modulation_order",
                        modulation_order,
                        labels,
                    )
                )
            else:
                self.logger.warning(
                    f"Unknown modulation '{channel.get('modulation')}' for upstream channel {channel_id}, skipping modulation metric"
                )

            # Basic metrics
            frequency = channel.get("frequency", 0)
            if frequency != 0 or channel_type != "ofdma":
                metrics.append(
                    self.format_metric(
                        "cablemodem_upstream_frequency",
                        frequency,
                        labels,
                    )
                )

            # Power (normalize for DOCSIS 3.1)
            power = self.normalize_power(channel.get("power", 0), channel_type)
            metrics.append(self.format_metric("cablemodem_upstream_power_dbmv", power, labels))

            # Symbol rate (ATDMA only)
            if channel_type == "atdma":
                symbol_rate = channel.get("symbolRate", 0)
                if symbol_rate != 0:
                    metrics.append(
                        self.format_metric(
                            "cablemodem_upstream_symbol_rate",
                            symbol_rate,
                            labels,
                        )
                    )

            # Timeout counts (counters with _total suffix)
            metrics.append(
                self.format_metric(
                    "cablemodem_upstream_t1_timeouts_total",
                    channel.get("t1Timeout", 0),
                    labels,
                )
            )
            metrics.append(
                self.format_metric(
                    "cablemodem_upstream_t2_timeouts_total",
                    channel.get("t2Timeout", 0),
                    labels,
                )
            )
            metrics.append(
                self.format_metric(
                    "cablemodem_upstream_t3_timeouts_total",
                    channel.get("t3Timeout", 0),
                    labels,
                )
            )
            metrics.append(
                self.format_metric(
                    "cablemodem_upstream_t4_timeouts_total",
                    channel.get("t4Timeout", 0),
                    labels,
                )
            )

            # Lock status (1=locked, 0=unlocked)
            lock_status = 1 if channel.get("lockStatus", False) else 0
            metrics.append(self.format_metric("cablemodem_upstream_lock_status", lock_status, labels))

            # OFDMA-specific metrics
            if channel_type == "ofdma":
                channel_width = channel.get("channelWidth", 0)
                if channel_width != 0:
                    metrics.append(
                        self.format_metric(
                            "cablemodem_upstream_channel_width",
                            channel_width,
                            labels,
                        )
                    )
                fft_size = self.map_fft_size(channel.get("fftType", ""))
                if fft_size is not None:
                    metrics.append(
                        self.format_metric(
                            "cablemodem_upstream_fft_size",
                            fft_size,
                            labels,
                        )
                    )
                elif channel.get("fftType"):
                    self.logger.warning(
                        f"Unknown FFT type '{channel.get('fftType')}' for upstream channel {channel_id}, skipping FFT size metric"
                    )
                active_subcarriers = channel.get("numberOfActiveSubCarriers", 0)
                if active_subcarriers != 0:
                    metrics.append(
                        self.format_metric(
                            "cablemodem_upstream_active_subcarriers",
                            active_subcarriers,
                            labels,
                        )
                    )

        self.logger.debug(f"Parsed {len(metrics)} upstream metrics")
        return metrics

    def parse_serviceflows(self, data: Dict) -> List[str]:
        """Parse service flows and return metrics"""
        metrics = []
        service_flows = data.get("serviceFlows", [])

        for flow in service_flows:
            service_flow = flow.get("serviceFlow", {})
            direction = service_flow.get("direction", "")

            if direction not in ["downstream", "upstream"]:
                continue

            labels = {"direction": direction}

            metrics.append(
                self.format_metric(
                    "cablemodem_serviceflow_max_traffic_rate",
                    service_flow.get("maxTrafficRate", 0),
                    labels,
                )
            )
            metrics.append(
                self.format_metric(
                    "cablemodem_serviceflow_max_traffic_burst",
                    service_flow.get("maxTrafficBurst", 0),
                    labels,
                )
            )
            metrics.append(
                self.format_metric(
                    "cablemodem_serviceflow_min_reserved_rate",
                    service_flow.get("minReservedRate", 0),
                    labels,
                )
            )
            metrics.append(
                self.format_metric(
                    "cablemodem_serviceflow_max_concatenated_burst",
                    service_flow.get("maxConcatenatedBurst", 0),
                    labels,
                )
            )

        self.logger.debug(f"Parsed {len(metrics)} serviceflow metrics")
        return metrics

    def publish_metrics(self, metrics: List[str]) -> bool:
        """Publish metrics to Prometheus/VictoriaMetrics"""
        if self.dry_run:
            self.logger.info("DRY RUN - Metrics that would be published:")
            for metric in metrics:
                self.logger.info(f"  {metric}")
            return True

        payload = "\n".join(metrics) + "\n"
        self.logger.debug(f"Publishing {len(metrics)} metrics to {self.prometheus_url}")

        try:
            start_time = time.time()
            req = urllib.request.Request(
                self.prometheus_url,
                data=payload.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
            )
            response = urllib.request.urlopen(req, timeout=30)
            elapsed = time.time() - start_time

            if 200 <= response.status < 300:
                self.logger.debug(f"Successfully published metrics: HTTP {response.status} in {elapsed:.2f}s")
                return True
            else:
                self.logger.error(f"Failed to publish metrics: HTTP {response.status} in {elapsed:.2f}s")
                return False
        except urllib.error.URLError as e:
            self.logger.error(f"Failed to publish metrics: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error publishing metrics: {e}")
            return False

    def ping_target(self, target: str) -> Dict:
        """Ping a target and return metrics"""
        self.logger.debug(f"Pinging {target} with {self.ping_count} packets, timeout {self.ping_timeout}s")
        try:
            # Execute ping command
            cmd = ["ping", "-c", str(self.ping_count), "-W", str(self.ping_timeout), target]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.ping_timeout + 5
            )

            output = result.stdout
            stderr = result.stderr

            # Parse ping output
            # Linux ping output format varies, but we can extract key metrics
            lines = output.split("\n")

            packets_sent = self.ping_count
            packets_received = 0
            latency_avg_ms = 0.0
            latency_min_ms = 0.0
            latency_max_ms = 0.0
            jitter_ms = 0.0
            up = 0

            # Parse for packet statistics
            for line in lines:
                if "packets transmitted" in line or "packets transmitted," in line:
                    # Format: "4 packets transmitted, 4 received, 0% packet loss"
                    parts = line.split(",")
                    if len(parts) >= 2:
                        try:
                            # Extract received count
                            for part in parts:
                                if "received" in part:
                                    packets_received = int(part.strip().split()[0])
                        except (ValueError, IndexError):
                            pass

                # Parse for round-trip statistics
                if "rtt min/avg/max/mdev" in line or "rtt min/avg/max/stddev" in line:
                    # Format: "rtt min/avg/max/mdev = 0.123/0.456/0.789/0.123 ms"
                    try:
                        stats_part = line.split("=")[1].strip()
                        # Remove "ms" if present
                        stats_part = stats_part.replace(" ms", "").replace("ms", "")
                        values = stats_part.split("/")
                        if len(values) >= 4:
                            latency_min_ms = float(values[0])
                            latency_avg_ms = float(values[1])
                            latency_max_ms = float(values[2])
                            jitter_ms = float(values[3])
                    except (ValueError, IndexError):
                        pass

            # Calculate packet loss from actual sent/received counts
            if packets_sent > 0:
                packet_loss_percent = ((packets_sent - packets_received) / packets_sent) * 100
            else:
                packet_loss_percent = 100.0

            # Determine if target is up (at least one packet received)
            up = 1 if packets_received > 0 else 0

            # Log ping result
            if up:
                self.logger.debug(f"Ping {target}: up, avg={latency_avg_ms:.2f}ms, loss={packet_loss_percent:.1f}%")
            else:
                self.logger.info(f"Ping {target} failed: {packet_loss_percent:.1f}% packet loss")

            return {
                "up": up,
                "latency_avg_ms": latency_avg_ms,
                "latency_min_ms": latency_min_ms,
                "latency_max_ms": latency_max_ms,
                "jitter_ms": jitter_ms,
                "packet_loss_percent": packet_loss_percent,
                "packets_sent_total": packets_sent,
                "packets_received_total": packets_received,
            }

        except subprocess.TimeoutExpired:
            self.logger.warning(f"Ping to {target} timed out")
            return {
                "up": 0,
                "latency_avg_ms": 0,
                "latency_min_ms": 0,
                "latency_max_ms": 0,
                "jitter_ms": 0,
                "packet_loss_percent": 100,
                "packets_sent_total": self.ping_count,
                "packets_received_total": 0,
            }
        except Exception as e:
            self.logger.error(f"Error pinging {target}: {e}")
            return {
                "up": 0,
                "latency_avg_ms": 0,
                "latency_min_ms": 0,
                "latency_max_ms": 0,
                "jitter_ms": 0,
                "packet_loss_percent": 100,
                "packets_sent_total": self.ping_count,
                "packets_received_total": 0,
            }

    def run_once(self) -> bool:
        """Run a single polling cycle"""
        self.logger.info("Starting poll cycle")
        start_time = time.time()

        metrics = []

        # Run all operations concurrently
        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Submit HTTP requests for modem endpoints
            future_downstream = executor.submit(self.fetch_json, "/rest/v1/cablemodem/downstream")
            future_upstream = executor.submit(self.fetch_json, "/rest/v1/cablemodem/upstream")
            future_serviceflows = executor.submit(self.fetch_json, "/rest/v1/cablemodem/serviceflows")

            # Submit ping operations
            future_pings = {
                executor.submit(self.ping_target, target): target
                for target in self.ping_targets
            }

            # Wait for HTTP requests to complete
            downstream_data, downstream_status, downstream_ok = future_downstream.result()
            upstream_data, upstream_status, upstream_ok = future_upstream.result()
            serviceflows_data, serviceflows_status, serviceflows_ok = future_serviceflows.result()

            # Add endpoint status metrics
            endpoints = {
                "downstream": (downstream_ok, downstream_status),
                "upstream": (upstream_ok, upstream_status),
                "serviceflow": (serviceflows_ok, serviceflows_status),
            }

            for endpoint_name, (ok, status) in endpoints.items():
                up_value = 1 if ok else 0
                metrics.append(
                    self.format_metric("cablemodem_endpoint_up", up_value, {"endpoint": endpoint_name})
                )
                if status is not None:
                    metrics.append(
                        self.format_metric("cablemodem_endpoint_http_status", status, {"endpoint": endpoint_name})
                    )

            # Parse modem data into metrics (only if successful)
            if downstream_ok and downstream_data:
                metrics.extend(self.parse_downstream(downstream_data))
            if upstream_ok and upstream_data:
                metrics.extend(self.parse_upstream(upstream_data))
            if serviceflows_ok and serviceflows_data:
                metrics.extend(self.parse_serviceflows(serviceflows_data))

            # Wait for pings to complete and parse results
            for future in concurrent.futures.as_completed(future_pings):
                target = future_pings[future]
                try:
                    result = future.result()
                    labels = {"target": target}

                    metrics.append(
                        self.format_metric("ping_up", result["up"], labels)
                    )
                    metrics.append(
                        self.format_metric("ping_latency_avg_ms", result["latency_avg_ms"], labels)
                    )
                    metrics.append(
                        self.format_metric("ping_latency_min_ms", result["latency_min_ms"], labels)
                    )
                    metrics.append(
                        self.format_metric("ping_latency_max_ms", result["latency_max_ms"], labels)
                    )
                    metrics.append(
                        self.format_metric("ping_jitter_ms", result["jitter_ms"], labels)
                    )
                    metrics.append(
                        self.format_metric(
                            "ping_packet_loss_percent", result["packet_loss_percent"], labels
                        )
                    )
                    metrics.append(
                        self.format_metric(
                            "ping_packets_sent_total", result["packets_sent_total"], labels
                        )
                    )
                    metrics.append(
                        self.format_metric(
                            "ping_packets_received_total", result["packets_received_total"], labels
                        )
                    )
                except Exception as e:
                    self.logger.error(f"Error processing ping result for {target}: {e}")

        self.logger.info(f"Generated {len(metrics)} metrics")

        # Publish metrics
        success = self.publish_metrics(metrics)

        if success:
            elapsed = time.time() - start_time
            self.logger.debug(f"Poll cycle completed in {elapsed:.2f}s")
            self.logger.info("Poll cycle completed successfully")
        else:
            self.logger.error("Poll cycle failed")

        return success

    def run(self):
        """Run continuous polling loop"""
        while True:
            cycle_start = time.time()
            try:
                self.run_once()
            except KeyboardInterrupt:
                self.logger.info("Received interrupt, shutting down")
                break
            except Exception as e:
                self.logger.error(f"Unexpected error in poll cycle: {e}")

            # Calculate remaining time to next interval boundary
            elapsed = time.time() - cycle_start
            remaining = self.interval - elapsed
            if remaining > 0:
                self.logger.debug(f"Waiting {remaining:.2f}s before next poll")
                time.sleep(remaining)
            else:
                self.logger.warning(f"Poll cycle took {elapsed:.2f}s, exceeding interval of {self.interval}s")
                # Sleep a small amount to prevent tight loop if cycle consistently exceeds interval
                time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(
        description="VM Stats Fetch / Publish Tool - Fetches cable modem statistics from SuperHub 5 and publishes to Prometheus/VictoriaMetrics"
    )

    parser.add_argument(
        "--superhub-url",
        default="https://192.168.100.1",
        help="Full URL of the SuperHub 5 including HTTP scheme (default: https://192.168.100.1)",
    )
    parser.add_argument(
        "--prometheus-url",
        help="Full URL of the Prometheus/VictoriaMetrics endpoint (e.g., http://10.150.2.2:8428/api/v1/import/prometheus). Required unless --dry-run is set.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Polling interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--superhub-timeout",
        type=int,
        default=10,
        help="SuperHub HTTP request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--ping-target",
        action="append",
        help="Ping target (can be specified multiple times)",
    )
    parser.add_argument(
        "--ping-count",
        type=int,
        default=3,
        help="Number of ping packets per target (default: 3)",
    )
    parser.add_argument(
        "--ping-timeout",
        type=int,
        default=2,
        help="Ping timeout in seconds (default: 2)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose/debug output",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch data but don't publish to Prometheus (useful for testing)",
    )

    args = parser.parse_args()

    # Validate that prometheus-url is provided when not in dry-run mode
    if not args.dry_run and not args.prometheus_url:
        parser.error("--prometheus-url is required when not in --dry-run mode")

    publisher = VMStatsPublisher(
        superhub_url=args.superhub_url,
        prometheus_url=args.prometheus_url,
        interval=args.interval,
        verbose=args.verbose,
        dry_run=args.dry_run,
        ping_targets=args.ping_target,
        ping_count=args.ping_count,
        ping_timeout=args.ping_timeout,
        superhub_timeout=args.superhub_timeout,
    )

    publisher.run()


if __name__ == "__main__":
    main()
