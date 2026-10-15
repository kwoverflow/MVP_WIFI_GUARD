"""Compatibility imports for the active in-house segmentation feature path."""

from csi_fall_segmentation.online import (
    IncompatibleSignalError,
    SegmentationFeatureBuilder as LiveFeatureBuilder,
    SegmentationFeatures as LiveFeatures,
)

__all__ = ["IncompatibleSignalError", "LiveFeatureBuilder", "LiveFeatures"]


class CpuOptimizedFeatureBuilder:
    """Same resample/Q/PCA/CWT/ACF equations; cached constants/JIT, original input carriers."""
    def __init__(self):
        from csi_fall_segmentation.edge_features import CpuEdgeFeatureBuilder
        self.builder = CpuEdgeFeatureBuilder()

    def start(self):
        pass

    def warmup(self, batch_size=4, fs_hz=320.0):
        self.builder.warmup()

    def build_batch(self, amplitudes, fs_hz):
        import numpy as np
        if not amplitudes:
            raise ValueError('empty inference batch')
        result = [self.builder.build(value, fs_hz) for value in amplitudes]
        return LiveFeatures(s3=np.concatenate([value.s3 for value in result]),
                            acf=np.concatenate([value.acf for value in result]))
