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
import okx.Account as Account
import okx.Trade as Trade
from app.functions import parse_positions, check_short_position_balance, is_within_timeframe
from app.functions_option import get_otm_next_expiry, open_short_strangle

class OKXPositionMonitor:
    def __init__(self):
        self.api_key = os.getenv("OKX_API_KEY_DEMO")
        self.api_secret = os.getenv("OKX_API_SECRET_DEMO")
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

        self.straddle_slippage_tolerance = configuration.STRADDLE_SLIPPAGE_TOLERANCE
        self.straddle_amount = configuration.STRADDLE_AMOUNT
        self.straddle_timeframe_start = configuration.STRADDLE_TIMEFRAME_START
        self.straddle_timeframe_end = configuration.STRADDLE_TIMEFRAME_END
        
        self.put_call_slippage_tolerance = configuration.PUT_CALL_SLIPPAGE_TOLERANCE
        self.put_call_amount = configuration.PUT_CALL_AMOUNT
        self.put_call_timeframe_start = configuration.PUT_CALL_TIMEFRAME_START
        self.put_call_timeframe_end = configuration.PUT_CALL_TIMEFRAME_END
    
    async def run_monitoring_loop(self):
        """Main monitoring loop that checks positions every interval"""
        logger.info(f"Starting {configuration.PROJECT_NAME} for tokens: {', '.join(self.tokens)}, time: {datetime.now(timezone.utc).replace(microsecond=0)}")
        logger.info(f"Check interval: {self.check_interval} seconds")

        while True:
            try:
                # Loop over tokens in the list
                for token in self.tokens:
                    logger.info(f"Checking loop for token: {token}, time: {datetime.now(timezone.utc).replace(microsecond=0)}")

                    # Check if there is enought liquidity
                    # Add liquidity if needed 
                    
                    # It is the time is for opening straddle position
                    logger.info(f"Checking conditions for opening straddle positions for token {token}")
                    if is_within_timeframe(self.straddle_timeframe_start[token], self.straddle_timeframe_end[token]):
                        logger.info(f"Execute opening straddle positions for token {token}")
                        
                        # Calculate positions to be opened
                        closest_call = get_otm_next_expiry(self.api_key, self.api_secret, self.passphrase, self.flag, token, "CALL")
                        logger.info(f"Closest CALL: {closest_call}")
                        closest_put = get_otm_next_expiry(self.api_key, self.api_secret, self.passphrase, self.flag, token, "PUT")
                        logger.info(f"Closest PUT: {closest_put}")
                        
                        # Check straddles for all tokens in the list
                        tasks = [self.check_straddle_one_token(token)]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                        # Handle any exceptions that occurred
                        processed_results = []
                        for result in results:
                            if isinstance(result, Exception):
                                logger.error(f"Exception for token {token}: {result}")
                                processed_results.append({
                                    "token": token,
                                    "status": "exception",
                                    "data": str(result)
                                })
                            else:
                                processed_results.append(result)

                        logger.info(f"Result of the check iteration for token {token}: {processed_results}")

                        # Calculate delta to open straddle positions

                        # Open straddles for tokens with available space
                        short_strangle_result = open_short_strangle(
                            closest_call["instId"],
                            closest_put["instId"],
                            int(self.straddle_amount[token] * 100),
                            self.api_key, 
                            self.api_secret, 
                            self.passphrase, 
                            self.flag)

                    # It is the time for opening put call position
                    logger.info(f"Checking conditions for opening put call positions for token {token}")
                    if is_within_timeframe(self.put_call_timeframe_start[token], self.put_call_timeframe_end[token]):
                        logger.info(f"Execute opening put call positions for token {token}")

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

            token_uly = token + "-USD"

            # Get public data
            #publicDataAPI = PublicData.PublicAPI(flag = self.flag)
            #result = publicDataAPI.get_instruments(instType = "OPTION", uly = token_uly)
            #logger.info(result)

            accountAPI = Account.AccountAPI(self.api_key, self.api_secret, self.passphrase, False, self.flag)

            #result = accountAPI.get_account_balance()
            #logger.info(result)

            result = accountAPI.get_positions(instType="OPTION")
            #logger.info(result)

            result_parsed = parse_positions(result, token)
            logger.info(result_parsed)

            checked_difference = check_short_position_balance(result_parsed)
            logger.info(f"Check difference in positions: {checked_difference}")

            #tradeAPI = Trade.TradeAPI(self.api_key, self.api_secret, self.passphrase, False, self.flag)

            return {
                "token": token,
                "status": "success",
                "data": str(checked_difference)
            }
            
        
        except aiohttp.ClientError as e:
            logger.error(f"HTTP request error for {token}: {e}")
            return {"token": token, "status": "connection_error", "data": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error for {token}: {e}")
            return {"token": token, "status": "error", "data": str(e)}
        

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