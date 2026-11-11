import { runInNewContext } from 'node:vm';
import { readFile, readdir } from 'node:fs/promises';

const publicDir = new URL('../public/workflow-lab/', import.meta.url);
const types = { html: 'text/html; charset=utf-8', js: 'text/javascript; charset=utf-8' };
export const assets = Object.fromEntries(await Promise.all(
  (await readdir(publicDir)).map(async (name) => {
    const type = types[name.split('.').at(-1)];
    if (!type) throw new Error('Unsupported Workflow Lab asset: ' + name);
    return ['/' + name, { type, content: await readFile(new URL(name, publicDir), 'utf8') }];
  })
));

// Evaluate only trusted, repository-owned configuration files.
const config = { window: {} };
for (const file of ['squads.js', 'policies.js']) runInNewContext(assets['/' + file].content, config);
export const officeConfig = { officeSquads: config.window.officeSquads, officePolicies: config.window.officePolicies };
