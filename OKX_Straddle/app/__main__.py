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
from app.functions import is_within_timeframe
from app.okx_functions_option import get_otm_next_expiry, open_position, close_all_open_options, get_option_summary, get_available_near_money_options

class PositionMonitor:
    def __init__(self):
        self.api_key = os.getenv("OKX_K_API_KEY")
        self.api_secret = os.getenv("OKX_K_API_SECRET")
        self.passphrase = os.getenv("OKX_K_PASSPHRASE")
        self.flag = os.getenv("OKX_K_FLAG")

        self.check_interval = configuration.API_CHECK_INTERVAL  # seconds
        self.tokens = configuration.LIST_OF_TOKENS
        
        if not all([self.api_key, self.api_secret, self.passphrase]):
            logger.error("Missing API credentials in environment variables")
            raise ValueError("Missing API credentials in environment variables")
        
        if not self.tokens:
            logger.error("No tokens configured in configuration.list_of_tokens")
            raise ValueError("No tokens configured in configuration.list_of_tokens")

        self.straddle_slippage_tolerance = configuration.STRADDLE_SLIPPAGE_TOLERANCE
        self.straddle_bid_ask_threshold = configuration.STRADDLE_BID_ASK_THRESHOLD
        self.straddle_amount = configuration.STRADDLE_AMOUNT
        self.straddle_timeframe_start = configuration.STRADDLE_TIMEFRAME_START
        self.straddle_timeframe_end = configuration.STRADDLE_TIMEFRAME_END
        self.straddle_allowed_strikes = configuration.STRADDLE_ALLOWED_STRIKES
        
        self.put_call_slippage_tolerance = configuration.PUT_CALL_SLIPPAGE_TOLERANCE
        self.put_call_bid_ask_threshold = configuration.PUT_CALL_BID_ASK_THRESHOLD
        self.put_call_amount = configuration.PUT_CALL_AMOUNT
        self.put_call_timeframe_start = configuration.PUT_CALL_TIMEFRAME_START
        self.put_call_timeframe_end = configuration.PUT_CALL_TIMEFRAME_END
        self.put_call_indent = configuration.PUT_CALL_INDENT

        self.okx_position_size_multiplier = configuration.OKX_POSITION_SIZE_MULTIPLIER
    
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
                    # !!! To be developed
                    
                    # Check time slot for opening short straddle position
                    logger.info(f"Checking conditions for opening straddle positions for token {token}")
                    if is_within_timeframe(self.straddle_timeframe_start[token], self.straddle_timeframe_end[token]):
                        logger.info(f"Process straddle positions for token {token}")
                        
                        # Close all unexecuted orders
                        logger.info(f"Close all unexecuted orders for token {token} if exist (will be reopenned with updated limit price)")
                        attempt = 0
                        while attempt < 10:
                            attempt += 1
                            close_all_open_options_response = close_all_open_options(
                                self.api_key, 
                                self.api_secret, 
                                self.passphrase, 
                                self.flag, 
                                token)
                            if close_all_open_options_response.get("status") == "ok":
                                logger.info(f"All orders closed successfully on attempt {attempt}.")
                                break

                        # Calculate position sizes to be opened to fill the required size
                        logger.info(f"Calculate position sizes for token {token} to be openned (straddle)")
                        
                        # Get position size from settings
                        straddle_call_size = int(self.straddle_amount[token] * self.okx_position_size_multiplier[token])
                        straddle_put_size = int(self.straddle_amount[token] * self.okx_position_size_multiplier[token])

                        short_option_summary_result = get_option_summary(
                            self.api_key, 
                            self.api_secret, 
                            self.passphrase, 
                            self.flag, 
                            token,
                            "SHORT")

                        logger.info(f"Result of the check open straddle legs for token {token}: {short_option_summary_result}")

                        # Calculate legs size to open
                        straddle_call_size_to_open = straddle_call_size - short_option_summary_result['total_calls']
                        straddle_put_size_to_open = straddle_put_size - short_option_summary_result['total_puts']
                        logger.info(f"Straddle position {token}. Calls: Plan - {straddle_call_size}, Openned - {short_option_summary_result['total_calls']}, To_open - {straddle_call_size_to_open}")
                        logger.info(f"Straddle position {token}. Puts: Plan - {straddle_put_size}, Openned - {short_option_summary_result['total_puts']}, To_open - {straddle_put_size_to_open}")
                        
                        # Define put call IDs for positions to be opened
                        closest_call_put = get_available_near_money_options(
                            self.api_key, 
                            self.api_secret, 
                            self.passphrase, 
                            self.flag, 
                            token, 
                            self.straddle_allowed_strikes[token],
                            1)
                        
                        if not closest_call_put["calls"][0] or not closest_call_put["puts"][0]:
                            logger.error(f"No options found expiring on given date within given strikes list")
                            raise ValueError(f"No options found expiring on given date within given strikes list")
                        closest_call = closest_call_put["calls"][0]
                        closest_put = closest_call_put["puts"][0]
                        logger.info(f"Closest CALL: {closest_call}")
                        logger.info(f"Closest PUT: {closest_put}")

                        # Open straddles for tokens with available space
                        if straddle_call_size_to_open > 0 or straddle_put_size_to_open > 0:
                            short_straddle_result = open_position(
                                closest_call["instId"],
                                closest_put["instId"],
                                straddle_call_size_to_open,
                                straddle_put_size_to_open,
                                self.api_key, 
                                self.api_secret, 
                                self.passphrase, 
                                self.flag,
                                self.straddle_slippage_tolerance[token],
                                self.straddle_bid_ask_threshold[token],
                                "SHORT")

                    # It is the time for opening long put call position
                    #logger.info(f"Checking conditions for opening long put call positions for token {token}")
                    #if is_within_timeframe(self.put_call_timeframe_start[token], self.put_call_timeframe_end[token]):
                    if False:
                        logger.info(f"Process long put call positions for token {token}")

                        # Close all unexecuted orders
                        logger.info(f"Close all unexecuted orders for token {token} if exist (will be reopenned with updated limit price)")
                        attempt = 0
                        while attempt < 10:
                            attempt += 1
                            close_all_open_options_response = close_all_open_options(
                                self.api_key, 
                                self.api_secret, 
                                self.passphrase, 
                                self.flag, 
                                token)
                            if close_all_open_options_response.get("status") == "ok":
                                logger.info(f"All orders closed successfully on attempt {attempt}.")
                                break

                        # Calculate position sizes to be opened to fill the required size
                        logger.info(f"Calculate position sizes for token {token} to be openned (long put call)")
                        
                        # Get position size from settings
                        call_size = int(self.put_call_amount[token] * self.okx_position_size_multiplier[token])
                        put_size = int(self.put_call_amount[token] * self.okx_position_size_multiplier[token])

                        long_option_summary_result = get_option_summary(
                            self.api_key, 
                            self.api_secret, 
                            self.passphrase, 
                            self.flag, 
                            token,
                            "LONG")

                        logger.info(f"Result of the check open long put call for token {token}: {long_option_summary_result}")

                        # Calculate size to open
                        call_size_to_open = call_size - long_option_summary_result['total_calls']
                        put_size_to_open = put_size - long_option_summary_result['total_puts']
                        logger.info(f"Long position {token}. Calls: Plan - {call_size}, Openned - {long_option_summary_result['total_calls']}, To_open - {call_size_to_open}")
                        logger.info(f"Long position {token}. Puts: Plan - {put_size}, Openned - {long_option_summary_result['total_puts']}, To_open - {put_size_to_open}")
                        
                        # Define put call IDs for positions to be opened
                        closest_call = get_otm_next_expiry(
                            self.api_key, 
                            self.api_secret, 
                            self.passphrase, 
                            self.flag, 
                            token, 
                            "CALL",
                            self.put_call_indent[token])
                        logger.info(f"Closest CALL for long: {closest_call}")
                        closest_put = get_otm_next_expiry(
                            self.api_key, 
                            self.api_secret, 
                            self.passphrase, 
                            self.flag, 
                            token, 
                            "PUT",
                            self.put_call_indent[token])
                        logger.info(f"Closest PUT for long: {closest_put}")

                        # Open long put call for tokens with available space
                        if call_size_to_open > 0 or put_size_to_open > 0:
                            long_put_call_result = open_position(
                                closest_call["instId"],
                                closest_put["instId"],
                                call_size_to_open,
                                put_size_to_open,
                                self.api_key, 
                                self.api_secret, 
                                self.passphrase, 
                                self.flag,
                                self.put_call_slippage_tolerance[token],
                                self.put_call_bid_ask_threshold[token],
                                "LONG")

                # Wait for next iteration
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop iteration: {e}", exc_info=True)
                # Continue despite errors
                await asyncio.sleep(self.check_interval)


        

async def main():
    """Main entry point"""
    position_monitor = PositionMonitor()
    await position_monitor.run_monitoring_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(f"{configuration.PROJECT_NAME} stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)