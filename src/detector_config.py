"""Compatibility re-export for detector configuration registry."""

from __future__ import annotations

from src.config.detector_config import (
    DETECTOR_CONFIGS,
    DetectorConfig,
    get_detector_config,
)

__all__ = ["DetectorConfig", "DETECTOR_CONFIGS", "get_detector_config"]
