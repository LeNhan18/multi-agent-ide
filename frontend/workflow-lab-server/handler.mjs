import { assets } from './assets.mjs';

const json = (status, code = 200) => Response.json({ status }, {
  status: code,
  headers: { 'Cache-Control': 'no-store' }
});

async function connection(request, env, url) {
  if (!request.headers.get('oai-authenticated-user-id')) return json('unavailable', 401);
  const key = env.OPENAI_API_KEY?.trim();
  if (request.method === 'GET') return json(key ? 'configured' : 'missing');
  if (request.method !== 'POST') return json('unavailable', 405);
  if (request.headers.get('Origin') !== url.origin) return json('unavailable', 403);
  if (!key) return json('missing');
  try {
    const response = await fetch('https://api.openai.com/v1/models', {
      headers: { Authorization: 'Bearer ' + key },
      redirect: 'error',
      signal: AbortSignal.timeout(8000)
    });
    await response.body?.cancel();
    if (response.ok) return json('connected');
    if (response.status === 401 || response.status === 403) return json('rejected');
    if (response.status === 429) return json('limited');
    return json('unavailable', 502);
  } catch {
    return json('unavailable', 502);
  }
}

const planFields = {
  title: { type: 'string' },
  criteria: { type: 'string' },
  risk: { type: 'string' },
  next: { type: 'string' },
  priority: { type: 'string', enum: ['Cao', 'Vừa', 'Thấp'] },
  hours: { type: 'number' }
};

async function taskPlan(request, env, url) {
  if (!request.headers.get('oai-authenticated-user-id')) return json('unavailable', 401);
  if (request.method !== 'POST') return json('unavailable', 405);
  if (request.headers.get('Origin') !== url.origin) return json('unavailable', 403);
  const key = env.OPENAI_API_KEY?.trim();
  if (!key) return json('missing', 503);
  let prompt;
  try {
    const body = await request.text();
    if (body.length > 8000) return json('invalid', 413);
    const input = JSON.parse(body).prompt;
    if (typeof input !== 'string' || !input.trim() || input.length > 1200) return json('invalid', 400);
    prompt = input.trim();
  } catch {
    return json('invalid', 400);
  }
  try {
    const response = await fetch('https://api.openai.com/v1/responses', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + key, 'Content-Type': 'application/json' },
      redirect: 'error',
      signal: AbortSignal.timeout(40000),
      body: JSON.stringify({
        model: 'gpt-5-mini',
        store: false,
        instructions: 'Lập một việc có thể thực hiện cho bảng AI Office. Trả lời tiếng Việt, ngắn gọn. Suy ra tiêu chí kiểm chứng, rủi ro, bước đầu tiên, ưu tiên và ước lượng giờ cho một phiên. Khi đề bài mơ hồ, ghi giả định hoặc câu cần làm rõ trong rủi ro; không bịa nguồn, dữ kiện hay kết quả đã thực hiện. Giờ dự kiến từ 0.25 đến 12. Chỉ đề xuất kế hoạch, không tuyên bố đã làm việc.',
        input: prompt,
        reasoning: { effort: prompt.length <= 120 ? 'high' : 'medium' },
        max_output_tokens: 2800,
        text: { format: {
          type: 'json_schema', name: 'task_plan', strict: true,
          schema: { type: 'object', properties: planFields, required: Object.keys(planFields), additionalProperties: false }
        } }
      })
    });
    if (!response.ok) {
      await response.body?.cancel();
      return json('unavailable', 502);
    }
    const data = await response.json();
    if (data.status !== 'completed') return json('unavailable', 502);
    const raw = data.output?.flatMap(item => item.content ?? []).find(item => item.type === 'output_text')?.text;
    const plan = JSON.parse(raw);
    const limits = { title: 160, criteria: 600, risk: 450, next: 450 };
    for (const [field, max] of Object.entries(limits)) {
      if (typeof plan[field] !== 'string' || !plan[field].trim()) return json('unavailable', 502);
      plan[field] = plan[field].trim().slice(0, max);
    }
    if (!planFields.priority.enum.includes(plan.priority) || !Number.isFinite(plan.hours)) return json('unavailable', 502);
    plan.hours = Math.max(.25, Math.min(12, Math.round(plan.hours * 4) / 4));
    return Response.json({ plan }, { headers: { 'Cache-Control': 'no-store' } });
  } catch {
    return json('unavailable', 502);
  }
}

export default {
  fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/api/openai/task-plan') return taskPlan(request, env, url);
    if (url.pathname === '/api/openai/connection') return connection(request, env, url);
    if (request.method !== 'GET' && request.method !== 'HEAD') return new Response(null, { status: 405 });
    const path = url.pathname === '/' ? '/index.html' : url.pathname;
    const asset = Object.hasOwn(assets, path) ? assets[path] : undefined;
    if (!asset) return new Response('Not found', { status: 404 });
    return new Response(request.method === 'HEAD' ? null : asset.content, {
      headers: { 'Content-Type': asset.type, 'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff' }
    });
  }
};
