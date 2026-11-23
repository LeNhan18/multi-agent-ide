(() => {
'use strict';

const panel = document.getElementById('accounts');
const status = document.getElementById('account-status');
const list = document.getElementById('account-list');
const route = '/api/local/chatgpt/';
const storageKey = 'ai-office-chatgpt-default-v1';
let selected = null;
let accounts = [];
let available = false;
try { selected = localStorage.getItem(storageKey); } catch {}

const label = (account, index) => (account.email || 'ChatGPT') + ' · Tài khoản ' + (index + 1);
const choices = () => accounts.map((account, index) => [account, label(account, index)]);
function setDefault(value) {
 selected = value;
 try { localStorage.setItem(storageKey, value); } catch {}
}

function authForm(text, id = '') {
 const form = document.createElement('form');
 form.method = 'post';
 form.action = route + 'start';
 const account = document.createElement('input');
 account.type = 'hidden';
 account.name = 'account';
 account.value = id;
 const button = document.createElement('button');
 button.type = 'submit';
 button.textContent = text;
 if (!id) button.className = 'primary';
 form.append(account, button);
 return form;
}

function render() {
 list.replaceChildren();
 const select = document.createElement('select');
 select.setAttribute('aria-label', 'Tài khoản mặc định');
 select.add(new Option('Dùng kết nối API đã cấu hình', ''));
 for (const [account, name] of choices()) {
  select.add(new Option(name + (account.connected ? '' : ' · Cần kết nối lại'), account.id));
  const row = document.createElement('div');
  row.className = 'actions';
  const title = document.createElement('span');
  title.textContent = name;
  row.append(title);
  if (account.connected) {
   const button = document.createElement('button');
   button.type = 'button';
   button.textContent = 'Ngắt kết nối';
   button.onclick = async () => {
    button.disabled = true;
    try {
     const response = await fetch(route + 'sign-out', {
      method: 'POST', body: new URLSearchParams({ account: account.id }), redirect: 'error',
     });
     if (!response.ok) throw Error();
     const { revoked } = await response.json();
     status.textContent = revoked ? 'Đã ngắt kết nối.' : 'Đã đăng xuất trên máy này. Có thể thu hồi quyền trong cài đặt ChatGPT.';
     await load();
    } catch { status.textContent = 'Chưa ngắt được kết nối. Hãy thử lại.'; }
    finally { button.disabled = false; }
   };
   row.append(button);
  } else row.append(authForm('Kết nối lại', account.id));
  list.append(row);
 }
 select.value = selected ?? '';
 select.onchange = () => setDefault(select.value);
 const wrapper = document.createElement('label');
 wrapper.append('Tài khoản mặc định ', select);
 if (accounts.length) list.prepend(wrapper);
 list.append(authForm(accounts.length ? 'Thêm tài khoản ChatGPT' : 'Tiếp tục bằng ChatGPT'));
}

async function load() {
 const response = await fetch(route + 'accounts', { cache: 'no-store', redirect: 'error' });
 if (!response.ok) return;
 ({ accounts } = await response.json());
 if (selected === null) {
  const first = accounts.find(account => account.connected);
  if (first) setDefault(first.id);
 }
 available = true;
 panel.hidden = false;
 render();
}

window.officeAccounts = {
 ready: load().catch(() => {}),
 isAvailable: () => available,
 choices,
 selection(agentChoice) {
  if (!available || agentChoice === 'api') return {};
  const id = agentChoice || selected;
  return id ? { accountId: id } : {};
 },
};

const url = new URL(location.href);
const outcome = url.searchParams.get('chatgpt');
if (outcome) {
 document.querySelector('[data-ide="settings"]').click();
 status.textContent = outcome === 'connected' ? 'Đã kết nối ChatGPT.' : 'Chưa kết nối được. Kiểm tra quyền dùng gói ChatGPT rồi thử lại.';
 url.searchParams.delete('chatgpt');
 history.replaceState(null, '', url.pathname + url.search + url.hash);
}
})();
