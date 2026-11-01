'use strict';
const path = require('node:path');
const { ApiController } = require('../api-controller.cjs');
const controller = new ApiController({ projectRoot: path.resolve(__dirname, '..', '..'),
  runner: path.resolve(__dirname, '..', 'backend_runner.py'), port: Number(process.argv[2]),
  logDir: process.argv[3], extraEnv: { AI_MEMORY_DB_PATH: path.join(process.argv[3], 'crash-test.sqlite3') } });
controller.on('state', state => { if (process.connected) process.send({ phase: state.phase }); });
process.on('message', message => { if (message === 'simulate-owner-crash') process.exit(0); });
controller.start().catch(error => { if (process.connected) process.send({ phase: 'error', error: error.message }); });
