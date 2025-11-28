import dataclasses
import json
import logging
import os
from typing import Optional

from .db import State

log = logging.getLogger("solarpi")

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config/")), "solarpi"
)
CONFIG_FILE = os.path.join(CONFIG_DIR, "solarpi.json")


@dataclasses.dataclass
class Config:
    battery_capacity: float = 600
    # 54:14:A7:53:14:E9 BTG964
    battery_monitor_addr: Optional[str] = ""
    # C8:47:80:0D:2C:6A ChargePro
    solar_charger_addr: Optional[str] = ""


CONFIG: Optional[Config] = None  # noqa: F824


def load() -> Config:
    """Load config from disk and set it to the global CONFIG object"""
    global CONFIG
    log.info(f"Reading config {CONFIG_FILE}")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                config = Config(**json.load(f))
        else:
            log.debug("Config file does not exist.. Using default")
            config = Config()
    except Exception as e:
        log.warning("Failed to read config. Using default")
        log.exception(e)
        config = Config()
    apply(config)
    CONFIG = config
    return config


def apply(config: Config):
    try:
        log.info(f"Applying config {config}")
        battery_capacity = config.battery_capacity
        assert battery_capacity > 0, "Battery capacity must be greater than 0"
        State.battery_capacity = battery_capacity
    except Exception as e:
        log.error("Failed to apply config")
        log.exception(e)


def save(**kwargs):
    """Save config to disk. If no config is passed the global CONFIG is used."""
    try:
        if CONFIG is None:
            config = load()
        else:
            config = CONFIG
        for k, v in kwargs.items():
            setattr(config, k, v)
        apply(config)
        log.info(f"Saving config {config} to {CONFIG_FILE}")
        with open(CONFIG_FILE, "w") as f:
            json.dump(dataclasses.asdict(config), f, indent=2)
    except Exception as e:
        log.error("Failed to save config")
        log.exception(e)
