import json
import time

import numpy as np
import pytest

from test_worker import FakeBuilder, FakeEngine, FakeProducer, feature_record, settings, signal_message
from wifiguard_contracts import topics
from wifiguard_inference.worker import InferenceWorker


def test_snapshot_is_bounded_atomic_and_model_math_is_unchanged(tmp_path):
    target=tmp_path/'runtime/status.json'
    worker=InferenceWorker(settings(status_file=target),engine=FakeEngine(),feature_builder=FakeBuilder())
    worker._producer=FakeProducer()
    assert worker.handle(topics.KAFKA_FEATURE_STREAM,feature_record(signal_message()))
    worker._write_runtime_status()
    value=json.loads(target.read_text())
    assert value['counts']['published']==1 and value['feature_backend']=='reference'
    assert value['processing_ms']['total']['p95'] >= 0
    assert 0 <= time.time()-value['epoch_seconds']<3
    assert 'amplitude' not in target.read_text() and len(target.read_bytes())<16384
    assert list(target.parent.glob('*.tmp'))==[]


def test_monitor_failure_does_not_fail_publication(tmp_path):
    blocked=tmp_path/'not-a-directory'; blocked.write_text('owned fixture')
    worker=InferenceWorker(settings(status_file=blocked/'status.json'),engine=FakeEngine(),feature_builder=FakeBuilder())
    worker._producer=FakeProducer()
    assert worker.handle(topics.KAFKA_FEATURE_STREAM,feature_record(signal_message()))
    worker._write_runtime_status()
    assert worker.status()['counts']['published']==1


def test_cpu_optimized_keeps_model_input_shapes_and_matches_reference(monkeypatch):
    monkeypatch.setenv('SSQ_GPU','0')
    from wifiguard_inference.features import CpuOptimizedFeatureBuilder, LiveFeatureBuilder
    t=np.arange(960,dtype=np.float64)/320
    phases=np.arange(30,dtype=np.float64)[None,:]/13
    sample=(20 + np.sin(2*np.pi*7*t[:,None]+phases)
        + .3*np.sin(2*np.pi*14*t[:,None]-phases)
        + np.random.default_rng(7).normal(0,.05,(960,30))).astype(np.float32)
    reference=LiveFeatureBuilder().build_batch([sample],320)
    optimized=CpuOptimizedFeatureBuilder().build_batch([sample],320)
    np.testing.assert_allclose(optimized.s3,reference.s3,rtol=1e-5,atol=1e-6)
    np.testing.assert_allclose(optimized.acf,reference.acf,rtol=1e-5,atol=1e-6)


def test_cpu_feature_backend_cannot_accidentally_enable_gpu_kernels(monkeypatch):
    monkeypatch.setenv('SSQ_GPU','1')
    with pytest.raises(ValueError,match='SSQ_GPU=0'):
        settings(feature_backend='cpu-optimized').validate(require_checkpoint=False,require_kafka=False)
