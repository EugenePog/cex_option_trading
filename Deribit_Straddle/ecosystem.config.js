// PM2 ecosystem for Deribit_Straddle.
//
// Auto-detects which environments exist on this host based on the project
// root path. On the prod server only the "prod" entries appear; on the dev
// Mac only the "test" entries appear. Eliminates the footgun where
// `pm2 start` would try to launch a cwd that doesn't exist on the host.
//
// Usage from the project root:
//   pm2 start ecosystem.config.js                       # both apps for THIS host
//   pm2 start ecosystem.config.js --only deribit-reporting-test
//   pm2 logs deribit-straddle-test
//   pm2 logs deribit-reporting-test --lines 200
//   pm2 restart deribit-reporting-prod                  # manual report trigger
//   pm2 save                                            # persist across reboots
//   pm2 startup                                         # one-time: generate boot script
//
// -------------------------------------------------------------------
// OS cron for the reporting one-shot
// PM2's own cron_restart runs in the daemon timezone — unreliable for our
// case. Use OS cron + `pm2 restart` instead.
//
//   which pm2                                           # note the absolute path
//   crontab -e                                          # as root on the prod box
//
//   15 10 * * * HOME=/root /usr/local/bin/pm2 restart deribit-reporting-prod \
//     >> /root/cex_option_trading/Deribit_Straddle/data/logs/cron.log 2>&1
//
//   HOME=/root is required — PM2 stores state in $HOME/.pm2 and cron has a
//   stripped environment that may not set HOME.
//   Verify with: crontab -l
//
// Smoke-test the report without waiting for the cron tick:
//   pm2 restart deribit-reporting-prod
//   pm2 logs deribit-reporting-prod
// (the process runs one cycle and goes back to "stopped" — that's the
// expected idle state for cron-driven one-shots.)
// -------------------------------------------------------------------

const fs = require("fs");

// Project root per environment. Single source of truth — every other
// path (script, cwd, logs) is derived from this.
const ROOT_BY_ENV = {
  prod: "/root/cex_option_trading/Deribit_Straddle",
  test: "/Users/eugene/Documents/projects/cex_option_trading/Deribit_Straddle",
};

// Keep only environments whose project root exists on this host.
const ENVS = Object.entries(ROOT_BY_ENV)
  .filter(([, root]) => fs.existsSync(root))
  .map(([env]) => env);

// Shared config — every field identical across environments and apps.
const baseConfig = {
  script:       ".venv/bin/python3",   // resolved relative to cwd
  interpreter:  "none",                // tells pm2 not to wrap the script
  kill_timeout: 15000,
  env: {
    PYTHONUNBUFFERED: "1",
    TZ:               "UTC",
  },
  merge_logs: true,
  time:       true,
};

// Long-running strategy daemon — restarts on crash.
const makeStrategyApp = (env) => ({
  ...baseConfig,
  name:          `deribit-straddle-${env}`,
  args:          `-m app --env test`, // hardcoded --env test run, when prod account is ready, use: `-m app --env ${env}`,
  cwd:           ROOT_BY_ENV[env],
  autorestart:   true,
  max_restarts:  10,
  restart_delay: 10000,
  min_uptime:    "30s",                // crashes within 30s of start count as failure
  out_file:      `./data/logs/deribit-straddle-${env}.out.log`,
  error_file:    `./data/logs/deribit-straddle-${env}.err.log`,
});

// Reporting — one-shot. OS cron triggers `pm2 restart` once a day.
// autorestart:false prevents PM2 from respawning after the clean exit.
const makeReportingApp = (env) => ({
  ...baseConfig,
  name:        `deribit-reporting-${env}`,
  args:        "-m app_reporting --once",
  cwd:         ROOT_BY_ENV[env],
  autorestart: false,
  out_file:    `./data/logs/app-reporting-${env}.out.log`,
  error_file:  `./data/logs/app-reporting-${env}.err.log`,
});

module.exports = {
  apps: ENVS.flatMap((env) => [makeStrategyApp(env), makeReportingApp(env)]),
};