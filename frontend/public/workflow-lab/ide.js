(() => {
'use strict';
const navigation = document.querySelector('.ide-nav');
const panes = [...document.querySelectorAll('[data-pane]')];
const buttons = [...navigation.querySelectorAll('[data-ide]')];
function showPane(name) {
 for (const pane of panes) pane.hidden = pane.dataset.pane !== name;
 for (const button of buttons) button.setAttribute('aria-pressed', button.dataset.ide === name);
}
for (const button of buttons) button.onclick = () => showPane(button.dataset.ide);

const editor = document.getElementById('ide-editor');
editor.innerHTML = `
 <h1>Soạn thảo</h1>
 <div class="ide-editor-grid">
  <aside class="ide-files" aria-label="Danh sách tệp">
   <button id="ide-project" type="button">Mở thư mục dự án</button>
   <button id="ide-close-project" type="button" hidden>Đóng dự án</button>
   <div class="actions" id="ide-draft-actions"><button id="ide-add" type="button">＋ Tệp</button><button id="ide-import" type="button">Mở tệp</button></div>
   <input type="file" id="ide-file" hidden accept=".txt,.md,.json,.yaml,.yml,.csv,.js,.ts,.html,.css,.py,.toml,.xml,.sql,.log,text/plain">
   <strong id="ide-project-name" hidden></strong>
   <div id="ide-files" class="ide-files-list"></div>
  </aside>
  <div class="ide-editor-body">
   <div class="ide-editor-toolbar">
    <input id="ide-filename" aria-label="Tên tệp" placeholder="Tên tệp" maxlength="120">
    <button id="ide-save" type="button">Lưu</button>
    <button id="ide-reload-file" type="button" hidden>Mở lại tệp</button>
    <button id="ide-download" type="button">Tải xuống</button>
    <button id="ide-delete" type="button">Xóa</button>
   </div>
   <textarea id="ide-text" aria-label="Nội dung tệp" spellcheck="false" placeholder="Tạo hoặc mở một tệp để bắt đầu"></textarea>
  </div>
 </div>
 <p class="ide-status muted" id="ide-status" role="status">Tệp đã lưu nằm trong trình duyệt này.</p>`;

const storageKey = 'ai-office-ide-files-v1';
const limit = 256_000;
const maxFiles = 20;
const by = id => document.getElementById(id);
const content = by('ide-text');
const filename = by('ide-filename');
const fileList = by('ide-files');
let files = [];
try {
 const stored = JSON.parse(localStorage.getItem(storageKey));
 if (Array.isArray(stored)) {
  files = stored.filter(file => typeof file?.id === 'string' && typeof file.name === 'string' &&
   typeof file.text === 'string' && file.text.length <= limit).slice(0, maxFiles);
 }
} catch {}
let activeId = files[0]?.id;
let dirty = false;
let project = null;
let projectFile = null;
let pending = false;
const ignoredFolders = new Set(['.git', '.venv', 'node_modules', '__pycache__', '.next', 'dist', 'build', '.ai-office']);

const selected = () => files.find(file => file.id === activeId);
const status = message => { by('ide-status').textContent = message; };
function persist(next) {
 try {
  localStorage.setItem(storageKey, JSON.stringify(next));
  files = next;
  return true;
 } catch {
  status('Không lưu được. Tải tệp xuống để giữ nội dung.');
  return false;
 }
}
function editableProject() {
 filename.value = projectFile?.path ?? '';
 filename.disabled = true;
 content.value = projectFile?.text ?? '';
 content.disabled = !projectFile;
 by('ide-save').disabled = by('ide-download').disabled = !projectFile;
 by('ide-delete').hidden = true;
 by('ide-reload-file').hidden = !projectFile;
}
function canLeaveProjectFile() {
 if (pending) return false;
 if (!dirty) return true;
 status('Lưu tệp đang sửa hoặc chọn Mở lại tệp để bỏ chỉnh sửa.');
 return false;
}
function describeProjectError(error) {
 if (error.name === 'NotAllowedError' || error.name === 'SecurityError') {
  status('Trình duyệt chưa cấp quyền. Mở lại thư mục và cho phép đọc, ghi.');
 } else if (error.name === 'NotFoundError') {
  status('Tệp hoặc thư mục đã được di chuyển hoặc xóa.');
 } else {
  status(error.message || 'Không truy cập được dự án.');
 }
}
async function loadProjectFile(handle, path, reload = false) {
 if (pending || (!reload && (projectFile?.path === path || !canLeaveProjectFile()))) return;
 pending = true;
 content.readOnly = true;
 try {
  const file = await handle.getFile();
  if (file.size > limit) throw Error('Tệp quá lớn. Giới hạn 256 KB.');
  const bytes = new Uint8Array(await file.arrayBuffer());
  let text;
  try { text = new TextDecoder('utf-8', {fatal: true}).decode(bytes); }
  catch { throw Error('Chỉ hỗ trợ tệp văn bản UTF-8.'); }
  if (text.includes(String.fromCharCode(0))) throw Error('Chỉ hỗ trợ tệp văn bản UTF-8.');
  projectFile = {handle, path, bytes, text, lineEnding: text.includes('\r\n') ? '\r\n' : '\n'};
  dirty = false;
  editableProject();
  for (const button of fileList.querySelectorAll('[data-project-file]')) {
   button.setAttribute('aria-pressed', button.dataset.projectFile === path);
  }
  status('Đã mở ' + path);
  content.focus();
 } catch (error) { describeProjectError(error); }
 finally { content.readOnly = false; pending = false; }
}
async function listProjectFolder(handle, container, prefix = '') {
 const entries = [];
 for await (const entry of handle.values()) {
  if (entry.kind === 'directory' && ignoredFolders.has(entry.name)) continue;
  entries.push(entry);
  if (entries.length > 500) break;
 }
 entries.sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'directory' ? -1 : 1));
 container.replaceChildren();
 for (const entry of entries.slice(0, 500)) {
  const path = prefix + entry.name;
  if (entry.kind === 'directory') {
   const folder = document.createElement('details');
   const label = document.createElement('summary');
   label.textContent = entry.name;
   const children = document.createElement('div');
   children.className = 'ide-project-children';
   folder.append(label, children);
   folder.addEventListener('toggle', async () => {
    if (!folder.open || folder.dataset.loaded) return;
    folder.dataset.loaded = 'true';
    try { await listProjectFolder(entry, children, path + '/'); }
    catch (error) { delete folder.dataset.loaded; describeProjectError(error); }
   });
   container.append(folder);
  } else {
   const button = document.createElement('button');
   button.type = 'button';
   button.textContent = entry.name;
   button.title = path;
   button.dataset.projectFile = path;
   button.setAttribute('aria-pressed', projectFile?.path === path);
   button.onclick = () => loadProjectFile(entry, path);
   container.append(button);
  }
 }
 if (!entries.length || entries.length > 500) {
  const note = document.createElement('small');
  note.textContent = entries.length ? 'Đang hiện tối đa 500 mục.' : 'Thư mục trống.';
  container.append(note);
 }
}
async function openProject() {
 if (pending || (project ? !canLeaveProjectFile() : dirty && !save())) return;
 if (!window.showDirectoryPicker) {
  status('Mở AI Office bằng Chrome hoặc Edge để lưu trực tiếp vào thư mục.');
  return;
 }
 pending = true;
 content.readOnly = filename.readOnly = true;
 try {
  const handle = await window.showDirectoryPicker({mode:'readwrite', id:'ai-office-project'});
  const tree = document.createElement('div');
  tree.className = 'ide-files-list';
  await listProjectFolder(handle, tree);
  project = handle;
  projectFile = null;
  dirty = false;
  fileList.replaceChildren(tree);
  by('ide-project-name').textContent = handle.name;
  by('ide-project-name').hidden = by('ide-close-project').hidden = false;
  by('ide-draft-actions').hidden = true;
  editableProject();
  status('Chọn tệp trong ' + handle.name + ' để chỉnh sửa.');
 } catch (error) {
  if (error.name !== 'AbortError') describeProjectError(error);
 } finally { content.readOnly = filename.readOnly = false; pending = false; }
}
async function saveProject() {
 if (!projectFile || pending || !dirty) return;
 const file = projectFile;
 const edited = content.value;
 const bom = file.bytes[0] === 239 && file.bytes[1] === 187 && file.bytes[2] === 191;
 const output = new TextEncoder().encode((bom ? '\uFEFF' : '') + edited.replace(/\r?\n/g, file.lineEnding));
 if (output.length > limit) { status('Tệp quá lớn. Giới hạn 256 KB.'); return; }
 pending = true;
 let stream;
 try {
  const current = await file.handle.getFile();
  if (current.size !== file.bytes.length) throw Error('Tệp đã thay đổi trên máy. Tải bản sửa xuống trước khi mở lại tệp.');
  const bytes = new Uint8Array(await current.arrayBuffer());
  if (bytes.some((byte, index) => byte !== file.bytes[index])) {
   throw Error('Tệp đã thay đổi trên máy. Tải bản sửa xuống trước khi mở lại tệp.');
  }
  stream = await file.handle.createWritable();
  await stream.write(output);
  await stream.close();
  stream = null;
  file.bytes = output;
  file.text = edited;
  dirty = content.value !== edited;
  status(dirty ? 'Có chỉnh sửa mới chưa lưu.' : 'Đã lưu vào dự án: ' + file.path);
 } catch (error) {
  if (stream) await stream.abort().catch(() => {});
  describeProjectError(error);
 } finally { pending = false; }
}

function render() {
 const file = selected();
 filename.value = file?.name ?? '';
 content.value = file?.text ?? '';
 content.disabled = filename.disabled = !file;
 for (const id of ['ide-save', 'ide-download', 'ide-delete']) by(id).disabled = !file;
 fileList.replaceChildren();
 for (const item of files) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = item.name;
  button.setAttribute('aria-pressed', item.id === activeId);
  button.onclick = () => {
   if (item.id === activeId || (dirty && !save())) return;
   activeId = item.id;
   render();
  };
  fileList.append(button);
 }
}
function save() {
 if (project) return saveProject();
 if (!selected()) return false;
 const name = filename.value.trim();
 if (!name || /[\\/:*?"<>|]/.test(name)) {
  status('Nhập tên tệp hợp lệ.');
  filename.focus();
  return false;
 }
 if (content.value.length > limit) {
  status('Tệp quá lớn. Giới hạn 256.000 ký tự.');
  return false;
 }
 const next = files.map(file => file.id === activeId ? {...file, name, text: content.value} : file);
 if (!persist(next)) return false;
 dirty = false;
 render();
 status('Đã lưu ' + name);
 return true;
}
function add(name, text = '') {
 if (project || pending) return;
 if (files.length >= maxFiles) { status('Đã đủ 20 tệp. Xóa một tệp để thêm tệp mới.'); return; }
 if (dirty && !save()) return;
 const file = {id: crypto.randomUUID(), name, text};
 if (!persist([...files, file])) return;
 activeId = file.id;
 render();
 status('Đã mở ' + name);
 content.focus();
}
function changed() {
 dirty = true;
 status('Chưa lưu · Ctrl+S');
}
content.oninput = filename.oninput = changed;
content.addEventListener('keydown', event => {
 if (event.key !== 'Tab' || event.shiftKey || event.ctrlKey || event.altKey) return;
 if (content.selectionStart === content.selectionEnd && content.selectionStart > 0) {
  const lineStart = content.value.lastIndexOf('\n', content.selectionStart - 1) + 1;
  if (/^\s*$/.test(content.value.slice(lineStart, content.selectionStart))) {
   event.preventDefault();
   content.setRangeText('    ', content.selectionStart, content.selectionEnd, 'end');
   changed();
  }
 }
});
document.addEventListener('keydown', event => {
 if (editor.hidden || !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return;
 event.preventDefault();
 save();
});
by('ide-project').onclick = openProject;
by('ide-reload-file').onclick = () => {
 if (!projectFile || pending || (dirty && !confirm('Bỏ chỉnh sửa chưa lưu và mở lại tệp gốc?'))) return;
 return loadProjectFile(projectFile.handle, projectFile.path, true);
};
by('ide-close-project').onclick = () => {
 if (!canLeaveProjectFile()) return;
 project = projectFile = null;
 by('ide-project-name').hidden = by('ide-close-project').hidden = true;
 by('ide-draft-actions').hidden = by('ide-delete').hidden = false;
 by('ide-reload-file').hidden = true;
 render();
 status('Đã đóng dự án.');
};
by('ide-add').onclick = () => add('untitled.md');
by('ide-save').onclick = save;
by('ide-import').onclick = () => by('ide-file').click();
by('ide-file').onchange = async event => {
 const file = event.target.files[0];
 event.target.value = '';
 if (!file) return;
 if (file.size > limit) { status('Tệp quá lớn. Giới hạn 256 KB.'); return; }
 try {
  const text = await file.text();
  if (text.includes('\0')) { status('Chọn tệp văn bản.'); return; }
  add(file.name.slice(0, 120), text);
 } catch { status('Không đọc được tệp.'); }
};
by('ide-download').onclick = () => {
 const url = URL.createObjectURL(new Blob([content.value], {type:'text/plain;charset=utf-8'}));
 const link = document.createElement('a');
 link.href = url;
 link.download = projectFile?.handle.name || filename.value.trim() || 'untitled.txt';
 link.click();
 setTimeout(() => URL.revokeObjectURL(url), 1000);
};
by('ide-delete').onclick = () => {
 const file = selected();
 if (project || pending || !file || !confirm('Xóa tệp ' + file.name + '?')) return;
 if (!persist(files.filter(item => item.id !== activeId))) return;
 activeId = files[0]?.id;
 dirty = false;
 render();
 status('Đã xóa tệp.');
};
window.addEventListener('beforeunload', event => {
 if (dirty) { event.preventDefault(); event.returnValue = ''; }
});
render();
showPane('editor');
})();
