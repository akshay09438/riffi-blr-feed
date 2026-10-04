#!/usr/bin/env node
'use strict';

// Runs the project's Python the same way on the Windows laptop and in a Linux cloud session:
// the .venv if there is one, otherwise the system Python (`python` on Windows, `python3` elsewhere).
// Usage: node .claude/hooks/py.js -m pytest -q tests

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

function pythonFor(root) {
  // root is the project folder (CLAUDE_PROJECT_DIR or cwd); the rest of each path is fixed, so no outside input reaches it
  // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
  const win = path.join(root, '.venv', 'Scripts', 'python.exe');
  // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
  const nix = path.join(root, '.venv', 'bin', 'python');
  if (fs.existsSync(win)) return win;
  if (fs.existsSync(nix)) return nix;
  return process.platform === 'win32' ? 'python' : 'python3';
}

module.exports = { pythonFor };

if (require.main === module) {
  const root = path.resolve(__dirname, '..', '..');
  const res = spawnSync(pythonFor(root), process.argv.slice(2), { cwd: process.cwd(), stdio: 'inherit' });
  process.exit(res.status === null ? 1 : res.status);
}
