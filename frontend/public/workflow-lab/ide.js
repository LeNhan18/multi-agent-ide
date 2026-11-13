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
   <div class="actions"><button id="ide-add" type="button">＋ Tệp</button><button id="ide-import" type="button">Mở tệp</button></div>
   <input type="file" id="ide-file" hidden accept=".txt,.md,.json,.yaml,.yml,.csv,.js,.ts,.html,.css,.py,.toml,.xml,.sql,.log,text/plain">
   <div id="ide-files" class="ide-files-list"></div>
  </aside>
  <div class="ide-editor-body">
   <div class="ide-editor-toolbar">
    <input id="ide-filename" aria-label="Tên tệp" placeholder="Tên tệp" maxlength="120">
    <button id="ide-save" type="button">Lưu</button>
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
 link.download = filename.value.trim() || 'untitled.txt';
 link.click();
 setTimeout(() => URL.revokeObjectURL(url), 1000);
};
by('ide-delete').onclick = () => {
 const file = selected();
 if (!file || !confirm('Xóa tệp ' + file.name + '?')) return;
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
