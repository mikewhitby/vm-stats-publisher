# VM Stats Fetch / Publish Tool

Fetches cable modem statistics from SuperHub 5 and publishes to Prometheus/VictoriaMetrics.

## Overview

This tool runs in a Docker container and continuously polls the SuperHub 5 REST API to fetch cable modem statistics (downstream, upstream, and service flow data). It normalizes the data and publishes it in Prometheus exposition format to a VictoriaMetrics or Prometheus endpoint.

## Features

- Fetches data from three SuperHub 5 endpoints:
  - `/rest/v1/cablemodem/downstream`
  - `/rest/v1/cablemodem/upstream`
  - `/rest/v1/cablemodem/serviceflows`
- Normalizes power values for DOCSIS 3.1 channels (OFDM/OFDMA)
- Maps modulation codes to human-readable names
- Publishes metrics in Prometheus format
- Configurable polling interval
- SSL certificate validation control (for self-signed certificates)
- Dry-run mode for testing
- Verbose logging support

## Building the Docker Image

### Using Pre-built Image (Recommended)

A pre-built Docker image is available from GitHub Container Registry:

```bash
docker pull ghcr.io/mikewhitby/vm-stats-publisher:latest
```

### Building Locally

```bash
docker build -t vm-stats-publisher .
```

## Running the Container

### Required Parameters

- `--prometheus-url` - Full URL of the Prometheus/VictoriaMetrics endpoint (e.g., `http://10.150.2.2:8428/api/v1/import/prometheus`). Required unless `--dry-run` is set.

### Optional Parameters

- `--superhub-url` - Full URL of the SuperHub 5 including HTTP scheme (default: `https://192.168.100.1`)
- `--interval` - Polling interval in seconds (default: 30)
- `--ping-target` - Ping target (can be specified multiple times)
- `--ping-count` - Number of ping packets per target (default: 3)
- `--ping-timeout` - Ping timeout in seconds (default: 2)
- `--verbose` - Enable verbose/debug output
- `--dry-run` - Fetch data but don't publish to Prometheus (useful for testing)

### Using Docker Compose

The container supports environment variables for configuration. This is the recommended method for production deployments.

#### Environment Variables

- `PROMETHEUS_URL` - Full URL of the Prometheus/VictoriaMetrics endpoint
- `SUPERHUB_URL` - Full URL of the SuperHub 5 (default: `https://192.168.100.1`)
- `INTERVAL` - Polling interval in seconds (default: 30)
- `PING_TARGETS` - Space-separated list of ping targets
- `PING_COUNT` - Number of ping packets per target (default: 3)
- `PING_TIMEOUT` - Ping timeout in seconds (default: 2)
- `VERBOSE` - Enable verbose/debug output (set to `true` or `1`)
- `DRY_RUN` - Fetch data but don't publish to Prometheus (set to `true` or `1`)

#### Docker Compose Example

```bash
docker-compose up -d
```

The included `docker-compose.yml` file provides an example configuration. Modify the environment variables as needed for your environment.

### Example Invocation

```bash
docker run -d \
  --name vm-stats-publisher \
  --restart unless-stopped \
  vm-stats-publisher \
  --prometheus-url http://10.150.2.2:8428/api/v1/import/prometheus
```

Note: `--superhub-url` can be omitted if your SuperHub is at the default address `https://192.168.100.1`.

### With Custom Interval

```bash
docker run -d \
  --name vm-stats-publisher \
  --restart unless-stopped \
  vm-stats-publisher \
  --prometheus-url http://10.150.2.2:8428/api/v1/import/prometheus \
  --interval 30
```

### With Ping Targets

```bash
docker run -d \
  --name vm-stats-publisher \
  --restart unless-stopped \
  vm-stats-publisher \
  --prometheus-url http://10.150.2.2:8428/api/v1/import/prometheus \
  --ping-target 192.168.1.1 \
  --ping-target 8.8.8.8 \
  --ping-target 1.1.1.1
```

### Dry Run (Testing)

```bash
docker run --rm \
  vm-stats-publisher \
  --dry-run \
  --verbose
```

Note: `--prometheus-url` is not required in dry-run mode, and `--superhub-url` can be omitted if using the default address.

## Notes

- The SuperHub uses a self-signed HTTPS certificate, which is automatically handled
- The tool runs continuously, polling at the specified interval
- Each poll fetches all three endpoints and publishes the combined metrics to Prometheus
- Use `--restart unless-stopped` to ensure the container restarts automatically on system reboot

## Metrics Published

### Downstream Metrics (SC-QAM)

- `cablemodem_downstream_frequency{channel, modulation, scheme}`
- `cablemodem_downstream_snr{channel, modulation, scheme}`
- `cablemodem_downstream_power_dbmv{channel, modulation, scheme}`
- `cablemodem_downstream_corrected_errors{channel, modulation, scheme}`
- `cablemodem_downstream_uncorrected_errors{channel, modulation, scheme}`
- `cablemodem_downstream_lock_status{channel, modulation, scheme}` (0=unlocked, 1=locked)

### Downstream Metrics (OFDM/DOCSIS 3.1)

- All SC-QAM metrics plus:
- `cablemodem_downstream_channel_width{channel, modulation, scheme}`
- `cablemodem_downstream_fft_type{channel, modulation, scheme}`
- `cablemodem_downstream_active_subcarriers{channel, modulation, scheme}`

### Upstream Metrics (ATDMA)

- `cablemodem_upstream_frequency{channel, modulation, scheme}`
- `cablemodem_upstream_power_dbmv{channel, modulation, scheme}`
- `cablemodem_upstream_symbol_rate{channel, modulation, scheme}`
- `cablemodem_upstream_t1_timeout{channel, modulation, scheme}`
- `cablemodem_upstream_t2_timeout{channel, modulation, scheme}`
- `cablemodem_upstream_t3_timeout{channel, modulation, scheme}`
- `cablemodem_upstream_t4_timeout{channel, modulation, scheme}`
- `cablemodem_upstream_lock_status{channel, modulation, scheme}` (0=unlocked, 1=locked)

### Upstream Metrics (OFDMA/DOCSIS 3.1)

- All ATDMA metrics (except symbol_rate) plus:
- `cablemodem_upstream_channel_width{channel, modulation, scheme}`
- `cablemodem_upstream_fft_type{channel, modulation, scheme}`
- `cablemodem_upstream_active_subcarriers{channel, modulation, scheme}`

### Service Flow Metrics

- `cablemodem_serviceflow_max_traffic_rate{direction}`
- `cablemodem_serviceflow_max_traffic_burst{direction}`
- `cablemodem_serviceflow_min_reserved_rate{direction}`
- `cablemodem_serviceflow_max_concatenated_burst{direction}`

### Ping Metrics

- `ping_up{target}` - 1 if target is reachable, 0 if not
- `ping_latency_avg_ms{target}` - Average round-trip time in milliseconds
- `ping_latency_min_ms{target}` - Minimum round-trip time in milliseconds
- `ping_latency_max_ms{target}` - Maximum round-trip time in milliseconds
- `ping_jitter_ms{target}` - Jitter (deviation) in milliseconds
- `ping_packet_loss_percent{target}` - Packet loss percentage (0-100)
- `ping_packets_sent_total{target}` - Total packets sent
- `ping_packets_received_total{target}` - Total packets received

## Labels

- `channel` - Channel ID
- `modulation` - Human-readable modulation name (e.g., QAM256, QPSK)
- `scheme` - Channel type (SC-QAM/OFDM for downstream, ATDMA/OFDMA for upstream)
- `direction` - Service flow direction (downstream/upstream)
- `target` - Ping target hostname or IP address

## Data Normalization

- **Power values**: DOCSIS 3.1 channels (OFDM downstream, OFDMA upstream) return power values 10x greater than DOCSIS 3.0 channels. These are automatically divided by 10 for normalization.
- **SNR values**: For OFDM channels, rxMer is used instead of snr.

## Running Locally (Without Docker)

You can also run the script directly with Python 3.12+:

```bash
python3 vm_stats_publisher.py \
  --prometheus-url http://10.150.2.2:8428/api/v1/import/prometheus
```

For dry-run testing without a Prometheus endpoint:

```bash
python3 vm_stats_publisher.py \
  --dry-run \
  --verbose
```

## License

See requirement.md for detailed specifications.
