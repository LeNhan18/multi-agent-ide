import { createServer } from 'node:http';
import handler from './handler.mjs';

const host = '127.0.0.1';
const port = Number(process.env.WORKFLOW_LAB_PORT || 8080);
const origin = 'http://' + host + ':' + port;

createServer(async (incoming, outgoing) => {
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
    if (incoming.headers.origin) headers.set('Origin', incoming.headers.origin);
    const pastedKey = incoming.headers['x-ai-office-key'];
    if (typeof pastedKey === 'string') headers.set('X-AI-Office-Key', pastedKey);
    const options = { method: incoming.method, headers };
    if (incoming.method === 'POST') {
      const chunks = [];
      let length = 0;
      for await (const chunk of incoming) {
        length += chunk.length;
        if (length > 16384) {
          outgoing.writeHead(413).end();
          return;
        }
        chunks.push(chunk);
      }
      options.body = Buffer.concat(chunks);
    }
    const response = await handler.fetch(new Request(url, options), process.env);
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    outgoing.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    outgoing.writeHead(500).end();
  }
}).listen(port, host, () => console.log(origin + '/workflow-lab/'));
