#!/usr/bin/env node
'use strict';

// The formatter Zuko runs after every edit (format.command in CLAUDE.md): ruff format, on
// Python files only. Ruff would otherwise reformat code examples inside Markdown docs and
// fail on YAML/CSV. Silent and best-effort, like Zuko's own hook.

const { spawnSync } = require('child_process');
const path = require('path');
const { pythonFor } = require('./py');

const file = process.argv[2];
if (!file || !/\.py$/i.test(file)) process.exit(0);
const root = path.resolve(__dirname, '..', '..');
spawnSync(pythonFor(root),['-m', 'ruff', 'format', '--quiet', file], { cwd: root, stdio: 'ignore', timeout: 60000 });
process.exit(0);
