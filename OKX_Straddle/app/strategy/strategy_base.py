from app import logger
from abc import ABC, abstractmethod

class StrategyBase(ABC):
    def __init__(self, token: str, config: dict, api_credentials: dict):
        self.token = token
        self.config = config
        self.api_key = api_credentials["api_key"]
        self.api_secret = api_credentials["api_secret"]
        self.passphrase = api_credentials["passphrase"]
        self.flag = api_credentials["flag"]

    @abstractmethod
    async def should_run(self) -> bool:
        """Check if strategy conditions are met"""
        pass

    @abstractmethod
    async def execute(self):
        """Execute the strategy logic"""
        pass

    async def run(self):
        """Entry point"""
        if await self.should_run():
            logger.info(f"Running strategy [{self.__class__.__name__}] for {self.token} - conditions are met ✅")
            await self.execute()
        else:
            logger.info(f"Skipping strategy [{self.__class__.__name__}] for {self.token} - conditions to run strategy not met ❌")