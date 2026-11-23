import { createServer } from 'node:http';
import handler from './handler.mjs';
import { createChatGPTAccounts } from './chatgpt-accounts.mjs';

const host = '127.0.0.1';
const port = Number(process.env.WORKFLOW_LAB_PORT || 8080);
const origin = 'http://' + host + ':' + port;

let accounts;
const server = createServer(async (incoming, outgoing) => {
  if (incoming.headers.host !== host + ':' + port || !incoming.url?.startsWith('/')) {
    outgoing.writeHead(403).end();
    return;
  }
  try {
    const url = new URL(incoming.url, origin);
    if (url.origin !== origin) {
      outgoing.writeHead(403).end();
      return;
    }
    if (url.pathname === '/workflow-lab') {
      outgoing.writeHead(302, { Location: '/workflow-lab/' }).end();
      return;
    }
    if (url.pathname.startsWith('/workflow-lab/')) url.pathname = url.pathname.slice('/workflow-lab'.length);
    const headers = new Headers({ 'oai-authenticated-user-id': 'local-owner' });
    if (incoming.headers.cookie && url.pathname === '/auth/callback') headers.set('Cookie', incoming.headers.cookie);
    if (incoming.headers.origin) headers.set('Origin', incoming.headers.origin);
    const pastedKey = incoming.headers['x-ai-office-key'];
    if (typeof pastedKey === 'string') headers.set('X-AI-Office-Key', pastedKey);
    const cancelled = new AbortController();
    outgoing.on('close', () => { if (!outgoing.writableFinished) cancelled.abort(); });
    const options = { method: incoming.method, headers, signal: cancelled.signal };
    if (incoming.method === 'POST') {
      const bodyLimit = url.pathname === '/api/ai/project-assist' ? 256 * 1024 : 16384;
      const chunks = [];
      let length = 0;
      for await (const chunk of incoming) {
        length += chunk.length;
        if (length > bodyLimit) {
          outgoing.writeHead(413).end();
          return;
        }
        chunks.push(chunk);
      }
      options.body = Buffer.concat(chunks);
    }
    const request = new Request(url, options);
    const response = url.pathname.startsWith('/api/local/chatgpt/') || url.pathname === '/auth/callback'
      ? (accounts ? await accounts.route(request, url) : new Response(null, { status: 503 }))
      : await handler.fetch(request, process.env, accounts);
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    outgoing.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    outgoing.writeHead(500).end();
  }
});
server.listen(port, host, () => {
  try { accounts = createChatGPTAccounts(origin); }
  catch { console.error('Không mở được dữ liệu đăng nhập ChatGPT. Các chức năng khác vẫn dùng được.'); }
  console.log(origin + '/workflow-lab/');
});
