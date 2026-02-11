from app import logger
from pydantic import BaseModel
from typing import Optional, Dict, List
from app.config import configuration
import os
import asyncio
import aiohttp
from web3 import Web3
from datetime import datetime, timezone
import okx.PublicData as PublicData

class OKXPositionMonitor:
    def __init__(self):
        self.api_key = os.getenv("OKX_API_KEY")
        self.api_secret = os.getenv("OKX_API_SECRET")
        self.passphrase = os.getenv("OKX_PASSPHRASE")
        self.flag = os.getenv("OKX_FLAG")

        self.check_interval = configuration.API_CHECK_INTERVAL  # seconds
        self.tokens = configuration.LIST_OF_TOKENS
        
        if not all([self.api_key, self.api_secret, self.passphrase]):
            logger.error("Missing API credentials in environment variables")
            raise ValueError("Missing API credentials in environment variables")
        
        if not self.tokens:
            logger.error("No tokens configured in configuration.list_of_tokens")
            raise ValueError("No tokens configured in configuration.list_of_tokens")

    
    
    async def run_monitoring_loop(self):
        """Main monitoring loop that checks positions every interval"""
        logger.info(f"Starting {configuration.PROJECT_NAME} for tokens: {', '.join(self.tokens)}")
        logger.info(f"Check interval: {self.check_interval} seconds")

        while True:
            try:
                # Check straddles for all tokens in the list
                tasks = [self.check_straddle_one_token(token) for token in self.tokens]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Handle any exceptions that occurred
                processed_results = []
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Exception for token {self.tokens[i]}: {result}")
                        processed_results.append({
                            "token": self.tokens[i],
                            "status": "exception",
                            "data": str(result)
                        })
                    else:
                        processed_results.append(result)

                logger.info(f"Result of the check iteration: {processed_results}")

                # Process straddles for tokens with available space
                # await self.process_straddles(processed_results)

                # Wait for next iteration
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop iteration: {e}", exc_info=True)
                # Continue despite errors
                await asyncio.sleep(self.check_interval)


    async def check_straddle_one_token(self, token: str) -> Dict:
        """Check straddle for a specific token"""
        try:
            logger.info(f"Checking straddle for token: {token}")
            
            flag = "1"  # live trading: 0, demo trading: 1

            publicDataAPI = PublicData.PublicAPI(flag = flag)

            result = publicDataAPI.get_instruments(instType = "SWAP")
            logger.info(result)
            
        
        except aiohttp.ClientError as e:
            logger.error(f"HTTP request error for {token}: {e}")
            return {"token": token, "status": "connection_error", "data": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error for {token}: {e}")
            return {"token": token, "status": "error", "data": str(e)}
        
    async def process_straddles(self, results: List[Dict]):
        """Process straddles for tokens with identified available space"""
        deposit_tasks = []
        
        for result in results:
            # Check conditions: 
            #   status = 'success' 
            #   and data.status = 'success' 
            #   and data.available_space > 0
            #   and timestamp not very old (< API_SPACE_AGE)
            if (result.get("status") == "success" and 
                result.get("data") and 
                result["data"].get("status") == "success" and 
                result["data"].get("available_space", 0) > 0):
                
                token = result["data"]["token"]
                available_space = result["data"]["available_space"]

                # Parse the ISO format timestamp
                data_timestamp = datetime.fromisoformat(str(result["data"]["timestamp"]).replace('Z', '+00:00'))
                current_timestamp = datetime.now(timezone.utc)
                
                # Calculate age in seconds
                age_seconds = (current_timestamp - data_timestamp).total_seconds()
                
                if age_seconds < configuration.MAX_SPACE_AGE:
                    logger.info(f"Available space detected for {token}: {available_space}")
                    deposit_tasks.append(self.deposit_endpoint(token, available_space))
                else:
                    logger.info(f"Available space detected for {token}: {available_space} but it is too old: {age_seconds}")
        
        if deposit_tasks:
            logger.info(f"Executing {len(deposit_tasks)} deposit operations")
            deposit_results = await asyncio.gather(*deposit_tasks, return_exceptions=True)
            
            # Log deposit results
            for i, deposit_result in enumerate(deposit_results):
                if isinstance(deposit_result, Exception):
                    logger.error(f"Deposit failed with exception: {deposit_result}")
                else:
                    logger.info(f"Deposit result: {deposit_result}")
        else:
            logger.info("No deposits needed - all pools are full or unavailable")

async def main():
    """Main entry point"""
    position_monitor = OKXPositionMonitor()
    await position_monitor.run_monitoring_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(f"{configuration.PROJECT_NAME} stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)