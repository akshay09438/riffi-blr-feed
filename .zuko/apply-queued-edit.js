#!/usr/bin/env node
'use strict';

// goodnight v2: at DAWN (an interactive session, ZUKO_OVERNIGHT not set), apply
// a staged dangerous change the human just approved. Verifies the staged
// content against its hash (so what applies is exactly what was reviewed and
// shown on the card), writes the file, and records provenance the merge-gate
// (E4) checks - so a goodnight PR can't carry an un-approved dangerous change.
// Self-contained (Node core only) so it runs as a copied .zuko/ file.
//
// Usage:
//   node .zuko/apply-queued-edit.js --task <id> --ack "<the human's own words>"
//
// Refuses under ZUKO_OVERNIGHT (this is a human, awake, decision) and refuses an
// `unsafe` card (those go through an attended /zuko:build, not a one-tap).

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

function arg(name, fallback = null) {
  const i = process.argv.indexOf(name);
  if (i === -1) return fallback;
  const next = process.argv[i + 1];
  return next && !next.startsWith('--') ? next : true;
}
function fail(msg, code = 1) {
  process.stderr.write('apply-queued-edit: ' + msg + '\n');
  process.exit(code);
}

if (process.env.ZUKO_OVERNIGHT) {
  fail('refusing during an unattended overnight run - staged changes are applied only at dawn, by a human, in an interactive session.', 2);
}

const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const task = arg('--task');
const ack = arg('--ack');
if (!task || task === true) fail('--task <id> is required');
if (!ack || ack === true || !String(ack).trim()) fail('--ack "<the human\'s own words>" is required - a real, informed yes');

const safeId = String(task).replace(/[^A-Za-z0-9._-]/g, '_');
const queueDir = path.join(projectDir, '.zuko', 'goodnight', 'queue');
const jsonPath = path.join(queueDir, safeId + '.json');
const contentPath = path.join(queueDir, safeId + '.content');
if (!fs.existsSync(jsonPath) || !fs.existsSync(contentPath)) fail(`no staged card "${safeId}" in the queue`);

const entry = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
const content = fs.readFileSync(contentPath, 'utf8');
const hash = crypto.createHash('sha256').update(content).digest('hex');
if (hash !== entry.hash) fail('staged content does not match its recorded hash - refusing to apply tampered work');
if (entry.verdict === 'unsafe') fail('this card was rated UNSAFE - apply it through an attended /zuko:build, not a one-tap');

// Apply the exact reviewed content.
const target = path.join(projectDir, entry.file);
fs.mkdirSync(path.dirname(target), { recursive: true });
fs.writeFileSync(target, content, 'utf8');

// Record provenance (the human's yes, bound to the exact hash). The merge-gate
// reads this; an applied dangerous change with no provenance entry fails CI.
const appliedPath = path.join(projectDir, '.zuko', 'goodnight', 'applied.json');
let applied = [];
try {
  const d = JSON.parse(fs.readFileSync(appliedPath, 'utf8'));
  if (Array.isArray(d.applied)) applied = d.applied;
} catch {
  /* first entry */
}
applied = applied.filter((a) => a && a.file !== entry.file);
applied.push({ task: safeId, file: entry.file, hash, verdict: entry.verdict, ack: String(ack), appliedAtMs: Date.now() });
fs.writeFileSync(appliedPath, JSON.stringify({ applied }, null, 2) + '\n', 'utf8');

// The card is consumed.
fs.rmSync(jsonPath, { force: true });
fs.rmSync(contentPath, { force: true });

process.stdout.write(`Applied ${entry.file} and recorded the approval. Run the tests, then it folds into the morning PR.\n`);
