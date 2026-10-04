#!/usr/bin/env node
'use strict';

// Deterministic Slack escalation for /stuck. A safety ping cannot depend on the
// model remembering to send it, or shaping the payload safely - so the delivery
// lives here, in tested code. The agent gathers the context and writes it to a
// payload file; this script does the POST.
//
// Usage:
//   node escalate.js --payload <file.json> [--webhook-env ZUKO_SLACK_WEBHOOK]
//
// Payload JSON fields (all optional except blocker):
//   who, repo, branch, framing, blastRadius, blocker, commitUrl, snapshotPath, mention
//
// Contract:
//   - Posts a SUMMARY plus a link. Never posts raw environment variables, full
//     diffs, or stack traces - those can carry secrets and live behind repo
//     access on the pushed branch.
//   - Redacts secret-looking substrings from free-text fields before sending.
//   - On any failure (no webhook, network error, non-2xx) prints a plain
//     fallback telling the human to message the backstop directly. The local
//     snapshot, written before this script runs, means the escalation already
//     succeeded locally. Exits non-zero so the agent surfaces the fallback.

const fs = require('fs');
const https = require('https');
const http = require('http');
const { URL } = require('url');

// High-confidence secret patterns - redacted from any free text before posting.
const SECRET_PATTERNS = [
  /-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----[\s\S]*?-----END[^-]*-----/g,
  /\bAKIA[0-9A-Z]{16}\b/g,
  /\bASIA[0-9A-Z]{16}\b/g,
  /https:\/\/hooks\.slack\.com\/services\/[A-Za-z0-9]+\/[A-Za-z0-9]+\/[A-Za-z0-9]+/g,
  /\bgh[pousr]_[A-Za-z0-9]{36,}\b/g,
  /\bgithub_pat_[A-Za-z0-9_]{60,}\b/g,
  /\bAIza[0-9A-Za-z_\-]{35}\b/g,
  /\bsk_live_[0-9A-Za-z]{20,}\b/g,
  /\bxox[baprs]-[0-9A-Za-z-]{10,}\b/g,
  /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g, // JWT
];

function redact(text) {
  if (!text) return '';
  let out = String(text);
  for (const re of SECRET_PATTERNS) out = out.replace(re, '[redacted]');
  return out;
}

function arg(name, fallback = null) {
  const i = process.argv.indexOf(name);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

function fail(message, code = 1) {
  process.stderr.write(message + '\n');
  process.exit(code);
}

function buildMessage(p) {
  const mention = p.mention ? `${p.mention} ` : '';
  const lines = [
    `${mention}*Zuko escalation - someone is stuck*`,
    p.who ? `*Who:* ${redact(p.who)}` : null,
    p.repo ? `*Repo:* ${redact(p.repo)}${p.branch ? ` (branch \`${redact(p.branch)}\`)` : ''}` : null,
    p.framing ? `*Working on:* ${redact(p.framing)}` : null,
    p.blastRadius ? `*Blast radius:* ${redact(p.blastRadius)}` : null,
    `*Blocker:* ${redact(p.blocker || '(not specified)')}`,
    p.commitUrl ? `*Snapshot pushed:* ${redact(p.commitUrl)}` : null,
    p.snapshotPath ? `*Local snapshot:* ${redact(p.snapshotPath)}` : null,
  ].filter(Boolean);
  return lines.join('\n');
}

function postToSlack(webhookUrl, text) {
  return new Promise((resolve, reject) => {
    let u;
    try {
      u = new URL(webhookUrl);
    } catch {
      return reject(new Error('webhook URL is malformed'));
    }
    const body = JSON.stringify({ text });
    // Slack webhooks are always https; http support exists so the POST path is
    // testable against a local server without weakening TLS anywhere.
    const transport = u.protocol === 'http:' ? http : https;
    const req = transport.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === 'http:' ? 80 : 443),
        path: u.pathname + u.search,
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
        timeout: 15000,
      },
      (res) => {
        let data = '';
        res.on('data', (c) => (data += c));
        res.on('end', () => {
          if (res.statusCode >= 200 && res.statusCode < 300) resolve(data);
          else reject(new Error(`Slack returned HTTP ${res.statusCode}: ${data.slice(0, 200)}`));
        });
      }
    );
    req.on('timeout', () => req.destroy(new Error('request timed out')));
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

function fallbackText(p, reason) {
  return (
    `Could not reach Slack (${reason}).\n` +
    `Your local snapshot is saved${p.snapshotPath ? ' at ' + p.snapshotPath : ''}` +
    `${p.commitUrl ? ' and pushed to ' + p.commitUrl : ''}.\n` +
    `Message the backstop directly: who=${p.who || '?'}, repo=${p.repo || '?'}, branch=${p.branch || '?'}, blocker=${redact(p.blocker || '?')}.`
  );
}

async function main() {
  const payloadPath = arg('--payload');
  if (!payloadPath) fail('escalate: --payload <file.json> is required');

  let p;
  try {
    p = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));
  } catch (e) {
    fail('escalate: could not read payload file: ' + e.message);
  }

  const webhookEnv = arg('--webhook-env', 'ZUKO_SLACK_WEBHOOK');
  const webhookUrl = process.env[webhookEnv];
  const text = buildMessage(p);

  if (!webhookUrl) {
    fail(
      `The Slack webhook env var ${webhookEnv} is not set on this machine, so the ping was not sent.\n` +
        fallbackText(p, `${webhookEnv} not set`)
    );
  }

  try {
    await postToSlack(webhookUrl, text);
    process.stdout.write('Escalation posted to Slack.\n');
    process.exit(0);
  } catch (e) {
    fail(fallbackText(p, e.message));
  }
}

// Only run when invoked directly, not when required by tests.
if (require.main === module) {
  main();
}

module.exports = { buildMessage, redact, main };
