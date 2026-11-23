import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHandoffs } from '../handoffs.mjs';

const scratch = await mkdtemp(join(tmpdir(), 'ai-office-handoffs-'));
const root = join(scratch, 'sample project');
const origin = 'http://127.0.0.1:8080';
const options = { directory: join(scratch, 'board data'), defaultRoot: root };
const url = new URL('/api/local/handoffs', origin);
let api = createHandoffs(origin, options);
const git = (cwd, ...args) => execFileSync('git', args, { cwd, stdio: 'pipe' });
const send = (body, from = origin) => api(new Request(url, {
  method: 'POST', headers: { Origin: from }, body: JSON.stringify({ project: root, ...body }),
}), url);
const assign = (scope, agent) => send({ action: 'assign', scope, agent, brief: 'Kiểm tra kết quả' });

try {
  await mkdir(join(root, 'src'), { recursive: true });
  await writeFile(join(root, 'src', 'parser.py'), 'baseline\n');
  await writeFile(join(root, 'main.py'), 'untouched\n');
  git(root, 'init', '-q');
  git(root, 'add', '.');
  git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'base');
  assert.equal((await send({action: 'assign'}, 'https://other.test')).status, 403);
  assert.equal((await assign('../outside', 'Claude')).status, 400);
  assert.equal((await assign('.git/config', 'Claude')).status, 400);
  const first = await assign('src', 'Claude');
  assert.equal(first.status, 200);
  const claude = (await first.json()).tasks[0];
  assert.equal((await assign('SRC/parser.py', 'Gemini')).status, 409);
  const second = await assign('docs', 'Gemini');
  assert.equal(second.status, 200);
  const board = await second.json();
  const gemini = board.tasks[1];
  assert.notEqual(claude.worktree, gemini.worktree);
  assert.equal(String(git(claude.worktree, 'rev-parse', 'HEAD')).trim(), claude.base);
  const posted = await send({action: 'message', taskId: claude.id, to: gemini.id, note: 'Cần checklist cho parser.'});
  assert.equal((await posted.json()).journal.at(-1).to, gemini.id);
  assert.match(await readFile(board.boardPath, 'utf8'), /Cần checklist/);
  assert.equal((await send({action: 'handoff', taskId: claude.id})).status, 400);
  await writeFile(claude.report, 'Đã rà parser. Test cần được chạy trước khi hợp nhất.');
  await writeFile(join(claude.worktree, 'src', 'parser.py'), 'claude draft\n');
  await writeFile(join(claude.worktree, 'main.py'), 'out of scope\n');
  git(claude.worktree, 'add', 'main.py');
  await writeFile(join(claude.worktree, 'main.py'), 'untouched\n');
  const rejected = await send({action: 'handoff', taskId: claude.id});
  assert.equal(rejected.status, 409);
  assert.match((await rejected.json()).error, /main\.py/);
  git(claude.worktree, 'restore', '--staged', 'main.py');
  await writeFile(join(claude.worktree, 'outside.txt'), 'untracked change\n');
  assert.equal((await send({action: 'handoff', taskId: claude.id})).status, 409);
  await rm(join(claude.worktree, 'outside.txt'));
  assert.equal((await send({action: 'finish', taskId: claude.id})).status, 409);
  const handoff = await send({action: 'handoff', taskId: claude.id});
  assert.equal(handoff.status, 200);
  assert.deepEqual((await handoff.json()).tasks[0].changedFiles, ['src/parser.py']);
  assert.equal((await assign('src/parser.py', 'Gemini')).status, 409);
  api = createHandoffs(origin, options);
  assert.equal((await send({action: 'finish', taskId: claude.id})).status, 200);
  assert.equal((await assign('src/parser.py', 'Gemini')).status, 200);
  assert.equal(await readFile(join(root, 'src', 'parser.py'), 'utf8'), 'baseline\n');
  assert.equal(await readFile(join(gemini.worktree, 'src', 'parser.py'), 'utf8'), 'baseline\n');
  assert.equal(String(git(root, 'status', '--porcelain')), '');
  console.log('PASS handoffs: Git worktree isolation, overlapping claims, routed messages, staged and untracked guards, persisted board, scope release');
} finally {
  await rm(scratch, { recursive: true, force: true });
}
