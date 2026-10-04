#!/usr/bin/env node
'use strict';

// goodnight v2: serialize a would-be dangerous change to the overnight QUEUE
// instead of applying it. Overnight, the guard denies the real write (no
// approval exists, and ZUKO_OVERNIGHT is set), so a dangerous edit is staged
// here as content + a plain-language card + the reviewer verdict, and waits for
// the human's morning yes. Self-contained (Node core only) so it runs as a
// copied .zuko/ file.
//
// Usage:
//   node .zuko/stage-dangerous-edit.js --task <id> --file <relpath> \
//     --content-file <path-to-new-content> --verdict <safe|not-proven-safe|unsafe> \
//     [--card-file <path-to-plain-language-card>]
//
// Writes:
//   .zuko/goodnight/queue/<task>.content   (the exact new file content)
//   .zuko/goodnight/queue/<task>.json      ({ task, file, hash, verdict, card, createdAtMs })
// The content hash binds the eventual approval to exactly this change (E5).

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

function arg(name, fallback = null) {
  const i = process.argv.indexOf(name);
  if (i === -1) return fallback;
  const next = process.argv[i + 1];
  return next && !next.startsWith('--') ? next : true;
}

function fail(msg) {
  process.stderr.write('stage-dangerous-edit: ' + msg + '\n');
  process.exit(1);
}

const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const task = arg('--task');
const file = arg('--file');
const contentFile = arg('--content-file');
const verdict = arg('--verdict', 'not-proven-safe');
const cardFile = arg('--card-file');

if (!task || task === true) fail('--task <id> is required');
if (!file || file === true) fail('--file <relpath> is required');
if (!contentFile || contentFile === true) fail('--content-file <path> is required');
if (!['safe', 'not-proven-safe', 'unsafe'].includes(verdict)) fail('--verdict must be safe | not-proven-safe | unsafe');

const content = fs.readFileSync(contentFile, 'utf8');
const hash = crypto.createHash('sha256').update(content).digest('hex');
const card = cardFile && cardFile !== true ? fs.readFileSync(cardFile, 'utf8') : '';

const queueDir = path.join(projectDir, '.zuko', 'goodnight', 'queue');
fs.mkdirSync(queueDir, { recursive: true });
const safeId = String(task).replace(/[^A-Za-z0-9._-]/g, '_');
fs.writeFileSync(path.join(queueDir, safeId + '.content'), content, 'utf8');
const entry = { task: safeId, file: file.replace(/\\/g, '/'), hash, verdict, card, createdAtMs: Date.now() };
fs.writeFileSync(path.join(queueDir, safeId + '.json'), JSON.stringify(entry, null, 2) + '\n', 'utf8');

process.stdout.write(
  `Staged ${entry.file} (verdict: ${verdict}) as queue card "${safeId}" - NOT applied. ` +
    `It waits for the human's approval at dawn.\n`
);
