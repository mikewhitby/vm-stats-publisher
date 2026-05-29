#!/bin/sh
set -e

# Build command-line arguments from environment variables
CMD="python vm_stats_publisher.py"

# SuperHub URL
if [ -n "$SUPERHUB_URL" ]; then
    CMD="$CMD --superhub-url $SUPERHUB_URL"
fi

# Prometheus URL
if [ -n "$PROMETHEUS_URL" ]; then
    CMD="$CMD --prometheus-url $PROMETHEUS_URL"
fi

# Polling interval
if [ -n "$INTERVAL" ]; then
    CMD="$CMD --interval $INTERVAL"
fi

# SuperHub timeout
if [ -n "$SUPERHUB_TIMEOUT" ]; then
    CMD="$CMD --superhub-timeout $SUPERHUB_TIMEOUT"
fi

# Ping targets (space-separated)
if [ -n "$PING_TARGETS" ]; then
    for target in $PING_TARGETS; do
        CMD="$CMD --ping-target $target"
    done
fi

# Ping count
if [ -n "$PING_COUNT" ]; then
    CMD="$CMD --ping-count $PING_COUNT"
fi

# Ping timeout
if [ -n "$PING_TIMEOUT" ]; then
    CMD="$CMD --ping-timeout $PING_TIMEOUT"
fi

# Verbose
if [ "$VERBOSE" = "true" ] || [ "$VERBOSE" = "1" ]; then
    CMD="$CMD --verbose"
fi

# Dry run
if [ "$DRY_RUN" = "true" ] || [ "$DRY_RUN" = "1" ]; then
    CMD="$CMD --dry-run"
fi

# Execute the command
echo "Executing: $CMD"
exec $CMD
