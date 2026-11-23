"""Courier configuration: one YAML file per container, plus WAHA_API_KEY from the environment."""

from .loader import ConfigError, LoadedConfig, load_config
from .model import (
    CourierConfig,
    FilterSettings,
    ForwarderConfig,
    HealthMonitorConfig,
    MatrixSettings,
    WahaSettings,
)


__all__ = [
    "ConfigError",
    "CourierConfig",
    "FilterSettings",
    "ForwarderConfig",
    "HealthMonitorConfig",
    "LoadedConfig",
    "MatrixSettings",
    "WahaSettings",
    "load_config",
]
