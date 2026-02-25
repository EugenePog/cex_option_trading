class LongPutCallStrategy(StrategyBase):

    async def should_run(self) -> bool:
        return is_within_timeframe(
            self.config["put_call_timeframe_start"],
            self.config["put_call_timeframe_end"]
        )

    async def execute(self):
        # Same pattern as above, long put/call logic here
        pass




                    # It is the time for opening long put call position
                    logger.info(f"Checking conditions for opening long put call positions for token {token}")
                    if is_within_timeframe(self.put_call_timeframe_start[token], self.put_call_timeframe_end[token]):
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