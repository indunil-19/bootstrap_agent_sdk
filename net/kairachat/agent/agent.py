import logging


class Agent:
    def __init__(self, name, config_path, tools, log_level=logging.INFO):
        self.name = name
        self.config_path = config_path
        self.tools = tools
        self.log_level = log_level
        self.agent = None  # Placeholder for the actual agent instance


