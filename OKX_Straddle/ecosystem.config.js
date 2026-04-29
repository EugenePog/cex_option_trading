// PM2 ecosystem for OKX_Straddle.
//
// For the first start:
//   cd /root/cex_option_trading/OKX_Straddle
//   pm2 start ecosystem.config.js
//   pm2 save
//   pm2 list                                    # both should show up

// Checks:
//   pm2 show app-reporting                      # check "cron restart" field
//   To smoke-test the report without waiting until 09:00 UTC, just pm2 restart app-reporting — it'll run one cycle and go back to "stopped" status, which is the expected idle state for cron jobs.

// Usage from the project root:
//   pm2 start ecosystem.config.js
//   pm2 logs okx-straddle-prod
//   pm2 logs app-reporting
//   pm2 restart app-reporting
//   pm2 save                          # persist across reboots
//   pm2 startup                       # one-time: generate boot script
//
// To trigger the report manually (outside the cron schedule):
//   pm2 restart app-reporting
//
// Note: PM2 reads cron_restart in the SYSTEM timezone. Setting TZ=UTC in the
// process env makes "0 9 * * *" mean 09:00 UTC regardless of host locale.

module.exports = {
  apps: [
    // -------------------------------------------------------------------
    // Strategy bot — long-running daemon.
    // Mirrors the manual command:
    //   pm2 start .venv/bin/python3 --name okx-straddle-prod \
    //     --cwd /root/cex_option_trading/OKX_Straddle -- -m app --env prod
    // -------------------------------------------------------------------
    {
      name: "okx-straddle-prod",
      script: "/root/cex_option_trading/OKX_Straddle/.venv/bin/python3",
      args: "-m app --env prod",// change to test/prod if needed
      interpreter: "none",
      cwd: "/root/cex_option_trading/OKX_Straddle",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 10000,
      kill_timeout: 15000,
      env: {
        PYTHONUNBUFFERED: "1",
        TZ: "UTC",
      },
      out_file: "./data/logs/okx-straddle-prod.out.log",
      error_file: "./data/logs/okx-straddle-prod.err.log",
      merge_logs: true,
      time: true,
    },

    // -------------------------------------------------------------------
    // Reporting — cron-driven one-shot at given time every day.
    // PM2 restarts the process on the cron schedule; the script runs one
    // cycle (--once) and exits. autorestart:false prevents PM2 from
    // immediately respawning it after a clean exit.
    // -------------------------------------------------------------------
    {
      name: "app-reporting",
      script: "/root/cex_option_trading/OKX_Straddle/.venv/bin/python3",
      args: "-m app_reporting --once",
      interpreter: "none",
      cwd: "/root/cex_option_trading/OKX_Straddle",
      autorestart: false,
      cron_restart: "15 10 * * *",        // 08:15 UTC daily. Cron works in local time only (08:15 UTC == 10:15 CET)
      kill_timeout: 15000,
      env: {
        PYTHONUNBUFFERED: "1",
        TZ: "UTC",
      },
      out_file: "./data/logs/app-reporting.out.log",
      error_file: "./data/logs/app-reporting.err.log",
      merge_logs: true,
      time: true,
    },
  ],
};