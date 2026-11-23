import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync, sign } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createChatGPTAccounts } from '../chatgpt-accounts.mjs';
import handler from '../handler.mjs';

const directory = mkdtempSync(join(tmpdir(), 'ai-office-accounts-'));
const origin = 'http://127.0.0.1:8080';
const issuer = 'https://auth.openai.com';
const resource = 'https://api.openai.com/v1';
const scopes = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct';
const { publicKey, privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: 'jwk' }), kid: 'fixture', alg: 'RS256' };
const accountsByCode = new Map();
const inferences = [];
const revocations = [];
const refreshes = [];
let failAccount;
let wrongNonce;
const base64 = value => Buffer.from(JSON.stringify(value)).toString('base64url');
const idToken = ({ client, nonce, email }) => {
  const token = base64({ alg: 'RS256', kid: jwk.kid }) + '.' +
    base64({ iss: issuer, aud: client, exp: Math.round(Date.now() / 1000) + 3600, nonce: wrongNonce ?? nonce, sub: email, email });
  return token + '.' + sign('RSA-SHA256', Buffer.from(token), privateKey).toString('base64url');
};

async function fetcher(url, options) {
  if (url === issuer + '/.well-known/openid-configuration') return Response.json({
    issuer, jwks_uri: issuer + '/jwks', revocation_endpoint: issuer + '/revoke',
  });
  if (url === issuer + '/jwks') return Response.json({ keys: [jwk] });
  if (url === issuer + '/revoke') {
    const token = options.body.get('token');
    revocations.push(token);
    return new Response(null);
  }
  if (url === issuer + '/api/accounts/oauth/token') {
    assert.equal(options.body.get('resource'), resource);
    if (options.body.get('grant_type') === 'refresh_token') {
      const token = options.body.get('refresh_token');
      refreshes.push(token);
      assert.equal(options.body.get('client_id'), 'oaiapp_A');
      return Response.json({ access_token: 'access-A-renewed', refresh_token: 'refresh-A-renewed', token_type: 'Bearer', expires_in: 3600 });
    }
    const entry = accountsByCode.get(options.body.get('code'));
    assert.equal(options.body.get('client_id'), entry.client);
    assert.equal(options.body.get('redirect_uri'), origin + '/auth/callback');
    assert.equal(createHash('sha256').update(options.body.get('code_verifier')).digest('base64url'), entry.challenge);
    return Response.json({ access_token: 'access-' + entry.id, refresh_token: 'refresh-' + entry.id,
      token_type: 'Bearer', expires_in: entry.id === 'A' ? 30 : 3600, scope: scopes, id_token: idToken(entry) });
  }
  if (url === resource + '/models') return Response.json({ models: [{ visibility: 'list', slug: 'gpt-test-mini' }] });
  assert.equal(url, resource + '/responses');
  const body = JSON.parse(options.body);
  const access = options.headers.Authorization;
  assert.ok(!Object.hasOwn(body, 'max_output_tokens'));
  assert.equal(body.input[0].role, 'user');
  inferences.push([access, body.input[0].content]);
  assert.equal(body.model, 'gpt-test-mini');
  assert.equal(body.store, false);
  assert.equal(body.stream, true);
  if (access === failAccount) return new Response('', { status: 429 });
  const plan = { title: 'Việc cần làm', criteria: 'Kiểm tra', risk: 'Thiếu dữ liệu', next: 'Gom tài liệu', priority: 'Vừa', hours: 2 };
  const event = { type: 'response.completed', response: { output: [{ content: [{ type: 'output_text', text: JSON.stringify(plan) }] }] } };
  return new Response('data: ' + JSON.stringify(event) + '\n\n', { headers: { 'Content-Type': 'text/event-stream' } });
}

let service = createChatGPTAccounts(origin, { directory, fetcher });
const route = (path, init) => {
  const url = new URL(path, origin);
  return service.route(new Request(url, init), url);
};
const post = (path, params = {}, from = origin) => route('/api/local/chatgpt/' + path, {
  method: 'POST', headers: { Origin: from }, body: new URLSearchParams(params),
});
async function connect(id, expected = 'connected') {
  const start = await post('start');
  assert.equal(start.status, 303);
  const cookie = start.headers.get('Set-Cookie').split(';')[0];
  const params = new URL(start.headers.get('Location')).searchParams;
  const client = 'oaiapp_' + id;
  const code = 'code-' + id;
  const state = params.get('state');
  accountsByCode.set(code, { id, client, nonce: params.get('nonce'), challenge: params.get('code_challenge'), email: 'user@example.com' });
  assert.equal(params.get('client_id'), 'dynamic_agent_client');
  const path = '/auth/callback?' + new URLSearchParams({ state, client_id: client, code });
  assert.equal((await route(path)).status, 400);
  const result = await route(path, { headers: { Cookie: cookie } });
  assert.equal(result.status, 303);
  assert.match(result.headers.get('Location'), new RegExp('chatgpt=' + expected + '#accounts$'));
  assert.equal((await route(path, { headers: { Cookie: cookie } })).status, 400);
}

const plan = (id, prompt) => handler.fetch(new Request(origin + '/api/ai/task-plan', {
  method: 'POST', headers: { Origin: origin, 'oai-authenticated-user-id': 'local-owner' },
  body: JSON.stringify({ accountId: id, prompt }),
}), {}, service);

try {
  assert.equal((await post('start', {}, 'https://foreign.test')).status, 403);
  await connect('A');
  await connect('B');
  wrongNonce = 'mismatch';
  await connect('C', 'failed');
  wrongNonce = undefined;
  service = createChatGPTAccounts(origin, { directory, fetcher });
  const result = await (await route('/api/local/chatgpt/accounts')).json();
  assert.equal(result.accounts.length, 2);
  assert.notEqual(result.accounts[0].id, result.accounts[1].id);
  assert.ok(!JSON.stringify(result).includes('access-'));
  if (process.platform !== 'win32') assert.equal(statSync(join(directory, 'accounts.json')).mode & 0o777, 0o600);
  const [a, b] = result.accounts.map(account => account.id);
  const [one, two, three] = await Promise.all([plan(a, 'Việc A'), plan(b, 'Việc B'), plan(a, 'Việc A2')]);
  for (const response of [one, two, three]) {
    assert.equal(response.status, 200);
    const data = await response.json();
    assert.equal(data.provenance.model, 'gpt-test-mini');
    assert.ok(!JSON.stringify(data).includes('access-'));
  }
  assert.deepEqual(refreshes, ['refresh-A']);
  assert.deepEqual(new Map(inferences.map(([token, prompt]) => [prompt, token])), new Map([
    ['Việc A', 'Bearer access-A-renewed'], ['Việc A2', 'Bearer access-A-renewed'], ['Việc B', 'Bearer access-B'],
  ]));
  failAccount = 'Bearer access-B';
  assert.equal((await plan(b, 'Chạm hạn mức')).status, 429);
  assert.equal(inferences.filter(([, prompt]) => prompt === 'Chạm hạn mức').length, 1);
  assert.equal((await (await post('sign-out', { account: a })).json()).revoked, true);
  assert.deepEqual(revocations, ['refresh-A-renewed']);
  assert.ok(!readFileSync(join(directory, 'accounts.json'), 'utf8').includes('access-A'));
  assert.equal((await (await plan(a, 'Đã ngắt')).json()).status, 'account_missing');
  console.log('PASS ChatGPT accounts: OAuth state, browser binding, PKCE, signed identity, account isolation, refresh, usage limit, revocation');
} finally {
  rmSync(directory, { recursive: true, force: true });
}
