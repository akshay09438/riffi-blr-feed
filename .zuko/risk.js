'use strict';

// Contextual risk calibration (Workstream C). Turns the binary "is this a
// dangerous glob" check into a true-risk SCORE and a ROUTE, so only the genuine
// riskiest changes are parked for a human while provably-benign ones can flow.
//
// Two design rules make this safe to trust:
//   1. MULTIPLICATIVE - one high axis dominates; a high-sensitivity OR
//      irreversible OR live-user change cannot be averaged down by low axes.
//   2. CONSERVATIVE BY DEFAULT - any missing/unknown input scores its axis to
//      the MAXIMUM. Silence and corruption always score UP, never down.
//
// Inputs (all human-set in the profile, except `facts` which the router derives
// deterministically from the diff and which may only ESCALATE, never relax):
//   sensitivity:   cosmetic | internal | user-data | auth | payments
//   reversibility: additive | reversible | irreversible
//   maturity:      prelaunch | early | live | scale
//   facts: { altersRls, widensQueryScope, irreversibleWrite, dropsOrRenamesColumn, touchesManagedSurface }
//
// Pure and unit-tested: same inputs -> same route, every time. It NEVER lowers
// the deterministic floor; it only governs HOW an approval is obtained
// (auto / one informed tap / a full human decision).

const SENS = { cosmetic: 1, internal: 2, 'user-data': 6, auth: 10, payments: 10 };
const REV = { additive: 1, reversible: 3, irreversible: 8 };
const MAT = { prelaunch: 1, early: 2, live: 6, scale: 8 };

const SENS_MAX = 10; // unknown sensitivity == auth-level
const REV_MAX = 8; // unknown reversibility == irreversible
const MAT_MAX = 8; // unknown maturity == scale (most live users)

// A surface counts as "sensitive" (deny-listed from auto-apply) at user-data+.
const SENSITIVE_AT = 6;

function resolve(map, key, fallback) {
  if (typeof key !== 'string') return fallback;
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : fallback;
}

function score(input) {
  const inp = input || {};
  const facts = inp.facts || {};
  const reasons = [];

  let sens = resolve(SENS, inp.sensitivity, SENS_MAX);
  let rev = resolve(REV, inp.reversibility, REV_MAX);
  const mat = resolve(MAT, inp.maturity, MAT_MAX);

  if (!Object.prototype.hasOwnProperty.call(SENS, inp.sensitivity)) reasons.push('sensitivity unknown -> treated as auth-level');
  if (!Object.prototype.hasOwnProperty.call(MAT, inp.maturity)) reasons.push('maturity unknown -> treated as live/scale');

  // Diff facts may only ESCALATE the axes (a comment cannot make a change safer).
  if (facts.dropsOrRenamesColumn || facts.irreversibleWrite) {
    rev = Math.max(rev, REV.irreversible);
    reasons.push('irreversible data operation in the diff');
  }
  if (facts.altersRls) {
    sens = Math.max(sens, SENS.auth);
    reasons.push('alters an RLS / authorization policy');
  }
  if (facts.widensQueryScope) {
    sens = Math.max(sens, SENS['user-data']);
    reasons.push('widens a data-scoping query');
  }
  const isPayments = inp.sensitivity === 'payments';
  const sensitiveSurface = sens >= SENSITIVE_AT;
  const irreversible = rev >= REV.irreversible;
  const liveOrScale = mat >= MAT.live;
  const denyAuto = sensitiveSurface || irreversible || facts.touchesManagedSurface;

  const value = sens * rev * mat; // multiplicative: one high axis dominates
  // A single maxed axis (e.g. auth == 10) reads HIGH even when the others are 1
  // - the point of multiplicative dominance is that it is not averaged down.
  const tier = value >= 10 ? 'high' : value >= 3 ? 'medium' : 'low';

  // --- routing ---
  let route;
  const humanRequired =
    isPayments ||
    (denyAuto && liveOrScale) || // a sensitive/irreversible surface on a live app
    (irreversible && (liveOrScale || sensitiveSurface)) ||
    value >= 60;

  const autoEligible =
    !denyAuto &&
    sens <= SENS.internal &&
    rev === REV.additive &&
    mat <= MAT.early &&
    value <= 4;

  if (humanRequired) {
    route = 'human-required';
    reasons.push(isPayments ? 'payments surface' : 'sensitive/irreversible change on a live or high-blast app');
  } else if (autoEligible) {
    route = 'auto-apply';
    reasons.push('provably benign: non-sensitive, additive, pre/early-launch');
  } else {
    route = 'one-tap-stage';
    reasons.push('staged for one informed human approval');
  }

  return { score: value, route, tier, reasons };
}

// CLI arg parsing, so the build router and goodnight can call this
// deterministically: node .zuko/risk.js --sensitivity auth --reversibility
// reversible --maturity live --facts altersRls,widensQueryScope
function parseArgs(argv) {
  const get = (name) => {
    const i = argv.indexOf(name);
    return i !== -1 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : undefined;
  };
  const facts = {};
  const factsArg = get('--facts');
  if (factsArg) for (const f of factsArg.split(',').map((s) => s.trim()).filter(Boolean)) facts[f] = true;
  return {
    sensitivity: get('--sensitivity'),
    reversibility: get('--reversibility'),
    maturity: get('--maturity'),
    facts,
  };
}

module.exports = { score, parseArgs, SENS, REV, MAT };

if (require.main === module) {
  process.stdout.write(JSON.stringify(score(parseArgs(process.argv.slice(2))), null, 2) + '\n');
}
