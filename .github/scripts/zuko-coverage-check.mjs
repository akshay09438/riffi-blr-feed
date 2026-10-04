#!/usr/bin/env node
// Coverage-no-regression on touched files. The un-gameable backstop: deleting
// or weakening a test to make code pass drops coverage on the file it touched,
// and this fails the check. Deterministic enforcement of "never weaken a test."
//
// Compares per-file line coverage of the changed files between the base branch
// and the PR head. A drop on any changed, already-covered file fails.
//
// Usage:
//   node zuko-coverage-check.mjs --base <base-summary.json> --head <head-summary.json> --changed <changed-files.txt>
//
// Summary files are jest's coverage/coverage-summary.json (json-summary reporter).
// changed-files.txt is a newline-separated list of repo-relative paths.

import fs from 'node:fs';
import path from 'node:path';

function arg(name) {
  const i = process.argv.indexOf(name);
  return i !== -1 ? process.argv[i + 1] : null;
}

const EPSILON = 0.01; // ignore floating-point noise
const COVERABLE = /\.(py|ts|tsx|js|jsx|mjs|cjs)$/i;

function loadSummary(file) {
  if (!file || !fs.existsSync(file)) return {};
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
  const map = {};
  for (const [key, val] of Object.entries(raw)) {
    if (key === 'total') continue;
    if (!val || !val.lines || typeof val.lines.pct !== 'number') continue;
    // Normalize absolute paths to repo-relative, forward-slash.
    const rel = path.relative(process.cwd(), key).split(path.sep).join('/');
    map[rel] = val.lines.pct;
  }
  return map;
}

const baseMap = loadSummary(arg('--base'));
const headMap = loadSummary(arg('--head'));
const changedFile = arg('--changed');
const changed = changedFile && fs.existsSync(changedFile)
  ? fs.readFileSync(changedFile, 'utf8').split('\n').map((s) => s.trim()).filter(Boolean)
  : [];

const regressions = [];
for (const file of changed) {
  if (!COVERABLE.test(file)) continue;
  const head = headMap[file];
  const base = baseMap[file];
  // New file (not in base): no regression possible. A brand-new untested file
  // is a review concern, not a no-regression failure.
  if (base === undefined || head === undefined) continue;
  if (head < base - EPSILON) {
    regressions.push({ file, base, head });
  }
}

if (regressions.length) {
  console.error('Coverage regression on touched files (a test may have been weakened or deleted):');
  for (const r of regressions) {
    console.error(`  ${r.file}: ${r.base.toFixed(2)}% -> ${r.head.toFixed(2)}%`);
  }
  console.error('\nRestore coverage on these files. Never weaken or delete a test to make code pass.');
  process.exit(1);
}

console.log(`Coverage-no-regression: OK (${changed.length} changed file(s) checked, no drops).`);
