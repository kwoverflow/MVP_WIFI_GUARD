"""Read-only, bounded operator snapshots; never run a command or accept a path from a client."""
from __future__ import annotations

import json
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path


def number(value, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value if value >= minimum and (maximum is None or value <= maximum) else None


def snapshot(variable, max_age=10):
    filename = os.getenv(variable, "")
    if not filename:
        return None, "unconfigured", None
    try:
        with Path(filename).open('rb') as stream:
            raw = stream.read(262145)
        if len(raw) > 262144:
            raise ValueError
        data = json.loads(raw)
        stamp = number(data.get('epoch_seconds'))
        if stamp is None:
            raise ValueError
        age = time.time() - stamp
        if not 0 <= age <= max_age:
            return None, "stale", stamp
        return data, "current", stamp
    except (OSError, ValueError, TypeError, AttributeError):
        return None, "unavailable", None


def resources():
    host, host_state, stamp = snapshot('RESOURCE_SNAPSHOT_FILE')
    worker, worker_state, worker_stamp = snapshot('INFERENCE_STATUS_FILE')
    host = host or {}
    worker = worker or {}
    gpu_rows = host.get('gpus') if isinstance(host.get('gpus'), list) else []
    gpus = []
    for gpu in gpu_rows[:16]:
        if not isinstance(gpu, dict):
            continue
        gpus.append({
            'index': number(gpu.get('index')),
            'name': str(gpu.get('name', 'GPU'))[:120],
            'utilizationPercent': number(gpu.get('utilization_percent'), maximum=100),
            'memoryUsedMiB': number(gpu.get('memory_used_mib')),
            'memoryTotalMiB': number(gpu.get('memory_total_mib')),
        })
    timings = worker.get('processing_ms') if isinstance(worker.get('processing_ms'), dict) else {}
    timing_summary = {}
    for stage in ('features', 'inference', 'total'):
        values = timings.get(stage) if isinstance(timings.get(stage), dict) else {}
        timing_summary[stage] = {key: number(values.get(key)) for key in ('count', 'median', 'p95', 'max')}
    counts = worker.get('counts') if isinstance(worker.get('counts'), dict) else {}
    return {
        'checkedAt': datetime.now(UTC).isoformat(),
        'pipelineMode': 'mqtt-kafka-cloud', 'physicalE2EVerified': False,
        'host': {'state': host_state, 'sampledAtUnix': stamp,
            'scope': 'whole-host-including-other-apps; not isolated E2E service usage',
            'cpuPercent': number(host.get('cpu_percent'), maximum=100),
            'cpuCount': number(host.get('logical_cpu_count')),
            'memoryUsedBytes': number(host.get('memory_used_bytes')),
            'memoryTotalBytes': number(host.get('memory_total_bytes')),
            'diskUsedBytes': number(host.get('disk_used_bytes')),
            'diskTotalBytes': number(host.get('disk_total_bytes')), 'gpus': gpus},
        'inference': {'state': worker_state, 'sampledAtUnix': worker_stamp,
            'running': worker.get('running') is True if worker_state == 'current' else None,
            'featureBackend': worker.get('feature_backend') if worker.get('feature_backend') in ('reference', 'cpu-optimized') else None,
            'modelVersion': str(worker.get('model_version', ''))[:160] or None,
            'counts': {key: number(counts.get(key)) for key in ('received', 'published', 'duplicates', 'errors', 'batches')},
            'processingMs': timing_summary,
            'timingScope': 'recent per-window batch average; not capture-to-alert latency',
            'hasError': worker.get('has_error') is True},
        'notifications': {'emailConfigured': bool(os.getenv('SMTP_HOST')),
            'mobilePushEnabled': os.getenv('MOBILE_PUSH_ENABLED') == '1',
            'providerAcceptanceIsNotPhoneReceipt': True},
    }
