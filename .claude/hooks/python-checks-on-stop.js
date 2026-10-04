#!/usr/bin/env node
'use strict';

// Stop hook: before Claude ends a turn in which Python code (or the configs and inputs it
// reads) changed, run the style check and the test suite; a failure blocks the stop.
// Zuko's own pre-stop gate only notices JavaScript-family files, so this is its Python twin.
// Nothing changed (per `git status`) means nothing to check.

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { pythonFor } = require('./py');

const CHECKED = /\.(py|ya?ml|toml|ini|csv)$/i;
const IGNORED = /(^|\/)(\.venv|data|reports|coverage|reference)\//;

function readStdin() {
  try {
    return JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
  } catch {
    return {};
  }
}

function changedFiles(projectDir) {
  const res = spawnSync('git', ['status', '--porcelain', '--untracked-files=all'], {
    cwd: projectDir,
    encoding: 'utf8',
    timeout: 15000,
  });
  if (res.status !== 0 || !res.stdout) return [];
  return res.stdout
    .split('\n')
    .map((l) => l.slice(3).trim().replace(/^"|"$/g, '').split(' -> ').pop())
    .filter((p) => p && CHECKED.test(p) && !IGNORED.test(p));
}

function run(exe, args, projectDir) {
  // nosemgrep: javascript.lang.security.detect-child-process.detect-child-process -- exe is the project's own Python (from py.js) with fixed ruff/pytest arguments; no shell, no outside input
  const res = spawnSync(exe, args, { cwd: projectDir, encoding: 'utf8', timeout: 600000 });
  const out = `${res.stdout || ''}\n${res.stderr || ''}`.trim().split('\n');
  return { ok: res.status === 0, tail: out.slice(-25).join('\n') };
}

const payload = readStdin();
if (payload.stop_hook_active) process.exit(0); // already blocked once: do not loop
const projectDir = payload.cwd || process.env.CLAUDE_PROJECT_DIR || process.cwd();
if (!fs.existsSync(path.join(projectDir, 'riffi_ingest'))) process.exit(0); // not the engine project
if (changedFiles(projectDir).length === 0) process.exit(0);

const py = pythonFor(projectDir);
const failures = [];
const lint = run(py, ['-m', 'ruff', 'check', '.'], projectDir);
if (!lint.ok) failures.push(`style check (ruff check .) failed:\n${lint.tail}`);
const tests = run(py, ['-m', 'pytest', '-q', 'tests'], projectDir);
if (!tests.ok) failures.push(`tests (pytest -q tests) failed:\n${tests.tail}`);
if (failures.length) {
  process.stdout.write(
    JSON.stringify({
      decision: 'block',
      reason: `Python checks are red; fix them before finishing (never weaken a test to pass).\n\n${failures.join('\n\n')}`,
    })
  );
}
process.exit(0);
