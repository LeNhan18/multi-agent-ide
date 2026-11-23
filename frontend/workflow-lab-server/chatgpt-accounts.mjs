import { createHash, createPublicKey, randomBytes, randomUUID, verify } from 'node:crypto';
import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const issuer = 'https://auth.openai.com';
const resource = 'https://api.openai.com/v1';
const dynamicClient = 'dynamic_agent_client';
const permission = 'chatgpt.tokens.use.direct';
const scopes = 'openid profile email offline_access resource.invoke ' + permission;
const opaque = () => randomBytes(32).toString('base64url');
const cookieName = state => 'office_oauth_' + state;
const clientPattern = /^oaiapp_[a-zA-Z0-9_-]{1,180}$/;
const json = (data, status = 200) => Response.json(data, {
  status, headers: { 'Cache-Control': 'no-store', 'Cross-Origin-Resource-Policy': 'same-origin' },
});

async function authFetch(fetcher, url, options = {}) {
  const response = await fetcher(url, { ...options, redirect: 'error', signal: AbortSignal.timeout(10000) });
  if (!response.ok) {
    await response.body?.cancel();
    throw new Error('ChatGPT authentication request failed');
  }
  return response;
}

async function identity(fetcher, jwt, client, nonce) {
  const parts = typeof jwt === 'string' ? jwt.split('.') : [];
  if (parts.length !== 3 || jwt.length > 32000) throw new Error('Invalid identity token');
  const decode = part => JSON.parse(Buffer.from(part, 'base64url').toString('utf8'));
  const header = decode(parts[0]);
  if (header.alg !== 'RS256' || typeof header.kid !== 'string') throw new Error('Unsupported identity signature');
  const discovery = await (await authFetch(fetcher, issuer + '/.well-known/openid-configuration')).json();
  if (discovery.issuer !== issuer || new URL(discovery.jwks_uri).origin !== issuer) throw new Error('Invalid issuer');
  const { keys } = await (await authFetch(fetcher, discovery.jwks_uri)).json();
  const jwk = keys.find(key => key.kid === header.kid && key.kty === 'RSA' && (!key.alg || key.alg === 'RS256'));
  if (!jwk || !verify('RSA-SHA256', Buffer.from(parts[0] + '.' + parts[1]),
    createPublicKey({ key: jwk, format: 'jwk' }), Buffer.from(parts[2], 'base64url'))) throw new Error('Invalid signature');
  const claims = decode(parts[1]);
  const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (claims.iss !== issuer || !audiences.includes(client) ||
      ((audiences.length > 1 || claims.azp !== undefined) && claims.azp !== client) ||
      !Number.isFinite(claims.exp) || claims.exp <= Date.now() / 1000 ||
      claims.nonce !== nonce || typeof claims.sub !== 'string' || !claims.sub) throw new Error('Invalid identity claims');
  return { subject: claims.sub, email: typeof claims.email === 'string' ? claims.email.slice(0, 200) : '' };
}

async function completedOutput(response) {
  let buffer = '';
  let size = 0;
  for await (const chunk of response.body.pipeThrough(new TextDecoderStream())) {
    size += chunk.length;
    if (size > 2_000_000) break;
    buffer += chunk;
    let end;
    while ((end = buffer.search(/\r?\n\r?\n/)) >= 0) {
      const frame = buffer.slice(0, end);
      buffer = buffer.slice(end).replace(/^\r?\n\r?\n/, '');
      const data = frame.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
      if (!data || data === '[DONE]') continue;
      const event = JSON.parse(data);
      if (event.type === 'response.completed') {
        return { raw: event.response?.output?.flatMap(item => item.content ?? [])
          .filter(item => item.type === 'output_text').map(item => item.text).join('') };
      }
      if (event.type === 'response.failed' || event.type === 'error') {
        const code = event.response?.error?.code ?? event.code;
        return { error: code?.startsWith('subscription_sharing_usage_') ? 'limited' : 'unavailable' };
      }
    }
  }
  return { error: 'unavailable' };
}

export function createChatGPTAccounts(origin, {
  directory = join(process.env.LOCALAPPDATA || process.env.XDG_CONFIG_HOME || join(homedir(), '.config'),
    'ai-office', 'chatgpt-' + new URL(origin).port),
  fetcher = fetch,
} = {}) {
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const file = join(directory, 'accounts.json');
  let records;
  try { records = JSON.parse(readFileSync(file, 'utf8')); }
  catch (error) {
    if (error.code !== 'ENOENT') throw error;
    records = { host: 'urn:uuid:' + randomUUID(), accounts: [] };
  }
  const save = next => {
    const temporary = join(directory, opaque() + '.tmp');
    try {
      writeFileSync(temporary, JSON.stringify(next), { mode: 0o600, flag: 'wx' });
      renameSync(temporary, file);
      records = next;
    } finally { rmSync(temporary, { force: true }); }
  };
  if (!Array.isArray(records.accounts) || typeof records.host !== 'string') throw new Error('Invalid ChatGPT accounts file');
  save(records);
  const get = id => records.accounts.find(account => account.id === id);
  const put = account => {
    const current = get(account.id);
    save({ ...records, accounts: current ? records.accounts.map(row => row === current ? account : row) : [...records.accounts, account] });
  };
  const publicAccounts = () => records.accounts.map(({ id, email, access }) => ({ id, email, connected: Boolean(access) }));
  const pending = new Map();
  const refreshes = new Map();
  const running = new Map();
  const modelCache = new Map();
  const redirect = origin + '/auth/callback';

  async function grant(params) {
    return (await authFetch(fetcher, issuer + '/api/accounts/oauth/token', {
      method: 'POST', body: new URLSearchParams({ ...params, resource }),
    })).json();
  }

  function credentials(token, previous) {
    const granted = typeof token.scope === 'string' ? token.scope.split(/\s+/) : previous?.scopes ?? [];
    if (!granted.includes(permission) || !granted.includes('resource.invoke') || typeof token.access_token !== 'string' ||
        !token.access_token || String(token.token_type).toLowerCase() !== 'bearer' ||
        !Number.isFinite(token.expires_in) || token.expires_in <= 0) throw new Error('ChatGPT plan unavailable');
    return { access: token.access_token, refresh: token.refresh_token ?? previous?.refresh,
      expiry: Date.now() + token.expires_in * 1000, scopes: granted };
  }

  function start(id) {
    const account = id ? get(id) : null;
    if (id && !account) return json({ status: 'missing' }, 404);
    for (const [state, attempt] of pending) if (Date.now() >= attempt.expiry) pending.delete(state);
    if (pending.size >= 12) return json({ status: 'limited' }, 429);
    const state = opaque(), nonce = opaque(), verifier = opaque(), binder = opaque();
    pending.set(state, { nonce, verifier, account, binder, expiry: Date.now() + 300000 });
    const params = new URLSearchParams({ client_id: account?.client ?? dynamicClient, ext_agent_host_id: records.host,
      response_type: 'code', redirect_uri: redirect, scope: scopes, resource, state, nonce,
      code_challenge_method: 'S256', code_challenge: createHash('sha256').update(verifier).digest('base64url') });
    if (!account) params.set('agent_name_hint', 'AI Office');
    if (account?.email) params.set('login_hint', account.email);
    if (account?.identityToken) params.set('id_token_hint', account.identityToken);
    return new Response(null, { status: 303, headers: {
      Location: issuer + '/api/accounts/authorize?' + params,
      'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store',
      'Set-Cookie': cookieName(state) + '=' + binder + '; Path=/auth/callback; Max-Age=300; HttpOnly; SameSite=Lax',
    } });
  }

  async function callback(request, params) {
    const state = params.get('state');
    const attempt = pending.get(state);
    const cookie = cookieName(state) + '=' + attempt?.binder;
    if (!attempt || Date.now() >= attempt.expiry ||
        !request.headers.get('Cookie')?.split(/;\s*/).includes(cookie)) return json({ status: 'invalid' }, 400);
    pending.delete(state);
    let outcome = 'failed';
    try {
      const client = attempt.account?.client ?? params.get('client_id');
      if (params.has('error') || !clientPattern.test(client) || !params.get('code') ||
          (params.has('client_id') && params.get('client_id') !== client)) throw new Error('Incomplete sign-in');
      const token = await grant({ grant_type: 'authorization_code', client_id: client, code: params.get('code'),
        code_verifier: attempt.verifier, redirect_uri: redirect });
      const profile = await identity(fetcher, token.id_token, client, attempt.nonce);
      const existing = records.accounts.find(account => account.client === client);
      if ((existing && existing.subject !== profile.subject) ||
          (attempt.account && existing !== attempt.account)) throw new Error('Account changed');
      put({ ...profile, ...credentials(token), id: existing?.id ?? randomUUID(), client, identityToken: token.id_token });
      modelCache.delete(existing?.id);
      outcome = 'connected';
    } catch { /* Use a fixed error string, excluding code or tokens. */ }
    return new Response(null, { status: 303, headers: {
      Location: '/workflow-lab/?chatgpt=' + outcome + '#accounts', 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
      'Set-Cookie': cookieName(state) + '=; Path=/auth/callback; Max-Age=0; HttpOnly; SameSite=Lax',
    } });
  }

  async function getCredentials(id) {
    const account = get(id);
    if (!account?.access) return null;
    if (account.expiry > Date.now() + 60000) return account;
    if (!account.refresh) return null;
    if (refreshes.has(id)) return refreshes.get(id);
    const renewal = (async () => {
      const token = await grant({ grant_type: 'refresh_token', client_id: account.client, refresh_token: account.refresh });
      if (get(id) !== account) return null;
      const updated = { ...account, ...credentials(token, account) };
      put(updated);
      return updated;
    })();
    refreshes.set(id, renewal);
    try { return await renewal; }
    finally { refreshes.delete(id); }
  }

  async function signOut(id) {
    const account = get(id);
    if (!account) return json({ status: 'missing' }, 404);
    // Block new requests and discard the local session even if revocation is unavailable.
    put({ id, email: account.email, subject: account.subject, client: account.client });
    running.get(id)?.abort();
    modelCache.delete(id);
    let revoked = true;
    if (account.refresh) {
      try {
        const info = await (await authFetch(fetcher, issuer + '/.well-known/openid-configuration')).json();
        if (info.issuer !== issuer || new URL(info.revocation_endpoint).origin !== issuer) throw new Error('Invalid issuer');
        await authFetch(fetcher, info.revocation_endpoint, {
          method: 'POST', body: new URLSearchParams({ token: account.refresh, token_type_hint: 'refresh_token', client_id: account.client }),
        });
      } catch { revoked = false; }
    }
    return json({ revoked });
  }

  return {
    async route(request, url) {
      if (url.pathname === '/auth/callback' && request.method === 'GET') return callback(request, url.searchParams);
      if (url.pathname === '/api/local/chatgpt/accounts' && request.method === 'GET') return json({ accounts: publicAccounts() });
      if (request.method !== 'POST' || request.headers.get('Origin') !== origin) return json({ status: 'invalid' }, 403);
      if (url.pathname === '/api/local/chatgpt/start') return start(new URLSearchParams(await request.text()).get('account'));
      if (url.pathname === '/api/local/chatgpt/sign-out') return signOut(new URLSearchParams(await request.text()).get('account'));
      return json({ status: 'missing' }, 404);
    },
    async infer(id, input, preferredModel, signal) {
      let controller = running.get(id);
      if (!controller || controller.signal.aborted) {
        controller = new AbortController();
        running.set(id, controller);
      }
      const combined = AbortSignal.any([signal, controller.signal]);
      try {
        const account = await getCredentials(id);
        if (!account) return { error: 'account_missing' };
        combined.throwIfAborted();
        const headers = { Authorization: 'Bearer ' + account.access, 'Content-Type': 'application/json' };
        let catalog = modelCache.get(id);
        if (!catalog || catalog.expiry < Date.now()) {
          const response = await fetcher(resource + '/models', { headers, signal: combined, redirect: 'error' });
          if (!response.ok) {
            await response.body?.cancel();
            return { error: response.status === 429 ? 'limited' : response.status === 401 || response.status === 403 ? 'account_missing' : 'unavailable' };
          }
          const { models } = await response.json();
          catalog = { models: models.filter(item => item.visibility === 'list' && typeof item.slug === 'string').map(item => item.slug),
            expiry: Date.now() + 300000 };
          combined.throwIfAborted();
          modelCache.set(id, catalog);
        }
        const model = catalog.models.includes(preferredModel) ? preferredModel :
          catalog.models.find(slug => /(?:mini|luna)/i.test(slug)) ?? catalog.models[0];
        if (!model) return { error: 'model_unavailable' };
        combined.throwIfAborted();
        const response = await fetcher(resource + '/responses', { method: 'POST', headers, signal: combined, redirect: 'error',
          body: JSON.stringify({ ...input, model, store: false, stream: true }) });
        if (!response.ok) {
          await response.body?.cancel();
          return { error: response.status === 429 ? 'limited' : response.status === 401 || response.status === 403 ? 'account_missing' :
            response.status === 400 || response.status === 404 ? 'model_unavailable' : 'unavailable' };
        }
        const result = await completedOutput(response);
        combined.throwIfAborted();
        return { ...result, model, raw: result.raw?.split(account.access).join('[đã ẩn]') };
      } catch (error) {
        return { error: error.name === 'TimeoutError' ? 'timeout' : combined.aborted ? 'cancelled' : 'unavailable' };
      }
    },
  };
}
