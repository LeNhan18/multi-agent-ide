(() => {
'use strict';
const keys = new Map();
const providerNames = { openai: 'OpenAI', anthropic: 'Claude', gemini: 'Gemini' };
const providersByModel = new Map([['gpt-5-mini', 'openai']]);
const scopeId = (provider, agent) => agent ? provider + ':' + agent.squad + ':' + agent.role : provider;
const messages = {
 connected: 'Key hợp lệ.', rejected: 'Nhà cung cấp từ chối key hoặc quyền đọc model.',
 limited: 'Đang bị giới hạn. Thử lại sau.', missing: 'Chưa có key.', invalid: 'Key không hợp lệ.',
 unavailable: 'Chưa kiểm tra được kết nối.',
};

function mount(host, {getProvider, agent, serverReady} = {}) {
 const box = document.createElement('div');
 box.className = 'api-key-box';
 const providerSelect = document.createElement('select');
 for (const [id, name] of Object.entries(providerNames)) providerSelect.add(new Option(name, id));
 const input = document.createElement('input');
 input.type = 'password';
 input.maxLength = 512;
 input.autocomplete = 'off';
 input.spellcheck = false;
 input.placeholder = 'Dán API key';
 const provider = () => getProvider?.() || providerSelect.value;
 const controls = document.createElement('div');
 controls.className = 'api-key-controls';
 const label = (text, field) => {
  const node = document.createElement('label');
  node.append(text, field);
  return node;
 };
 const save = document.createElement('button');
 save.type = 'button';
 save.textContent = 'Dùng key';
 const check = document.createElement('button');
 check.type = 'button';
 check.textContent = 'Kiểm tra';
 const remove = document.createElement('button');
 remove.type = 'button';
 remove.textContent = 'Xóa key';
 const state = document.createElement('output');
 state.setAttribute('role', 'status');
 let requestVersion = 0;
 const refresh = () => {
  requestVersion++;
  input.value = '';
  check.disabled = false;
  const present = keys.has(scopeId(provider(), agent));
  remove.disabled = !present;
  state.textContent = present ? 'Đang dùng key đã dán.' : serverReady?.() ? 'Có key phía server.' : 'Chưa dán key.';
 };
 function useKey() {
  const value = input.value.trim();
  if (!/^[!-~]{1,512}$/.test(value)) {
   state.textContent = 'Dán key hợp lệ, không có khoảng trắng.';
   return false;
  }
  keys.set(scopeId(provider(), agent), value);
  refresh();
  return true;
 }
 save.onclick = useKey;
 input.addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); useKey(); }
 });
 remove.onclick = () => { keys.delete(scopeId(provider(), agent)); refresh(); };
 providerSelect.onchange = refresh;
 check.onclick = async () => {
  if (input.value.trim() && !useKey()) return;
  const chosen = provider();
  const key = keys.get(scopeId(chosen, agent));
  if (agent && !key) { state.textContent = 'Dán key riêng trước khi kiểm tra.'; return; }
  const version = ++requestVersion;
  check.disabled = true;
  state.textContent = 'Đang kiểm tra…';
  let status = 'unavailable';
  try {
   const response = await fetch('/api/ai/connection?' + new URLSearchParams({provider: chosen}), {
    method: 'POST', headers: key ? {'X-AI-Office-Key': key} : {}, cache: 'no-store', redirect: 'error',
   });
   status = (await response.json()).status;
  } catch {}
  if (version !== requestVersion) return;
  state.textContent = messages[status] || messages.unavailable;
  check.disabled = false;
 };
 if (!getProvider) controls.append(label('Nhà cung cấp', providerSelect));
 controls.append(label(agent ? 'API key riêng' : 'API key chung', input), save, check, remove);
 const note = document.createElement('p');
 note.textContent = 'Key đã dán dùng đến khi tải lại hoặc đóng trang.';
 box.append(controls, state, note);
 host.replaceChildren(box);
 refresh();
 return refresh;
}

window.officeApiKeys = {
 mount,
 registerModels(models) {
  for (const [model, [provider]] of Object.entries(models)) providersByModel.set(model, provider);
 },
 headers(settings, agent) {
  const provider = providersByModel.get(settings.model || 'gpt-5-mini');
  if (!provider || (settings.connection === 'dedicated' && !agent)) return {};
  const key = keys.get(scopeId(provider, settings.connection === 'dedicated' ? agent : null));
  return key ? {'X-AI-Office-Key': key} : {};
 },
};
window.officeApiKeys.mount(document.getElementById('shared-api-key'));
})();
