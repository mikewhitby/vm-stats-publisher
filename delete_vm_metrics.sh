#!/bin/bash
# Delete all cable modem metrics from VictoriaMetrics

# Check for VictoriaMetrics URL argument
if [ -z "$1" ]; then
    echo "Usage: $0 <victoriametrics_url>"
    exit 1
fi

VM_URL="$1"

# Delete all cablemodem metrics
echo "Deleting all cablemodem metrics from ${VM_URL}..."
curl -X POST \
  --data-urlencode 'match[]={__name__=~"cablemodem_.*"}' \
  "${VM_URL}/api/v1/admin/tsdb/delete_series"

# Delete all ping metrics
echo "Deleting all ping metrics from ${VM_URL}..."
curl -X POST \
  --data-urlencode 'match[]={__name__=~"ping_.*"}' \
  "${VM_URL}/api/v1/admin/tsdb/delete_series"

echo ""
echo "Delete requests sent."
