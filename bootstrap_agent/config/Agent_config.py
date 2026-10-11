from bootstrap_agent.config.Config import Config


class AgentConfig(Config):
    def __init__(self, config_path=None):

        super().__init__(config_path=config_path)
