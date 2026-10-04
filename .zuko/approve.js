#!/usr/bin/env node
'use strict';

// Records a human-acknowledged approval that unlocks specific dangerous-path
// files so Claude can apply a high-impact change directly - instead of handing
// the human a patch to apply by hand. Written into the repo by /bootstrap (as
// .zuko/approve.js) and called by /zuko:build and /zuko:fix AFTER the human has
// confirmed, in plain language, that they understand the change is high-impact
// and authorize it.
//
// Usage:
//   node .zuko/approve.js --files "a/b.ts,c/d.ts" --reason "<plain summary>" --ack "<human words>" [--ttl-min 30]
//   node .zuko/approve.js --clear            (clear all approvals after the change lands)
//
// Approvals are file-scoped (exact paths, never globs), time-boxed (default 30
// min), and recorded with the human's own words for an audit trail. They unlock
// ONLY the project dangerous-path list - never the catastrophic universal rules
// (rm -rf, force-push to the protected branch, disabling TLS, writing secrets),
// which stay blocked no matter what.
//
// Self-contained (no requires outside Node core) so it works as a copied file.

const fs = require('fs');
const path = require('path');

function arg(name, fallback = null) {
  const i = process.argv.indexOf(name);
  if (i === -1) return fallback;
  const next = process.argv[i + 1];
  return next && !next.startsWith('--') ? next : true;
}

const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const file = path.join(projectDir, '.zuko', 'approvals.json');

// E2: never mint or clear an approval during an unattended overnight run. The
// launcher sets ZUKO_OVERNIGHT; the dawn approval happens in a separate
// interactive session where it is not set. Defense in depth with the guard,
// which also blocks invoking this script overnight.
if (process.env.ZUKO_OVERNIGHT) {
  process.stderr.write(
    'approve: refusing during an unattended overnight run (ZUKO_OVERNIGHT is set). ' +
      'Overnight, dangerous changes are STAGED, not approved; a human approves them in the morning from an interactive session.\n'
  );
  process.exit(2);
}

function read() {
  try {
    const d = JSON.parse(fs.readFileSync(file, 'utf8'));
    return Array.isArray(d.approvals) ? d.approvals : [];
  } catch {
    return [];
  }
}
function write(approvals) {
  fs.mkdirSync(path.join(projectDir, '.zuko'), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ approvals }, null, 2) + '\n', 'utf8');
}

if (arg('--clear')) {
  write([]);
  process.stdout.write('Approvals cleared.\n');
  process.exit(0);
}

const filesArg = arg('--files');
if (!filesArg || filesArg === true) {
  process.stderr.write('approve: --files "a,b,c" is required (or --clear)\n');
  process.exit(1);
}
const reason = arg('--reason', '') === true ? '' : arg('--reason', '');
const ack = arg('--ack', '') === true ? '' : arg('--ack', '');
const hash = arg('--hash', '') === true ? '' : arg('--hash', ''); // E5: bind to exact content
const ttlMin = Number(arg('--ttl-min', '30')) || 30;
const ttlMs = ttlMin * 60 * 1000;
const now = Date.now();

const paths = filesArg.split(',').map((s) => s.trim().replace(/\\/g, '/')).filter(Boolean);
const kept = read().filter((a) => a && !paths.includes(a.path));
for (const p of paths) kept.push({ path: p, reason, ack, grantedAtMs: now, ttlMs, ...(hash ? { hash } : {}) });
write(kept);

process.stdout.write(
  `Approved ${paths.length} file(s) for ${ttlMin} min:\n  ` + paths.join('\n  ') + '\n'
);
