// ROaaS job API (Cloudflare Worker). Routes:
//   public        GET /api/status, GET /api/offers, POST /api/validate {orderText}, GET /t/<job>.enc (encrypted telemetry)
//   Crow adapter  POST /api/crow/quote, POST /api/crow/jobs, GET /api/crow/jobs/<id>/result   (header X-ROaaS-Key)
//   lab agent     GET /api/lab/ws (WebSocket), PUT /api/lab/jobs/<id>/telemetry|episode, GET /api/lab/jobs
//                 (Authorization: Bearer <LAB_TOKEN>)
//   anything else: the static site in public/
import { Lab } from './lab';
import { sameSecret } from './order';
import offers from '../../crow/offers.json';

export { Lab };

export interface Env {
  LAB: DurableObjectNamespace<Lab>;
  ASSETS: Fetcher;
  ADAPTER_KEY?: string;
  LAB_TOKEN?: string;
  OPERATORS: string;
  ROBOT_ID: string;
  MAX_QUEUE: string;
  QUOTES_PER_HOUR: string;
  QUOTE_VALID_SEC: string;
  PRICING_JSON?: string;
  SKIP_HELD_CHECK?: string;
  PUBLIC_BASE?: string;
  RETENTION_DAYS?: string;
  RESEND_API_KEY?: string;
  NOTIFY_TO: string;
  NOTIFY_FROM: string;
}

const deny = (status: number, error: string) =>
  new Response(JSON.stringify({ error }), { status, headers: { 'content-type': 'application/json' } });

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const p = url.pathname;
    const lab = () => env.LAB.get(env.LAB.idFromName(env.ROBOT_ID));
    const forward = (path: string) => lab().fetch(new Request(new URL(path, url.origin), req));

    if (p === '/api/status' && req.method === 'GET') return forward('/status');
    if (p === '/api/offers' && req.method === 'GET') return Response.json(offers);
    if (p === '/api/validate' && req.method === 'POST') return forward('/validate');

    if (/^\/t\/job-[0-9a-f]{24}\.enc$/.test(p) && req.method === 'GET') return forward('/file' + p);

    if (p.startsWith('/api/crow/')) {
      if (!sameSecret(req.headers.get('x-roaas-key'), env.ADAPTER_KEY)) return deny(401, 'unauthorized');
      if (p === '/api/crow/quote' && req.method === 'POST') return forward('/crow/quote');
      if (p === '/api/crow/jobs' && req.method === 'POST') return forward('/crow/jobs');
      if (/^\/api\/crow\/jobs\/[A-Za-z0-9-]{1,80}\/result$/.test(p) && req.method === 'GET') return forward(p.slice(4));
      return deny(404, 'not found');
    }

    if (p.startsWith('/api/lab/')) {
      const auth = req.headers.get('authorization') ?? '';
      if (!sameSecret(auth.startsWith('Bearer ') ? auth.slice(7) : null, env.LAB_TOKEN)) return deny(401, 'unauthorized');
      return forward(p.slice(4));
    }

    return env.ASSETS.fetch(req);
  },
} satisfies ExportedHandler<Env>;
