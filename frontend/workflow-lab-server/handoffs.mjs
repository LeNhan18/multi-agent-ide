import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, mkdir, open, readFile, realpath, rename, rm, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const roles = ['Claude', 'Gemini', 'ChatGPT', 'Agent khác'];
const idPattern = /^[a-f0-9-]{36}$/;
const maxNote = 8000;
const git = async (cwd, args) => (await exec('git', ['-c', 'core.fsmonitor=false', ...args], {
  cwd, timeout: 30000, maxBuffer: 4 * 1024 * 1024, windowsHide: true,
})).stdout;

class BoardError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}
const text = (value, max) => typeof value === 'string' && value.length <= max ? value.trim() : '';
const json = (value, status = 200) => Response.json(value, {
  status, headers: { 'Cache-Control': 'no-store', 'Cross-Origin-Resource-Policy': 'same-origin' },
});
const key = path => path.normalize('NFC').toLowerCase();
const inside = (path, scope) => key(path) === key(scope) || key(path).startsWith(key(scope) + '/');
const within = (root, path) => {
  const suffix = relative(root, path);
  return !suffix || (suffix !== '..' && !suffix.startsWith('..' + (process.platform === 'win32' ? '\\' : '/')) && !isAbsolute(suffix));
};

function parseScope(input) {
  const scope = text(input, 240).replace(/\/$/, '');
  const segments = scope.split('/');
  if (!scope || segments.some(part => !part || part === '.' || part === '..' || key(part) === '.git' ||
      part !== part.trim() || part.endsWith('.') || /[\\:*?"<>|\x00-\x1f]/u.test(part))) {
    throw new BoardError('Nhập đường dẫn tương đối trong dự án, ví dụ docs hoặc src/parser.py.');
  }
  return scope;
}

async function noteFrom(path) {
  if (await lstat(path).then(info => !info.isFile(), error => error.code === 'ENOENT' ? true : Promise.reject(error))) {
    throw new BoardError('Chưa có tệp bàn giao. Hãy lưu kết quả vào đường dẫn trong hướng dẫn giao việc.');
  }
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0));
  try {
    if (!(await handle.stat()).isFile()) throw new BoardError('Tệp bàn giao không hợp lệ.');
    const buffer = Buffer.alloc(maxNote * 4 + 1);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    return text(buffer.subarray(0, bytesRead).toString('utf8'), maxNote);
  } finally { await handle.close(); }
}

export function createHandoffs(origin, {
  directory = join(process.env.LOCALAPPDATA || process.env.XDG_CONFIG_HOME || join(homedir(), '.config'),
    'ai-office', 'handoffs'),
  defaultRoot = process.env.WORKFLOW_LAB_PROJECT_ROOT || process.cwd(),
} = {}) {
  async function project(value) {
    const requested = text(value, 2000);
    if (!isAbsolute(requested)) throw new BoardError('Chọn đường dẫn tuyệt đối tới thư mục dự án Git.');
    let root, repo, common;
    try {
      root = await realpath(requested);
      repo = (await git(root, ['rev-parse', '--show-toplevel'])).trimEnd();
      common = (await git(root, ['rev-parse', '--path-format=absolute', '--git-common-dir'])).trimEnd();
      if (await realpath(repo) !== root) throw Error();
      common = await realpath(common);
    } catch { throw new BoardError('Không mở được dự án Git. Hãy chọn thư mục gốc của repo.'); }
    const home = join(directory, createHash('sha256').update(common).digest('hex').slice(0, 24));
    if (within(root, resolve(home))) throw new BoardError('Thư mục bàn giao cần nằm ngoài dự án Git.');
    return { root, home, file: join(home, 'board.json') };
  }

  async function read(board) {
    try { return JSON.parse(await readFile(board.file, 'utf8')); }
    catch (error) {
      if (error.code !== 'ENOENT') throw error;
      return { tasks: [], journal: [] };
    }
  }

  async function change(board, action) {
    await mkdir(board.home, { recursive: true, mode: 0o700 });
    const lock = join(board.home, '.lock');
    try { await mkdir(lock, { mode: 0o700 }); }
    catch (error) {
      if (error.code === 'EEXIST') throw new BoardError('Bảng đang cập nhật. Hãy thử lại sau vài giây.', 409);
      throw error;
    }
    const temp = join(board.home, randomUUID() + '.tmp');
    try {
      const state = await read(board);
      const rollback = await action(state);
      try {
        await writeFile(temp, JSON.stringify(state, null, 2), { flag: 'wx', mode: 0o600 });
        await rename(temp, board.file);
      } catch (error) {
        await rollback?.();
        throw error;
      }
      return state;
    } finally {
      await rm(temp, { force: true });
      await rm(lock, { recursive: true, force: true });
    }
  }

  function record(state, task, type, note = '', to = null) {
    task.updatedAt = new Date().toISOString();
    state.journal.push({ taskId: task.id, to, type, note, at: task.updatedAt });
  }

  async function assign(board, state, input) {
    const brief = text(input.brief, 1600);
    const scope = parseScope(input.scope);
    if (!brief || !roles.includes(input.agent)) throw new BoardError('Nhập yêu cầu và chọn agent.');
    if (state.tasks.length >= 1000) throw new BoardError('Bảng đã đạt giới hạn số công việc.', 409);
    const overlap = state.tasks.find(task => task.status !== 'done' &&
      (inside(scope, task.scope) || inside(task.scope, scope)));
    if (overlap) throw new BoardError(overlap.agent + ' đang giữ phạm vi ' + overlap.scope + '.', 409);
    const id = randomUUID();
    const space = join(board.home, id);
    const worktree = join(space, 'worktree');
    await mkdir(space, { mode: 0o700 });
    let base;
    try {
      base = (await git(board.root, ['rev-parse', '--verify', 'HEAD'])).trimEnd();
      await git(board.root, ['-c', 'core.hooksPath=' + space, 'worktree', 'add', '--detach', worktree, base]);
    } catch {
      await rm(space, { recursive: true, force: true });
      throw new BoardError('Không tạo được worktree. Kiểm tra Git và commit đầu tiên của dự án.', 409);
    }
    const task = { id, agent: input.agent, brief, scope, base, worktree, report: join(space, 'handoff.md'),
      status: 'working', changedFiles: [], note: '' };
    state.tasks.push(task);
    record(state, task, 'assigned');
    return async () => {
      await git(board.root, ['worktree', 'remove', '--force', worktree]);
      await rm(space, { recursive: true, force: true });
    };
  }

  async function inspect(task) {
    const output = await Promise.all([
      git(task.worktree, ['diff', '--name-only', '--no-renames', '--ignore-submodules=none', '-z', task.base, '--']),
      git(task.worktree, ['diff', '--cached', '--name-only', '--no-renames', '--ignore-submodules=none', '-z', task.base, '--']),
      git(task.worktree, ['diff', '--name-only', '--no-renames', '--ignore-submodules=none', '-z', task.base, 'HEAD', '--']),
      git(task.worktree, ['ls-files', '--others', '--exclude-standard', '-z']),
    ]);
    const paths = [...new Set(output.flatMap(list => list.split('\0').filter(Boolean)))].sort();
    const outside = paths.filter(path => process.platform === 'win32'
      ? !inside(path, task.scope)
      : path !== task.scope && !path.startsWith(task.scope + '/'));
    if (outside.length) throw new BoardError('Có thay đổi ngoài phạm vi đã giao: ' + outside.slice(0, 5).join(', '), 409);
    return paths;
  }

  async function update(state, input) {
    const task = typeof input.taskId === 'string' && idPattern.test(input.taskId) &&
      state.tasks.find(row => row.id === input.taskId);
    if (!task || task.status === 'done') throw new BoardError('Công việc đã đóng hoặc không còn trên bảng.', 404);
    if (input.action === 'message') {
      const note = text(input.note, 1200);
      const target = state.tasks.find(row => row.id === input.to && row.id !== task.id && row.status !== 'done');
      if (!note || (input.to && !target)) throw new BoardError('Nhập tin nhắn và chọn người nhận hiện có.');
      if (state.journal.length >= 5000) throw new BoardError('Nhật ký đã đầy.', 409);
      record(state, task, 'message', note, target?.id);
      return;
    }
    if (input.action === 'handoff') {
      const note = await noteFrom(task.report);
      if (!note) throw new BoardError('Tệp bàn giao cần có nội dung, tối đa 8.000 ký tự.');
      const changedFiles = await inspect(task);
      if (task.status === 'review' && task.note === note &&
          JSON.stringify(task.changedFiles) === JSON.stringify(changedFiles)) return;
      task.changedFiles = changedFiles;
      task.note = note;
      task.status = 'review';
      record(state, task, 'handoff');
      return;
    }
    if (input.action === 'finish' && task.status === 'review') {
      await inspect(task);
      task.status = 'done';
      record(state, task, 'done');
      return;
    }
    throw new BoardError('Hãy nhận bàn giao trước khi đóng công việc.', 409);
  }

  return async function route(request, url) {
    try {
      if (request.method === 'GET' && url.pathname === '/api/local/handoffs') {
        const root = url.searchParams.get('project');
        if (!root) return json({ defaultRoot, roles });
        const board = await project(root);
        return json({ ...(await read(board)), root: board.root, boardPath: board.file });
      }
      if (request.method !== 'POST' || request.headers.get('Origin') !== origin) return json({ error: 'Yêu cầu không hợp lệ.' }, 403);
      if (url.pathname !== '/api/local/handoffs') return json({ error: 'Không tìm thấy.' }, 404);
      const input = await request.json().catch(() => null);
      if (!input || typeof input !== 'object') throw new BoardError('Yêu cầu không hợp lệ.');
      const board = await project(input.project);
      const state = await change(board, state => input.action === 'assign' ? assign(board, state, input) : update(state, input));
      return json({ ...state, root: board.root, boardPath: board.file });
    } catch (error) {
      if (error instanceof BoardError) return json({ error: error.message }, error.status);
      return json({ error: 'Không cập nhật được bảng bàn giao. Kiểm tra quyền ghi thư mục và Git.' }, 500);
    }
  };
}
