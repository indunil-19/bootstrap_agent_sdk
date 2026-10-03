import json
from abc import ABC
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class Config(ABC):
    def __init__(self, config_path=None):
        self.config_path = config_path
        self._load_config()

    def get(self, key, default=None):
        """Retrieve a configuration value by key."""
        return self.config.get(key, default)

    def _load_config(self):
        try:
            with open(self.config_path) as config_file:
                self.config = json.load(config_file)
        except FileNotFoundError:
            logger.error("Config file not found")
            raise FileNotFoundError(f"Configuration file not found at: {self.config_path}")
        except json.JSONDecodeError:
            logger.error("Config file not valid")
            raise ValueError(f"Configuration file at {self.config_path} is not a valid JSON.")