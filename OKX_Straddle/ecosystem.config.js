// PM2 ecosystem for OKX_Straddle.
//
// For the first start:
//   cd /root/cex_option_trading/OKX_Straddle
//   pm2 start ecosystem.config.js (--only app-reporting)
//   pm2 save
//   pm2 list                                    # both should show up

// OS cron:
// which pm2
// crontab -e (as root, since the process runs under root) and add:
// 15 10 * * * HOME=/root /usr/local/bin/pm2 restart app-reporting >> /root/cex_option_trading/OKX_Straddle/data/logs/cron.log 2>&1
// Replace /usr/local/bin/pm2 with whatever which pm2 gave you. The HOME=/root is important — PM2 stores its state in $HOME/.pm2, and cron runs with a stripped environment that may not set HOME correctly. The redirect captures any cron-side errors so you can debug later.
// Verify: crontab -l


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
      name: "okx-straddle-prod",  // prod
      //name: "okx-straddle-test",  // test
      script: "/root/cex_option_trading/OKX_Straddle/.venv/bin/python3", //prod
      //script: "/Users/eugene/Documents/projects/cex_option_trading/OKX_Straddle/.venv/bin/python3",  // test
      args: "-m app --env prod",  // prod
      //args: "-m app --env test",  // test
      interpreter: "none",
      cwd: "/root/cex_option_trading/OKX_Straddle",  // prod
      //cwd: "/Users/eugene/Documents/projects/cex_option_trading/OKX_Straddle", // test
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
    // Reporting — cron-driven one-shot at 09:00 UTC every day.
    // PM2 restarts the process on the cron schedule; the script runs one
    // cycle (--once) and exits. autorestart:false prevents PM2 from
    // immediately respawning it after a clean exit.
    // -------------------------------------------------------------------
    {
      name: "app-reporting",
      script: "/root/cex_option_trading/OKX_Straddle/.venv/bin/python3",   // prod
      //script: "/Users/eugene/Documents/projects/cex_option_trading/OKX_Straddle/.venv/bin/python3",   // test
      args: "-m app_reporting --once",
      interpreter: "none",
      cwd: "/root/cex_option_trading/OKX_Straddle", // prod
      //cwd: "/Users/eugene/Documents/projects/cex_option_trading/OKX_Straddle",  // test
      autorestart: false,
      //cron_restart: "00 11 * * *",        // 08:15 UTC daily. Cron works in local time only (08:15 UTC == 10:15 CET) - not working in pm2, handled by OS cron, see instruction on rows 9-14
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