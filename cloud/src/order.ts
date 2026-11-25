// Scope consistency and pricing. Robot-specific validation (limits, envelope, duration) is NOT here: the lab agent
// runs the Python adapter on every quote (one source of truth) and again before any motion.

export interface Scope { task: string; orderText: string; nonce: string; orderSha256: string }
export interface Pricing { token: string; deliver_within_sec?: number; [task: string]: unknown }

const SCOPE_PATTERNS: Record<keyof Scope, RegExp> = {
  task: /^(joint_waypoints|repeatability)$/,
  orderText: /^\{[\s\S]{20,3000}\}\s*$/,
  nonce: /^[0-9a-f]{32}$/,
  orderSha256: /^[0-9a-f]{64}$/,
};

export async function sha256Hex(text: string): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Returns the parsed order, or a reason why the four scope fields do not name one consistent order. */
export async function checkScope(scope: unknown, robotId: string): Promise<{ order?: any; error?: string }> {
  if (!scope || typeof scope !== 'object') return { error: 'scope must be an object' };
  const s = scope as Record<string, unknown>;
  for (const k of Object.keys(SCOPE_PATTERNS) as (keyof Scope)[]) {
    if (typeof s[k] !== 'string' || !SCOPE_PATTERNS[k].test(s[k] as string)) return { error: `scope.${k} is missing or malformed` };
  }
  let order: any;
  try { order = JSON.parse(s.orderText as string); } catch { return { error: 'scope.orderText is not JSON' }; }
  if (!order || typeof order !== 'object') return { error: 'scope.orderText is not a JSON object' };
  if ((await sha256Hex(s.orderText as string)) !== s.orderSha256) return { error: 'scope.orderSha256 is not the SHA-256 of scope.orderText' };
  if (order.nonce !== s.nonce) return { error: "scope.nonce differs from the order's nonce" };
  const legacy = !('schema' in order);
  const robot = legacy ? 'dobot_cr3a' : order.robot;
  const task = legacy ? 'joint_waypoints' : order.task;
  if (robot !== robotId) return { error: `the order is for robot ${String(robot)}, this provider runs ${robotId}` };
  if (task !== s.task) return { error: "scope.task differs from the order's task" };
  return { order };
}

const BASE_UNITS = /^(0|[1-9][0-9]{0,30})$/;

/** Price in base units (decimal string), from pricing.json: base + per_waypoint x waypoints [x repetitions]. */
export function priceFor(pricing: Pricing, task: string, order: any): { amount?: string; error?: string } {
  const p = pricing[task] as Record<string, string> | undefined;
  if (!p || typeof pricing.token !== 'string' || pricing.token.includes('PLACEHOLDER')) return { error: 'prices are not configured' };
  if (!BASE_UNITS.test(p.base ?? '') || !BASE_UNITS.test(p.per_waypoint ?? '')) return { error: 'prices are not configured' };
  const params = 'schema' in order ? order.params : order;
  const wps = Array.isArray(params?.waypoints) ? params.waypoints.length : 0;
  const reps = task === 'repeatability' ? Number(params?.repetitions ?? 1) : 1;
  const amount = BigInt(p.base) + BigInt(p.per_waypoint) * BigInt(wps) * BigInt(Number.isSafeInteger(reps) ? reps : 1);
  return { amount: amount.toString() };
}

/** Constant-time comparison of two secrets. */
export function sameSecret(a: string | null, b: string | undefined): boolean {
  if (!a || !b) return false;
  const x = new TextEncoder().encode(a), y = new TextEncoder().encode(b);
  if (x.length !== y.length) return false;
  return crypto.subtle.timingSafeEqual(x, y);
}
