from app import logger
from pydantic import BaseModel
from typing import Optional, Dict, List
from app.config import configuration
import os
from decimal import Decimal
import asyncio
import json
from cryptography.fernet import Fernet
import hmac
import hashlib
import time
import aiohttp
from web3 import Web3
from datetime import datetime, timezone

class OKXPositionMonitor:
    def __init__(self):
        self.api_base_url = str("http://") + str(configuration.API_HOST_IP) + ":" + str(configuration.API_PORT)
        self.api_key = os.getenv("DEPOSIT_API_KEY")
        self.api_secret = os.getenv("DEPOSIT_API_SECRET")
        self.encryption_key = os.getenv("DEPOSIT_ENCRYPTION_KEY")
        self.cipher = Fernet(self.encryption_key.encode()) if self.encryption_key else None
        self.check_interval = configuration.API_CHECK_INTERVAL  # seconds
        self.tokens = configuration.LIST_OF_TOKENS
        
        if not all([self.api_key, self.api_secret, self.encryption_key]):
            logger.error("Missing API credentials in environment variables")
            raise ValueError("Missing API credentials in environment variables")
        
        if not self.tokens:
            logger.error("No tokens configured in configuration.list_of_tokens")
            raise ValueError("No tokens configured in configuration.list_of_tokens")

    async def process_deposits(self, results: List[Dict]):
        """Process deposit requests for tokens with available space"""
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

    
    async def check_pool_space(self, token: str) -> Dict:
        """Check available pool space for a specific token"""
        try:
            logger.info(f"Checking pool space for token: {token}")
            
            # Prepare request payload
            payload = {
                "token": self._encrypt_field(token)
            }
            
            # Generate timestamp and signature
            timestamp = int(time.time())
            signature = self._generate_signature(payload, timestamp)
            
            # Prepare headers with authentication
            headers = {
                "X-API-Key": self.api_key,
                "X-Signature": signature,
                "X-Timestamp": str(timestamp),
                "Content-Type": "application/json"
            }
            
            # Make async HTTPS request
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_base_url}/yield-basis/available_space",
                    json=payload,
                    headers=headers,
                    ssl=True,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"Pool space check successful for {token}")
                        return {"token": token, "status": "success", "data": result}
                    elif response.status == 401:
                        logger.error(f"Authentication failed for {token}")
                        return {"token": token, "status": "auth_failed", "data": None}
                    else:
                        error_text = await response.text()
                        logger.error(f"Request failed for {token} with status {response.status}: {error_text}")
                        return {"token": token, "status": "error", "data": error_text}
        
        except aiohttp.ClientError as e:
            logger.error(f"HTTP request error for {token}: {e}")
            return {"token": token, "status": "connection_error", "data": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error for {token}: {e}")
            return {"token": token, "status": "error", "data": str(e)}
    
    async def check_all_tokens(self) -> List[Dict]:
        """Check pool space for all configured tokens concurrently"""
        tasks = [self.check_pool_space(token) for token in self.tokens]
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
        
        return processed_results
    
    async def run_monitoring_loop(self):
        """Main monitoring loop that checks positions every interval"""
        logger.info(f"Starting {configuration.PROJECT_NAME} for tokens: {', '.join(self.tokens)}")
        logger.info(f"Check interval: {self.check_interval} seconds")

        while True:
            try:
                # Check straddles for all tokens in the list
                results = await self.check_straddles_all_tokens()
                logger.info(f"Result of the check iteration: {results}")

                # Process straddles for tokens with available space
                await self.process_straddles(results)

                # Wait for next iteration
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop iteration: {e}", exc_info=True)
                # Continue despite errors
                await asyncio.sleep(self.check_interval)



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