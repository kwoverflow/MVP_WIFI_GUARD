import json
import time

import pytest
from helpers import signup_home, signup_root, signup_member, invite_code


def test_resource_status_requires_auth_and_unmeasured_is_not_zero(client, monkeypatch):
    monkeypatch.delenv('RESOURCE_SNAPSHOT_FILE', raising=False)
    monkeypatch.delenv('INFERENCE_STATUS_FILE', raising=False)
    assert client.get('/api/v1/training/resources').status_code == 401
    actor = signup_home(client, 'resources@test.io')
    result = client.get('/api/v1/training/resources', headers=actor.h).json()
    assert result['host']['state'] == 'unconfigured'
    assert result['host']['cpuPercent'] is None and result['host']['gpus'] == []
    assert result['inference']['processingMs']['features']['p95'] is None
    assert result['physicalE2EVerified'] is False


def test_live_resources_are_whitelisted_and_not_e2e_exclusive(client, monkeypatch, tmp_path):
    path = tmp_path/'host.json'
    path.write_text(json.dumps({'epoch_seconds': time.time(), 'cpu_percent': 31.5,
        'logical_cpu_count': 8, 'memory_used_bytes': 1024, 'memory_total_bytes': 2048,
        'private_key': 'must-never-appear', 'gpus': [{'index': 0, 'name': 'GPU',
        'utilization_percent': 23, 'memory_used_mib': 123}]}))
    monkeypatch.setenv('RESOURCE_SNAPSHOT_FILE', str(path))
    actor = signup_home(client, 'live-resources@test.io')
    response = client.get('/api/v1/training/resources', headers=actor.h)
    assert response.json()['host']['state'] == 'current'
    assert response.json()['host']['cpuPercent'] == 31.5
    assert response.json()['host']['gpus'][0]['utilizationPercent'] == 23
    assert 'not isolated E2E' in response.json()['host']['scope']
    assert 'must-never-appear' not in response.text and str(path) not in response.text


@pytest.mark.parametrize('content', ['broken', '{}', '[]', '{"cpu_percent": 99}',
    json.dumps({'epoch_seconds': time.time()-120, 'cpu_percent': 99}),
    json.dumps({'epoch_seconds': time.time()+120, 'cpu_percent': 99}), 'x'*262145],
    ids=['broken','no-timestamp','not-object','no-timestamp-with-cpu','stale','future','oversized'])
def test_broken_stale_future_or_oversized_snapshots_never_show_healthy(client, monkeypatch, tmp_path, content):
    path=tmp_path/'host.json'; path.write_text(content)
    monkeypatch.setenv('RESOURCE_SNAPSHOT_FILE', str(path))
    actor=signup_home(client, 'invalid-resource@test.io')
    body=client.get('/api/v1/training/resources', headers=actor.h).json()
    assert body['host']['state'] != 'current' and body['host']['cpuPercent'] is None


def test_runtime_inference_stage_numbers_and_errors_do_not_leak_inputs(client, monkeypatch, tmp_path):
    path=tmp_path/'worker.json'
    path.write_text(json.dumps({'epoch_seconds':time.time(), 'running':True,
        'model_version':'model:v1', 'feature_backend':'cpu-optimized', 'has_error':True,
        'last_error':'raw-sensitive-input', 'counts':{'published':5},
        'processing_ms':{'features':{'count':4,'p95':12.3},'inference':{'p95':45.6}}}))
    monkeypatch.setenv('INFERENCE_STATUS_FILE',str(path))
    actor=signup_home(client,'inference-resource@test.io')
    response=client.get('/api/v1/training/resources',headers=actor.h)
    assert response.json()['inference']['processingMs']['features']['p95']==12.3
    assert response.json()['inference']['hasError'] is True
    assert 'raw-sensitive-input' not in response.text


def test_training_evidence_is_not_live_training_or_automatic_deployment(client, monkeypatch, tmp_path):
    monkeypatch.setenv('TRAINING_ARTIFACTS_DIR', str(tmp_path))
    monkeypatch.setattr('wifiguard_api.routers.training._mlflow_healthy',lambda:False)
    actor=signup_home(client,'evidence-only@test.io')
    body=client.get('/api/v1/training/status',headers=actor.h).json()
    assert body['evidenceOnly'] is True and body['trainingRunning'] is None
    assert body['automaticRetrainingConfigured'] is False and body['overall']=='degraded'


def test_member_cannot_run_audited_training_validation(client):
    root=signup_root(client,'operation-root@test.io')
    member=signup_member(client,'operation-member@test.io',invite_code(client,root))
    assert client.post('/api/v1/training/validate',headers=member.h).status_code==403
