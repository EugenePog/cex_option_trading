def strangle():
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
                        closest_call = get_otm_next_expiry(
                            self.api_key, 
                            self.api_secret, 
                            self.passphrase, 
                            self.flag, 
                            token, 
                            "CALL")
                        logger.info(f"Closest CALL: {closest_call}")
                        closest_put = get_otm_next_expiry(
                            self.api_key, 
                            self.api_secret, 
                            self.passphrase, 
                            self.flag, 
                            token, 
                            "PUT")
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
                                "SHORT")