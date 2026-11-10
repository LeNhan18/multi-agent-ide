(() => {
'use strict';
const badge=document.getElementById('api-badge');
const button=document.getElementById('api-check');
const result=document.getElementById('api-result');
const states={
 missing:['Chưa cấu hình','Chưa tìm thấy Secret OPENAI_API_KEY trên bản đang chạy.'],
 configured:['Đã cấu hình','Có Secret trên bản đang chạy. Bấm kiểm tra để xác nhận OpenAI chấp nhận key.'],
 connected:['Đã kết nối','OpenAI đã chấp nhận key khi đọc danh sách model.'],
 rejected:['Kiểm tra key','OpenAI từ chối thông tin xác thực hoặc quyền đọc model.'],
 limited:['Thử lại sau','OpenAI đang giới hạn yêu cầu.'],
 unavailable:['Chưa xác minh','Không xác minh được kết nối với OpenAI.']
};
async function refresh(method){
 button.disabled=true;
 if(method==='POST')result.textContent='Đang kiểm tra với OpenAI…';
 let state='unavailable';
 try{
  const response=await fetch('/api/openai/connection',{method,cache:'no-store'});
  const data=await response.json();
  if(Object.hasOwn(states,data.status))state=data.status;
 }catch{}
 const [label,message]=states[state];
 badge.textContent=label;
 badge.className='tag '+(state==='connected'||state==='configured'?'ok':'warn');
 result.textContent=message;
 button.disabled=false;
}
button.addEventListener('click',()=>refresh('POST'));
refresh('GET');
})();
