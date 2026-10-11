import os
import re

_ENV_VAR = re.compile(r"\$\{([^}]+)\}")


def expand_env(value):
    """Recursively replace ${VAR} in strings with the environment variable's value."""
    if isinstance(value, str):
        def replace(match):
            var_name = match.group(1)
            if var_name not in os.environ:
                raise ValueError(f"Environment variable '{var_name}' used in agent config is not set")
            return os.environ[var_name]

        return _ENV_VAR.sub(replace, value)
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    return value
