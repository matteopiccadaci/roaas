// One Durable Object per robot: quotes, the job queue, the robot's live status, and the lab agent's WebSocket.
// The lab agent connects OUT to this object (no inbound port in the lab). While it is connected and reports a ready
// robot with the operator on duty, quotes are accepted, and each one is validated by the lab's own Python code.
import { DurableObject } from 'cloudflare:workers';
import type { Env } from './index';
import { checkScope, priceFor, sha256Hex, type Pricing } from './order';
import pricingFile from '../../crow/pricing.json';

const ACTIVE = ['started', 'claimed', 'awaiting_approval', 'running', 'failed'];
// Sent again to an agent that (re)connects: only jobs that never reached the robot. A 'running' job the agent is not
// handling any more was cut by a crash or restart: it becomes 'failed' and runs again only on `roaas ops retry`.
const RESEND = ['started', 'claimed', 'awaiting_approval'];
const INTERRUPTED = 'interrupted: the lab agent stopped during the run. Nothing is re-run by itself; `roaas ops retry` asks the operator again';
const LAB_STATES = new Set(['claimed', 'awaiting_approval', 'running', 'failed', 'declined']);
const STATUS_MAX_AGE_MS = 30_000;
const VALIDATE_TIMEOUT_MS = 8_000;
const VALIDATE_PER_MINUTE = 30;
const MAX_EPISODE_BYTES = 1_000_000;
const CHUNK = 1_000_000;                  // SQLite rows hold at most 2 MB
const DAY_MS = 86_400_000;

type Json = Record<string, any>;
const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });

export class Lab extends DurableObject<Env> {
  private sql: SqlStorage;
  private pending = new Map<string, (v: Json) => void>();

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS quotes (id TEXT PRIMARY KEY, buyer TEXT, scope TEXT, price TEXT,
      deliver_within_sec INTEGER, created_ms INTEGER, expires_ms INTEGER)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, commitment_id TEXT UNIQUE, quote_id TEXT,
      buyer TEXT, scope TEXT, state TEXT, detail TEXT, created_ms INTEGER, updated_ms INTEGER, deliver_by_ms INTEGER,
      telemetry_sha256 TEXT, telemetry_bytes INTEGER)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS events (job_id TEXT, ts INTEGER, state TEXT, detail TEXT)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)`);
    // Files live here, not in R2: the Workers Free plan includes SQLite storage in Durable Objects.
    this.sql.exec(`CREATE TABLE IF NOT EXISTS files (name TEXT, idx INTEGER, data BLOB, created_ms INTEGER,
      PRIMARY KEY (name, idx))`);
  }

  // ------------------------------------------------------------------ helpers
  private kvGet(k: string): string | null {
    const r = this.sql.exec('SELECT v FROM kv WHERE k = ?', k).toArray();
    return r.length ? (r[0]!.v as string) : null;
  }
  private kvSet(k: string, v: string) { this.sql.exec('INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)', k, v); }
  private putFile(name: string, data: ArrayBuffer) {
    this.sql.exec('DELETE FROM files WHERE name = ?', name);
    const now = Date.now();
    for (let i = 0, off = 0; off < data.byteLength || (i === 0 && data.byteLength === 0); i++, off += CHUNK) {
      this.sql.exec('INSERT INTO files (name, idx, data, created_ms) VALUES (?, ?, ?, ?)', name, i, data.slice(off, off + CHUNK), now);
    }
    this.ctx.waitUntil(this.scheduleCleanup());
  }
  private getFile(name: string): Uint8Array | null {
    const rows = this.sql.exec('SELECT data FROM files WHERE name = ? ORDER BY idx', name).toArray();
    if (!rows.length) return null;
    const parts = rows.map((r) => new Uint8Array(r.data as ArrayBuffer));
    const out = new Uint8Array(parts.reduce((n, a) => n + a.length, 0));
    let off = 0;
    for (const a of parts) { out.set(a, off); off += a.length; }
    return out;
  }
  private async scheduleCleanup() { if ((await this.ctx.storage.getAlarm()) === null) await this.ctx.storage.setAlarm(Date.now() + DAY_MS); }
  /** Daily: delete files older than the retention the deliveries promise (available_until). */
  async alarm() {
    const days = Number(this.env.RETENTION_DAYS || 30);
    this.sql.exec('DELETE FROM files WHERE created_ms < ?', Date.now() - (days + 1) * DAY_MS);
    if (this.sql.exec('SELECT COUNT(*) AS n FROM files').one().n as number) await this.ctx.storage.setAlarm(Date.now() + DAY_MS);
  }
  private agent(): WebSocket | undefined { return this.ctx.getWebSockets('agent')[0]; }
  private pricing(): Pricing { return this.env.PRICING_JSON ? JSON.parse(this.env.PRICING_JSON) : (pricingFile as Pricing); }
  private job(id: string): Json | undefined { return this.sql.exec('SELECT * FROM jobs WHERE id = ?', id).toArray()[0] as Json | undefined; }
  private setState(id: string, state: string, detail = '') {
    const now = Date.now();
    this.sql.exec('UPDATE jobs SET state = ?, detail = ?, updated_ms = ? WHERE id = ?', state, detail, now, id);
    this.sql.exec('INSERT INTO events (job_id, ts, state, detail) VALUES (?, ?, ?, ?)', id, now, state, detail);
  }
  private queueLength(): number {
    return this.sql.exec(`SELECT COUNT(*) AS n FROM jobs WHERE state IN (${ACTIVE.map(() => '?').join(',')}) AND deliver_by_ms > ?`,
      ...ACTIVE, Date.now()).one().n as number;
  }

  status(): Json {
    const raw = this.kvGet('status');
    const st: Json = raw ? JSON.parse(raw) : {};
    const age = Date.now() - Number(this.kvGet('status_ms') ?? 0);
    const connected = this.agent() !== undefined;
    const online = connected && age < STATUS_MAX_AGE_MS;
    // Operator state lives here (roaas ops open|close|pause|resume); the agent's hello sets on_duty initially.
    const onDuty = this.kvGet('on_duty') === '1';
    const paused = this.kvGet('paused') === '1';
    const accepting = online && st.ready === true && onDuty && !paused;
    const reason = !online ? 'lab agent not connected' : paused ? 'paused by the operator'
      : !onDuty ? 'operator off duty' : (st.reason ?? '');
    return { robot: this.env.ROBOT_ID, online, accepting, ready: !!st.ready, on_duty: onDuty, paused, busy: !!st.busy,
             reason, queue: this.queueLength(), last_seen_s: raw ? Math.round(age / 1000) : null };
  }

  private askLab(msg: Json): Promise<Json> {
    const ws = this.agent();
    if (!ws) return Promise.resolve({ ok: false, errors: ['lab agent not connected'] });
    const id = crypto.randomUUID();
    return new Promise((resolve) => {
      const timer = setTimeout(() => { this.pending.delete(id); resolve({ ok: false, errors: ['lab did not answer in time'] }); }, VALIDATE_TIMEOUT_MS);
      this.pending.set(id, (v) => { clearTimeout(timer); resolve(v); });
      ws.send(JSON.stringify({ ...msg, id }));
    });
  }

  private notify(subject: string, text: string) {
    const { RESEND_API_KEY: key, NOTIFY_TO: to, NOTIFY_FROM: from } = this.env;
    if (!key || !to) { console.log(`notify (no email configured): ${subject}`); return; }
    this.ctx.waitUntil(fetch('https://api.resend.com/emails', {
      method: 'POST', headers: { authorization: `Bearer ${key}`, 'content-type': 'application/json' },
      body: JSON.stringify({ from, to: [to], subject: `[ROaaS] ${subject}`, text }),
    }).then((r) => { if (!r.ok) console.log(`notify failed: ${r.status}`); }, (e) => console.log(`notify failed: ${e}`)));
  }

  /** Every operator must show the hold funded. From the first: the real delivery deadline, the buyer, and the terms,
   *  which must name the quoted order (its SHA-256 is in the deliverable's description). */
  private async commitment(cid: string): Promise<{ ok: boolean; why?: string; deliverByMs?: number; buyer?: string; description?: string }> {
    if (this.env.SKIP_HELD_CHECK === '1') return { ok: true };
    const ops = this.env.OPERATORS.split(',').map((s) => s.trim().replace(/\/+$/, '')).filter(Boolean);
    let deliverByMs: number | undefined;
    for (const op of ops) {
      try {
        const r = await fetch(`${op}/v1/commitments/${cid}`);
        const d: Json = r.ok ? await r.json() : {};
        if (d.escrowState !== 'Held') return { ok: false, why: `${op} reports ${d.escrowState ?? r.status}` };
        deliverByMs ??= Date.parse(d.deadlines?.deliverBy ?? '') || undefined;
      } catch {
        return { ok: false, why: `${op} unreachable` };
      }
    }
    try {
      const r = await fetch(`${ops[0]}/v1/commitments/${cid}/terms`);
      const body: Json = r.ok ? await r.json() : {};
      const t: Json = body.terms ?? body;                    // commitment.schema.json: the signed terms
      const buyer = typeof t.buyer === 'string' ? t.buyer : t.buyer?.address;
      const description = t.deliverable?.description;
      if (typeof description !== 'string') return { ok: false, why: 'terms without a deliverable description' };
      return { ok: true, deliverByMs, buyer, description };
    } catch {
      return { ok: false, why: 'terms unreadable' };
    }
  }

  private jobMessage(j: Json, origin: string): Json {
    return { type: 'job', job: { id: j.id, commitment_id: j.commitment_id, buyer: j.buyer, scope: JSON.parse(j.scope),
             state: j.state, deliver_by_ms: j.deliver_by_ms, telemetry_url: `${this.env.PUBLIC_BASE || origin}/t/${j.id}.enc` } };
  }

  private pushPending(origin: string) {
    const ws = this.agent();
    if (!ws) return;
    const rows = this.sql.exec(`SELECT * FROM jobs WHERE state IN (${RESEND.map(() => '?').join(',')}) AND deliver_by_ms > ?
      ORDER BY created_ms`, ...RESEND, Date.now()).toArray();
    for (const j of rows) ws.send(JSON.stringify(this.jobMessage(j as Json, origin)));
  }

  // ------------------------------------------------------------------ HTTP, routed by the Worker
  async fetch(req: Request): Promise<Response> {
    const url = new URL(req.url);
    const p = url.pathname;
    try {
      if (p === '/status') return json(this.status());
      if (p === '/validate') return this.validate(req);
      if (p === '/crow/quote') return this.quote(req);
      if (p === '/crow/jobs') return this.start(req, url.origin);
      let m = p.match(/^\/crow\/jobs\/([A-Za-z0-9-]{1,80})\/result$/);
      if (m) return this.result(m[1]!);
      if (p === '/lab/ws') return this.acceptAgent(req, url.origin);
      let f = p.match(/^\/file\/t\/(job-[0-9a-f]{24})\.enc$/);
      if (f) {
        const data = this.getFile(`t/${f[1]}.enc`);
        return data ? new Response(data, { headers: { 'content-type': 'application/octet-stream' } }) : json({ error: 'not found' }, 404);
      }
      m = p.match(/^\/lab\/jobs\/([A-Za-z0-9-]{1,80})\/(telemetry|episode)$/);
      if (m && req.method === 'PUT') return m[2] === 'telemetry' ? this.putTelemetry(m[1]!, req) : this.putEpisode(m[1]!, req, url.origin);
      const op = p.match(/^\/lab\/ops\/(open|close|pause|resume)$/);
      if (op && req.method === 'POST') return this.operator(op[1]!);
      m = p.match(/^\/lab\/jobs\/([A-Za-z0-9-]{1,80})\/(retry|decline)$/);
      if (m && req.method === 'POST') return m[2] === 'retry' ? this.retry(m[1]!, url.origin) : this.decline(m[1]!, req);
      if (p === '/lab/jobs') return json(this.sql.exec('SELECT id, commitment_id, buyer, state, detail, deliver_by_ms, updated_ms FROM jobs ORDER BY created_ms DESC LIMIT 50').toArray());
      return json({ error: 'not found' }, 404);
    } catch (e) {
      console.log(`internal: ${(e as Error).stack ?? e}`);
      return json({ error: 'internal' }, 500);
    }
  }

  private async validate(req: Request): Promise<Response> {
    const body = (await req.json().catch(() => ({}))) as Json;
    const orderText = typeof body.orderText === 'string' ? body.orderText : null;
    if (!orderText || orderText.length > 16384) return json({ ok: false, errors: ['send {"orderText": "<the order>"}'] }, 400);
    // Public route: at most VALIDATE_PER_MINUTE asks reach the lab per minute.
    const minute = String(Math.floor(Date.now() / 60_000));
    const [m, n] = (this.kvGet('validate_rate') ?? '0:0').split(':');
    const count = m === minute ? Number(n) + 1 : 1;
    this.kvSet('validate_rate', `${minute}:${count}`);
    if (count > VALIDATE_PER_MINUTE) return json({ ok: false, errors: ['too many validations; try in a minute'] }, 429, { 'retry-after': '60' });
    const v = await this.askLab({ type: 'validate', order_text: orderText });
    return json(v, v.ok ? 200 : 422);
  }

  private async quote(req: Request): Promise<Response> {
    const body = (await req.json().catch(() => ({}))) as Json;
    const { order, error } = await checkScope(body.scope, this.env.ROBOT_ID);
    if (error) return json({ error }, 400);
    // The seller adapter does not pass the buyer to quote: the limit is global (the adapter has its own too).
    const buyer = typeof body.buyer === 'string' ? body.buyer.toLowerCase() : '';
    const n = this.sql.exec('SELECT COUNT(*) AS n FROM quotes WHERE created_ms > ?', Date.now() - 3_600_000).one().n as number;
    if (n >= Number(this.env.QUOTES_PER_HOUR)) return json({ error: 'too many quotes; try later' }, 429, { 'retry-after': '600' });
    const st = this.status();
    if (!st.accepting) return json({ error: 'robot not available', reason: st.reason || 'robot not ready' }, 503);
    if (st.queue >= Number(this.env.MAX_QUEUE)) return json({ error: 'queue full' }, 503, { 'retry-after': '1800' });
    const v = await this.askLab({ type: 'validate', order_text: body.scope.orderText });
    if (!v.ok) return json({ error: 'order refused', errors: v.errors }, 422);
    const pricing = this.pricing();
    const price = priceFor(pricing, body.scope.task, order);
    if (price.error) return json({ error: price.error }, 503);
    const deliverWithinSec = Math.max(7201, Number(pricing.deliver_within_sec ?? 259200));
    const validForSec = Number(this.env.QUOTE_VALID_SEC);
    const id = `q-${crypto.randomUUID()}`;
    const now = Date.now();
    this.sql.exec('INSERT INTO quotes VALUES (?, ?, ?, ?, ?, ?, ?)', id, buyer, JSON.stringify(body.scope), price.amount!,
      deliverWithinSec, now, now + validForSec * 1000);
    return json({ price: { token: pricing.token, amount: price.amount }, quoteId: id, deliverWithinSec, validForSec,
                  estimate: v.estimate });
  }

  private async start(req: Request, origin: string): Promise<Response> {
    const body = (await req.json().catch(() => ({}))) as Json;
    const cid = String(body.commitmentId ?? '');
    if (!/^0x[0-9a-f]{64}$/.test(cid)) return json({ error: 'commitmentId must be 0x and 64 lowercase hex' }, 400);
    const idem = req.headers.get('idempotency-key');
    if (idem && idem !== cid) return json({ error: 'Idempotency-Key must be the commitment ID' }, 400);
    const existing = this.sql.exec('SELECT id FROM jobs WHERE commitment_id = ?', cid).toArray()[0];
    if (existing) return json({ jobId: existing.id });                      // same key, same job (after a restart)
    const q = this.sql.exec('SELECT * FROM quotes WHERE id = ?', String(body.quoteId ?? '')).toArray()[0] as Json | undefined;
    if (!q) return json({ error: 'unknown quoteId' }, 404);
    const qs = JSON.parse(q.scope), s = body.scope ?? {};
    if (['task', 'nonce', 'orderSha256', 'orderText'].some((k) => qs[k] !== s[k])) return json({ error: 'scope differs from the quote' }, 409);
    const c = await this.commitment(cid);
    if (!c.ok) return json({ error: 'the commitment is not Held', detail: c.why }, 409);
    if (c.description !== undefined && !c.description.includes(qs.orderSha256)) return json({ error: 'the commitment does not name the quoted order' }, 409);
    const buyer = (c.buyer ?? q.buyer ?? '').toLowerCase();
    const deliverBy = c.deliverByMs ?? Number(q.created_ms) + Number(q.deliver_within_sec) * 1000;
    const id = `job-${cid.slice(2, 26)}`;
    const now = Date.now();
    this.sql.exec('INSERT INTO jobs (id, commitment_id, quote_id, buyer, scope, state, detail, created_ms, updated_ms, deliver_by_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      id, cid, q.id, buyer, q.scope, 'started', '', now, now, deliverBy);
    this.sql.exec('INSERT INTO events (job_id, ts, state, detail) VALUES (?, ?, ?, ?)', id, now, 'started', cid);
    const j = this.job(id)!;
    this.agent()?.send(JSON.stringify(this.jobMessage(j, origin)));
    this.notify(`job ${id} funded: approval needed`, `A Crow deal is funded and waits for the operator.\nJob ${id}\nCommitment ${cid}\nDeliver before ${new Date(Number(j.deliver_by_ms)).toISOString()}`);
    return json({ jobId: id });
  }

  private async result(id: string): Promise<Response> {
    const j = this.job(id);
    if (!j) return json({ error: 'unknown job' }, 404);
    if (j.state !== 'packaged' && j.state !== 'delivered') return json({ state: j.state, detail: j.detail }, 202);
    const data = this.getFile(`e/${id}.json`);
    if (!data) return json({ state: j.state, detail: 'episode missing' }, 202);
    if (j.state === 'packaged') this.setState(id, 'delivered', 'fetched by the seller adapter');
    return new Response(data, { headers: { 'content-type': 'application/json' } });
  }

  private async putTelemetry(id: string, req: Request): Promise<Response> {
    const j = this.job(id);
    if (!j || !['running', 'claimed', 'awaiting_approval'].includes(j.state)) return json({ error: 'no running job with this id' }, 409);
    const data = await req.arrayBuffer();
    const digest = await crypto.subtle.digest('SHA-256', data);
    const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
    if (data.byteLength > 100_000_000) return json({ error: 'telemetry over 100 MB' }, 413);
    this.putFile(`t/${id}.enc`, data);
    this.sql.exec('UPDATE jobs SET telemetry_sha256 = ?, telemetry_bytes = ? WHERE id = ?', hex, data.byteLength, id);
    return json({ ok: true, sha256: hex, bytes: data.byteLength });
  }

  private async putEpisode(id: string, req: Request, origin: string): Promise<Response> {
    const j = this.job(id);
    if (!j || j.state !== 'running') return json({ error: 'no running job with this id' }, 409);
    const text = await req.text();
    if (text.length > MAX_EPISODE_BYTES) return json({ error: `episode over ${MAX_EPISODE_BYTES} bytes` }, 413);
    let doc: Json;
    try { doc = JSON.parse(text); } catch { return json({ error: 'episode is not JSON' }, 400); }
    const scope = JSON.parse(j.scope);
    const ft = doc.full_telemetry ?? {};
    const problems = [];
    if (doc.format !== 'roaas-episode/1') problems.push('format');
    if (doc.order_text !== scope.orderText) problems.push('order_text is not the order of this deal');
    if (doc.manifest?.deal?.commitment_id !== j.commitment_id) problems.push('manifest.deal.commitment_id');
    if (ft.url !== `${this.env.PUBLIC_BASE || origin}/t/${id}.enc`) problems.push('full_telemetry.url');
    if (!j.telemetry_sha256 || ft.ciphertext_sha256 !== j.telemetry_sha256) problems.push('full telemetry not uploaded, or its hash differs');
    if (problems.length) return json({ error: 'episode refused', problems }, 422);
    this.putFile(`e/${id}.json`, new TextEncoder().encode(text).buffer as ArrayBuffer);
    this.setState(id, 'packaged', `${text.length} bytes`);
    return json({ ok: true });
  }

  // ------------------------------------------------------------------ operator commands (roaas ops ...)
  private operator(action: string): Response {
    if (action === 'open' || action === 'close') this.kvSet('on_duty', action === 'open' ? '1' : '0');
    else this.kvSet('paused', action === 'pause' ? '1' : '0');
    return json(this.status());
  }

  private retry(id: string, origin: string): Response {
    const j = this.job(id);
    if (!j) return json({ error: 'unknown job' }, 404);
    if (!['failed', 'declined', 'awaiting_approval', 'claimed', 'started'].includes(j.state)) return json({ error: `job is ${j.state}` }, 409);
    if (Number(j.deliver_by_ms) < Date.now() + 600_000) return json({ error: 'too close to the delivery deadline' }, 409);
    this.setState(id, 'started', 'retry requested by the operator');
    const ws = this.agent();
    ws?.send(JSON.stringify({ ...this.jobMessage(this.job(id)!, this.kvGet('origin') ?? origin), retry: true }));
    return json({ ok: true, pushed: ws !== undefined });
  }

  private async decline(id: string, req: Request): Promise<Response> {
    const j = this.job(id);
    if (!j) return json({ error: 'unknown job' }, 404);
    if (['packaged', 'delivered'].includes(j.state)) return json({ error: `job is ${j.state}` }, 409);
    const body = (await req.json().catch(() => ({}))) as Json;
    const why = String(body.reason ?? 'declined by the operator').slice(0, 300);
    this.setState(id, 'declined', why);
    this.notify(`job ${id} declined`, `${why}\nNothing will be delivered: the buyer can reclaim the payment after deliverBy (${new Date(Number(j.deliver_by_ms)).toISOString()}).`);
    return json({ ok: true });
  }

  // ------------------------------------------------------------------ the lab agent's WebSocket
  private acceptAgent(req: Request, origin: string): Response {
    if (req.headers.get('upgrade') !== 'websocket') return json({ error: 'WebSocket upgrade expected' }, 426);
    for (const old of this.ctx.getWebSockets('agent')) old.close(4000, 'replaced by a new connection');
    const pair = new WebSocketPair();
    this.ctx.acceptWebSocket(pair[1], ['agent']);
    this.kvSet('origin', origin);
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer) {
    let m: Json;
    try { m = JSON.parse(typeof message === 'string' ? message : new TextDecoder().decode(message)); } catch { return; }
    if (m.type === 'hello' || m.type === 'status') {
      this.kvSet('status', JSON.stringify(m.status ?? {}));
      this.kvSet('status_ms', String(Date.now()));
      if (m.type === 'hello') {
        this.kvSet('on_duty', m.status?.on_duty === false ? '0' : '1');
        const current = typeof m.status?.job_id === 'string' ? m.status.job_id : null;
        for (const r of this.sql.exec(`SELECT id FROM jobs WHERE state = 'running'`).toArray()) {
          if (r.id === current) continue;                // the same agent, reconnected mid-run: the job goes on
          this.setState(r.id as string, 'failed', INTERRUPTED);
          this.notify(`job ${r.id} interrupted`, INTERRUPTED);
        }
        this.pushPending(this.kvGet('origin') ?? '');
      }
    } else if (m.type === 'validate_result' && typeof m.id === 'string') {
      this.pending.get(m.id)?.(m);
      this.pending.delete(m.id);
    } else if (m.type === 'job_event' && typeof m.job_id === 'string' && LAB_STATES.has(m.state)) {
      const j = this.job(m.job_id);
      if (!j || j.state === 'packaged' || j.state === 'delivered') return;
      this.setState(m.job_id, m.state, String(m.detail ?? '').slice(0, 500));
      if (m.state === 'failed' || m.state === 'declined') this.notify(`job ${m.job_id} ${m.state}`, String(m.detail ?? ''));
    }
  }

  async webSocketClose(ws: WebSocket, code: number) {
    ws.close(code, 'closing');
    if (this.kvGet('on_duty') === '1' && this.ctx.getWebSockets('agent').length === 0 && code !== 4000) {
      this.notify('lab agent disconnected', `The lab agent disconnected (code ${code}) while the operator is on duty: no quotes are accepted until it reconnects.`);
    }
  }
}
