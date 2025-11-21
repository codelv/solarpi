import os
import json
import dataclasses
import logging

from typing import Any, Optional

from .db import State

log = logging.getLogger("solarpi")

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config/")), "solarpi")
CONFIG_FILE = os.path.join(CONFIG_DIR, 'solarpi.json')

@dataclasses.dataclass
class Config:
    battery_capacity: float = 600

CONFIG: Optional[Config] = None

def load() -> Config:
    """ Load config from disk and set it to the global CONFIG object """
    global CONFIG
    log.info(f"Reading config {CONFIG_FILE}")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = Config(**json.load(f))
        else:
            log.debug(f"Config file does not exist.. Using default")
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
    """ Save config to disk. If no config is passed the global CONFIG is used. """
    try:
        global  CONFIG
        if CONFIG is None:
            config = load()
        else:
            config = CONFIG
        for k, v in kwargs.items():
            setattr(config, k, v)
        apply(config)
        log.info(f"Saving config {config} to {CONFIG_FILE}")
        with open(CONFIG_FILE, 'w') as f:
            json.dump(dataclasses.asdict(config), f, indent=2)
    except Exception as e:
        log.error("Failed to save config")
        log.exception(e)
