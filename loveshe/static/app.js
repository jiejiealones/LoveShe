'use strict';
const $ = (s, root = document) => root.querySelector(s);
const token = $('meta[name="loveshe-token"]').content;
const state = {contacts: [], contact: null, detail: null, tab: 'overview', selected: null,
  filters: {query: '', start: '', end: '', offset: 0, prefix: ''}, jobs: new Map(), sessions: [], preview: null, drafts: new Map(), selections: new Map()};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = n => Number(n || 0).toLocaleString('zh-CN');
const when = (ts, short = false) => ts ? new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', year:'numeric',month:'2-digit',day:'2-digit',...(short ? {} : {hour:'2-digit',minute:'2-digit',hour12:false})}).format(new Date(ts)) : '暂无';
const localDay = ts => new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(ts ? new Date(ts) : new Date());
const statusName = {fact:'已确认事实',inference:'推测',unconfirmed:'待确认'};
const modeName = {memory:'记忆整理',analyze:'关系分析',advise:'回复建议',consult:'自由咨询'};
let noticeTimer;
function notice(message, error = false) {
  clearTimeout(noticeTimer); const el = $('#notice'); el.textContent = message; el.hidden = false;
  el.className = error ? 'error' : '';
  if (!error) noticeTimer = setTimeout(() => { el.hidden = true; }, 10000);
}
async function api(path, data) {
  const options = {headers: {'X-LoveShe-Token':token}};
  if (data !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(data); }
  const response = await fetch('/api/' + path, options);
  const value = await response.json();
  if (!response.ok) throw Error(value.error || '操作未完成');
  return value;
}
const withContact = data => ({contact:state.contact,...data});
function showModal(html) { $('#modal-body').innerHTML = html; if (!$('#modal').open) $('#modal').showModal(); }
function closeModal() { $('#modal').close(); }
function empty(title, note, action = '') { return `<div class="empty"><h2>${esc(title)}</h2><p>${esc(note)}</p>${action}</div>`; }
function evidenceButtons(ids) { return ids.map(id=>`<button class="ghost evidence" data-action="evidence" data-prefix="${esc(id.slice(0,8))}">${esc(id.slice(0,8))} ↗</button>`).join(' '); }
function markdown(value) {
  // Render a deliberately small Markdown subset; all source text is escaped.
  return String(value).split('\n').map(line => {
    let text = esc(line).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
    text = text.replace(/`?([a-f0-9]{8})(?:[a-f0-9]{0,56})(?:…[a-f0-9]*)?`?/g,
      (_, prefix) => `<button class="ghost evidence" data-action="evidence" data-prefix="${prefix}">${prefix} ↗</button>`);
    if (/^### /.test(line)) return `<h3>${text.slice(4)}</h3>`;
    if (/^##? /.test(line)) return `<h2>${text.replace(/^#+ /,'')}</h2>`;
    if (/^&gt; /.test(text)) return `<blockquote>${text.slice(5)}</blockquote>`;
    if (/^[-*] /.test(line)) text = '• ' + text.slice(2);
    return text ? `<p>${text}</p>` : '<br>';
  }).join('');
}
async function loadState() {
  const data = await api('state'); state.contacts = data.contacts; state.info = data;
  if (!state.contacts.some(c => c.id === state.contact)) state.contact = state.contacts[0]?.id || null;
  $('#contacts').innerHTML = state.contacts.map(c=>`<button class="contact ${c.id===state.contact?'active':''}" data-contact="${esc(c.id)}"><span class="avatar">${esc([...c.display_name][0] || '友')}</span><span>${esc(c.display_name)}<small>${num(c.message_count)} 条消息 · ${num(c.memory_count)} 条记忆</small></span></button>`).join('') || '<p class="muted small">添加联系人，开始建立背景。</p>';
  $('#model-label').textContent = data.model.configured ? `${data.model.model} · 外部模型` : '模型未配置 · 请运行“配置模型”';
  $('#model-label').title = '在项目目录双击“配置模型.cmd”，保存后重启应用。';
  data.jobs.filter(j=>['queued','running'].includes(j.state)).forEach(j=>watchJob(j.id));
}
async function loadContact() {
  const cid = state.contact;
  if (!cid) { state.detail = null; render(); return; }
  const detail = await api('contact?contact='+encodeURIComponent(cid));
  if (state.contact !== cid) return;
  state.detail = detail;
  if (!detail.consultations.some(c=>c.id===state.selected)) state.selected = detail.consultations[0]?.id || null;
  $('#contact-name').textContent = detail.contact.display_name;
  render();
}
async function refresh() { await loadState(); await loadContact(); }
function render() {
  $('#edit-profile').hidden = !state.contact;
  $('#tabs').querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.tab===state.tab));
  const el = $('#content');
  if (!state.detail) { $('#contact-name').textContent = '欢迎使用 LoveShe'; el.innerHTML = empty('从一位联系人开始','导入聊天记录，保存重要记忆，再一起讨论当下的沟通。','<button data-action="add-contact">新增联系人</button>'); return; }
  if (state.tab==='overview') renderOverview();
  if (state.tab==='history') { el.innerHTML = '<div class="card"><h2>聊天记录</h2><form data-form="history" class="history-filter"><div class="field"><label for="history-query">关键词或消息 ID</label><input id="history-query" name="query" placeholder="搜索聊天内容" value="'+esc(state.filters.query)+'"></div><div><label for="history-start">开始日期</label><input id="history-start" name="start" type="date" value="'+esc(state.filters.start)+'"></div><div><label for="history-end">结束日期</label><input id="history-end" name="end" type="date" value="'+esc(state.filters.end)+'"></div><button>筛选</button></form><div id="history-results">正在读取…</div></div>'; loadMessages().catch(e=>notice(e.message,true)); }
  if (['advisor','replies'].includes(state.tab)) renderAdvisor();
  if (state.tab==='memory') renderMemory();
  if (state.tab==='data') renderData();
}
function renderOverview() {
  const d=state.detail, s=d.statistics, ratio=s.total?Math.round((s.senders.me||0)/s.total*100):0;
  const months=Object.entries(s.by_month), max=Math.max(1,...months.map(x=>x[1]));
  $('#content').innerHTML = `<div class="card hero"><span class="eyebrow">理解过去 · 辅助当下</span><h2>每一次交流，都有它的背景。</h2><p>${esc(d.contact.goal || '先记录你希望怎样相处，再结合真实聊天和记忆，找到适合自己的表达。')}</p><div class="row"><button data-action="go-advisor">和军师聊聊 →</button><button class="ghost" data-action="quick-analysis">分析这段关系</button></div></div>
    <div class="metrics"><div class="metric"><div class="label">已收录消息</div><div class="value">${num(s.total)}</div><div class="caption">${when(s.first,true)} — ${when(s.last,true)}</div></div><div class="metric"><div class="label">本人发送占比</div><div class="value">${ratio}<span class="small">%</span></div><div class="caption">消息数量不能代表投入程度</div></div><div class="metric"><div class="label">长期记忆</div><div class="value">${d.memories.length}</div><div class="caption">${d.memories.filter(m=>m.status==='unconfirmed').length} 条待确认</div></div><div class="metric"><div class="label">咨询记录</div><div class="value">${d.consultations.length}</div><div class="caption">最近 100 次咨询</div></div></div>
    <div class="grid"><div class="card"><h2>每月互动记录</h2>${months.length?`<div class="bars">${months.map(([month,count])=>`<div class="bar-item"><span>${num(count)}</span><div class="bar" data-height="${Math.max(3,count/max*120)}"></div><span>${esc(month)}</span></div>`).join('')}</div>`:empty('还没有聊天记录','在数据管理中导入 JSON 或从微信更新。')}<p class="small muted">按北京时间统计。月份可能不完整，消息数量变化需结合原文解释。</p></div><div class="card"><div class="card-header"><h2>值得记住的事</h2><button class="ghost" data-action="go-memory">管理 →</button></div>${d.memories.slice(0,3).map(m=>`<div class="memory-item"><span class="badge ${m.status}">${statusName[m.status]}</span><p class="small">${esc(m.content)}</p></div>`).join('') || '<p class="muted">你确认的重要背景，会留在这里。</p>'}</div></div>
    <div class="card"><h2>关系背景</h2><p class="muted">${esc(d.contact.background || '尚未填写。认识经过、关系阶段和重要经历，都可以在联系人档案中补充。')}</p><button class="ghost" data-action="edit-profile">编辑档案 →</button></div>`;
  document.querySelectorAll('[data-height]').forEach(el=>el.style.height=el.dataset.height+'px');
}
async function loadMessages() {
  const cid=state.contact, key=JSON.stringify(state.filters), params=new URLSearchParams({contact:cid,...state.filters});
  const data=await api('messages?'+params);
  if (cid!==state.contact || state.tab!=='history' || key!==JSON.stringify(state.filters)) return;
  $('#history-results').innerHTML=(state.filters.prefix?`<p class="small muted">正在查看证据 ${esc(state.filters.prefix)} <button class="ghost" data-action="clear-prefix">返回记录</button></p>`:'')+`<div class="message-list">${data.messages.map(m=>`<div class="message ${m.sender}"><div class="metadata"><span>${m.sender==='me'?'我':m.sender==='them'?esc(state.detail.contact.display_name):'方向未知'}</span><span>${when(m.ts)}</span></div><div class="bubble">${esc(m.content)}</div><div class="metadata"><span>${m.kind==='1'||m.kind==='text'?'文字':'类型 '+esc(m.kind)}</span><button class="ghost" data-action="message-memory" data-id="${esc(m.id)}">保存为记忆</button><button class="ghost" data-action="copy-id" data-id="${esc(m.id)}">${m.id.slice(0,8)}</button></div></div>`).join('') || empty('没有匹配记录','试试其他关键词或时间范围。')}</div><div class="pagination"><button class="secondary" data-action="older" ${data.offset+100>=data.total?'disabled':''}>更早记录</button><span class="small muted">${data.total?data.offset+1:0}–${Math.min(data.offset+100,data.total)} / ${num(data.total)}</span><button class="secondary" data-action="newer" ${data.offset===0?'disabled':''}>较新记录</button></div>`;
}
function saveAskDraft() {
  const form = $('[data-form="ask"]');
  if (!form || !state.contact || state.tab==='replies') return;
  const draft = Object.fromEntries(new FormData(form));
  draft.history = form.elements.history.checked; draft.memory = form.elements.memory.checked;
  state.drafts.set(state.contact, draft);
}
function renderAdvisor() {
  const replies=state.tab==='replies', d={...state.detail,consultations:state.detail.consultations.filter(c=>replies?c.mode==='advise':c.mode!=='advise')};
  if (state.selected!==null && !d.consultations.some(c=>c.id===state.selected)) state.selected=d.consultations[0]?.id||null;
  const selected=d.consultations.find(c=>c.id===state.selected);
  $('#content').innerHTML=`<div class="advisor-grid"><div><div class="card"><div class="card-header"><h2>${replies?'根据最近对话，直接给出回复':'把当下的困惑，说具体一点'}</h2><span class="badge">${esc(state.info.model.model)}</span></div><form data-form="ask">${replies?`<input type="hidden" name="task" value="advise"><p class="muted">自动使用这位联系人的长期记忆和近期聊天，提供三个不同意图的回复方案。无需选择记录或填写问题。</p><label for="reply-note">补充要求或刚收到的消息（可不填）</label><textarea id="reply-note" name="question" placeholder="不填即可直接生成；也可以补充希望表达的意思。"></textarea><input type="checkbox" name="history" checked hidden><input type="checkbox" name="memory" checked hidden><div class="row end"><button>生成回复建议 →</button></div>`:`<div class="fields"><div><label for="ask-task">你希望得到什么</label><select id="ask-task" name="task"><option value="consult">自由咨询</option><option value="analyze">历史分析 / 阶段复盘</option></select></div><div><label for="ask-parent">咨询上下文</label><select id="ask-parent" name="parent_id"><option value="">开始新话题</option>${selected?`<option value="${selected.id}">追问当前记录 #${selected.id}</option>`:''}</select></div></div><label for="question">问题或最新聊天内容</label><textarea id="question" name="question" required placeholder="粘贴刚收到的消息，或描述你想理解的事情…"></textarea><details class="field"><summary>高级：自定义本次上下文</summary><div class="fields"><div><label for="ask-start">历史开始日期（可选）</label><input id="ask-start" type="date" name="start"></div><div><label for="ask-end">历史结束日期（可选）</label><input id="ask-end" type="date" name="end"></div></div><div class="row spread"><div class="row"><label class="check"><input name="history" type="checkbox" checked>使用历史</label><label class="check"><input name="memory" type="checkbox" checked>使用记忆与反馈</label></div></div></details><div class="row end"><button>预览发送内容 →</button></div>`}</form><p class="small muted">自动带上当前联系人的长期记忆与近期聊天，无需每次选择。咨询发送前仍可预览。</p></div>
    ${selected?`<div class="card"><div class="card-header"><h2>${modeName[selected.mode]||'咨询'} <span class="small muted">#${selected.id}</span></h2><span class="small muted">${when(selected.created_at)}</span></div><p class="small muted">${esc(replies?"根据当前记忆与近期对话生成，采用前请核对是否符合你的真实意愿。":selected.question)}</p><hr class="divider"><div class="answer">${markdown(selected.answer)}</div><hr class="divider"><div class="row">${replies?`<button class="secondary" data-action="discuss-reply">向军师追问</button>`:`<button class="secondary" data-action="follow-up">继续追问</button>`}<button class="secondary" data-action="edit-draft">编辑 / 复制</button><button class="secondary" data-action="feedback">记录反馈</button><button class="ghost" data-action="extract-consult">提取记忆候选</button></div>${d.feedback.filter(f=>f.consultation_id===selected.id).map(f=>`<div class="feedback"><span class="badge">已记录反馈</span><p class="small">${esc(f.note)}</p></div>`).join('')}</div>`:empty(replies?'让记忆帮你接上这段对话':'咨询从这里开始',replies?'直接点击生成即可，无需先输入问题。':'提出具体问题，军师会结合这位联系人的资料回答。')}</div>
    <aside class="card consult-history"><div class="card-header"><h3>${replies?'回复建议记录':'咨询记录'}</h3><button class="ghost" data-action="new-topic">${replies?'新建议':'新话题'}</button></div>${d.consultations.map(c=>`<div class="consult-item ${c.id===state.selected?'active':''}" role="button" tabindex="0" data-action="select-consult" data-id="${c.id}"><span class="metadata">${modeName[c.mode]||'咨询'} · ${when(c.created_at,true)}</span><p>${esc(replies?'基于记忆与近期对话的三个回复方案':c.question)}</p></div>`).join('') || '<p class="small muted">暂无记录</p>'}</aside></div>`;
  const draft = state.drafts.get(state.contact), form = $('[data-form="ask"]');
  if (!draft && selected && form.elements.parent_id) form.elements.parent_id.value=selected.id;
  if (draft && !replies) for (const [name, value] of Object.entries(draft)) {
    const field = form.elements[name]; if (!field) continue;
    if (field.type === 'checkbox') field.checked = value; else field.value = value;
  }
}
function renderMemory() {
  const d=state.detail;
  const summary=d.rolling_summary||{sections:{}},labels={portrait:'人物画像',relationship:'关系现状与变化',communication:'互动与沟通模式',open_questions:'尚待确认与后续关注'};
  const summaryCard=`<div class="card"><div class="card-header"><h2>人物画像与关系滚动摘要</h2><span class="small muted">${when(summary.updated_at)}</span></div><p class="small muted">上一版摘要 + 新增聊天 → 新版摘要。咨询与回复自动使用；重要事件条目保留在下方。${summary.stale?'记忆或档案已纠正，旧摘要已停用，下次更新重建。':''}</p>${Object.entries(labels).map(([key,label])=>{const c=summary.sections[key];return `<div class="memory-item"><h3>${label}</h3>${c?.text?`<span class="badge ${c.status}">${statusName[c.status]}</span><p>${esc(c.text)}</p>${evidenceButtons(c.evidence_ids||[])}`:'<p class="muted">尚无足够资料，完成更新后生成。</p>'}</div>`;}).join('')}</div>`;
  $('#content').innerHTML=automationPanel()+summaryCard+`<div class="card hero"><h2>记住重要的，也允许改变。</h2><p>事实、推测和待确认信息分别保存。每次纠错、删除和恢复都有版本记录；手动提取候选由你审核；启用自动维护后，AI 会按已授权范围增量更新。</p><div class="row"><button data-action="add-memory">添加记忆</button><button class="secondary" data-action="extract">生成记忆候选</button><button class="ghost" data-action="versions">修改历史与恢复 →</button></div></div>
    ${d.candidates.length?`<div class="card"><h2>待审核候选 <span class="badge">${d.candidates.length}</span></h2>${d.candidates.map(c=>`<div class="candidate"><span class="badge unconfirmed">AI 候选 · 尚未写入记忆</span><p>${esc(c.content)}</p><div class="row">${evidenceButtons(JSON.parse(c.evidence_ids))}<button class="secondary" data-action="review-candidate" data-id="${c.id}">编辑并保存</button><button class="ghost" data-action="reject-candidate" data-id="${c.id}">忽略</button></div></div>`).join('')}</div>`:''}
    <div class="card"><div class="card-header"><h2>联系人长期记忆</h2><span class="small muted">${d.memories.length} 条</span></div>${d.memories.map(m=>`<div class="memory-item"><div class="row spread"><span class="badge ${m.status}">${statusName[m.status]}</span><span class="metadata">${esc(m.category)} · ${m.origin==='auto'?'自动生成':'手动保存'}${m.locked?' · 已保护':''} · ${when(m.updated_at)}</span></div><p>${esc(m.content)}</p><div class="row spread"><div>${evidenceButtons(m.evidence_ids)}</div><div><button class="ghost" data-action="edit-memory" data-id="${m.id}">编辑 / 确认</button><button class="ghost" data-action="delete-memory" data-id="${m.id}">删除</button></div></div></div>`).join('') || empty('还没有长期记忆','可以手动添加重要背景，或生成候选后审核保存。')}</div>`;
}
function automationPanel() {
  const d=state.detail,p=d.automation,c=d.memory_coverage;
  return `<div class="card"><div class="card-header"><h2>自动跟进与联系人记忆</h2><span class="badge ${p.capture_enabled||p.auto_memory?'':'unconfirmed'}">${p.capture_enabled?'监听中':'监听暂停'} · ${p.auto_memory?'自动维护中':'记忆维护暂停'}</span></div><p class="small muted">最近同步：${when(p.last_poll)} · 最近记忆更新：${when(p.last_memory)}<br>历史压缩 ${num(c.processed)} / ${num(c.total)} 条，剩余 ${num(c.pending)} 条。上次完整日期检查点：${esc(c.checkpoint_day||'尚未完成')}。<br>本次补齐：${esc(c.window?.start_day||c.next_start_day)} — ${esc(c.window?.end_day||c.next_end_day)}（含首尾两天）。${c.window?`区间已处理 ${num(c.window.position)} / ${num(c.window.total)} 条。`:''}全部批次成功才推进日期；已有长期记忆继续保留。</p>${p.last_error?`<div class="notice error">${esc(p.last_error)}<br>已安排退避重试；也可修改设置后重试。</div>`:''}<form data-form="automation"><div class="row field"><label class="check"><input type="checkbox" name="capture_enabled" ${p.capture_enabled?'checked':''}>自动监听并保存微信增量</label><label class="check"><input type="checkbox" name="auto_memory" ${p.auto_memory?'checked':''}>按天更新记忆与滚动摘要</label><label class="check"><input type="checkbox" name="auto_overwrite" ${d.auto_overwrite?'checked':''}>允许合并与更新自动记忆</label></div><p class="small muted">手工保存或纠正的记忆受保护，不会被自动覆盖。监听只在 LoveShe 服务与本机微信运行时工作，使用周期轮询，有延迟。</p><details class="field"><summary>按天更新与近期上下文</summary><div class="fields"><div><label for="poll-seconds">监听间隔（秒）</label><input id="poll-seconds" name="poll_seconds" type="number" min="10" max="300" value="${p.poll_seconds}"></div><div><label>记忆更新方式</label><p class="notice small">按天更新：每日首次运行补齐至今天；同日新增内容可点“更新记忆到今天”立即整理。已有未完成区间会续跑。</p></div><div><label for="recent-days">最近消息前多少天</label><input id="recent-days" name="recent_days" type="number" min="1" max="90" value="${p.recent_days}"></div><div><label for="recent-limit">近期消息最多条数</label><input id="recent-limit" name="recent_limit" type="number" min="20" max="300" value="${p.recent_limit}"></div></div></details><div class="notice small">自动维护会分批向 ${esc(state.info.model.model)}（${esc(state.info.model.endpoint)}）发送本联系人的原文、档案、相关记忆、咨询与反馈；先补齐完整日期区间，再连续分批整理；每批最多160条消息 / 16000字原文，最多4轮咨询与10条反馈。使用当前账户额度，首次历史整理可能有多批请求。完整请求与结果可在更新记录中查看。</div><label class="check field"><input name="consent" type="checkbox">我同意按上述范围持续自动处理这位联系人的数据（首次启用或更换模型时必选）</label><div class="row"><button>保存自动跟进设置</button><button type="button" class="secondary" data-action="memory-now" ${p.auto_memory?'':'disabled'}>更新记忆到今天</button><button type="button" class="ghost" data-action="memory-runs">查看自动外发与更新记录 →</button></div></form><p class="small muted field">独立记忆目录：${esc(d.memory_directory)}<br>MEMORY.md 保存长期条目，memory_summary.md 为人物画像与关系滚动摘要，memory_index.md 为事件索引，updates/ 保存更新记录；请在应用中纠错。</p></div>`;
}
function renderData() {
  const d=state.detail, last=d.statistics.last;
  const summary=d.rolling_summary||{sections:{}},labels={portrait:'人物画像',relationship:'关系现状与变化',communication:'互动与沟通模式',open_questions:'尚待确认与后续关注'};
  const summaryCard=`<div class="card"><div class="card-header"><h2>人物画像与关系滚动摘要</h2><span class="small muted">${when(summary.updated_at)}</span></div><p class="small muted">上一版摘要 + 新增聊天 → 新版摘要。咨询与回复自动使用；重要事件条目保留在下方。${summary.stale?'记忆或档案已纠正，旧摘要已停用，下次更新重建。':''}</p>${Object.entries(labels).map(([key,label])=>{const c=summary.sections[key];return `<div class="memory-item"><h3>${label}</h3>${c?.text?`<span class="badge ${c.status}">${statusName[c.status]}</span><p>${esc(c.text)}</p>${evidenceButtons(c.evidence_ids||[])}`:'<p class="muted">尚无足够资料，完成更新后生成。</p>'}</div>`;}).join('')}</div>`;
  $('#content').innerHTML=automationPanel()+summaryCard+`<div class="grid"><div><div class="card"><h2>从微信更新</h2><p class="small muted">手动读取本机微信单人会话并导入，重复消息会自动跳过。</p><div class="notice">${d.binding?'已绑定微信会话。最近更新：'+when(d.binding.last_sync):'首次使用请读取会话列表，选择正确联系人并绑定。'}</div><button class="secondary" data-action="sessions">读取微信会话列表</button><div id="session-picker"></div><form data-form="sync"><div class="fields"><div><label for="sync-start">开始日期</label><input id="sync-start" name="start" type="date" required value="${localDay(last)}"></div><div><label for="sync-end">结束日期</label><input id="sync-end" name="end" type="date" required value="${localDay()}"></div></div><button ${d.binding?'':'disabled'}>更新聊天记录</button></form><p class="small muted">默认从最新消息所在日重新获取，覆盖边界并去重。首次连接失败时请先完成 WeFlow 初始化。</p></div>
    <div class="card"><h2>导入 JSON 文件</h2><form data-form="import"><label for="import-file">WeFlow / she-love-me / 通用消息 JSON（最大32MB）</label><input id="import-file" name="file" type="file" accept=".json,application/json" required><div class="row end field"><button>导入到 ${esc(d.contact.display_name)}</button></div></form><p class="small muted">通用格式：timestamp、sender（me / them）、content。文件仅在本机处理。</p></div>
    <div class="card"><h2>最近导入</h2>${d.imports.map(i=>`<div class="memory-item small"><span class="metadata">${esc(i.source)} · ${when(i.created_at)}</span><p>${esc(importSummary(JSON.parse(i.result)))}</p></div>`).join('') || '<p class="muted small">尚无新版导入记录，已有验证数据已沿用。</p>'}</div></div>
    <div><div class="card"><h2>数据由你管理</h2><p class="small muted">存储位置：<br>${esc(state.info.data_path)}</p><div class="field"><button class="secondary" data-action="export">导出本联系人 JSON</button></div><div class="field"><button class="secondary" data-action="backup">备份完整数据库</button></div><hr class="divider"><p class="small muted">备份含私人聊天，请自行妥善保管。删除联系人会清除此应用内的聊天、记忆和咨询；已有原始导出与备份需在本机另行管理。</p><button class="danger" data-action="delete-contact">删除联系人全部应用数据</button></div>
    <div class="card"><h2>已预留的后续能力</h2>${state.info.capabilities.map(c=>`<div class="capability"><span>${esc(c.name)}</span><span class="badge ${c.enabled?'':'unconfirmed'}">${c.enabled?'可用':'已预留 · 待实现'}</span></div>`).join('')}<p class="small muted field">监听与自动记忆需在上方按联系人启用；读屏和悬浮窗仍待实现。</p></div></div></div>`;
  if (state.sessions.length) renderSessions();
}
const importSummary = r => `共 ${num(r.total)} 条；新增 ${num(r.imported)}，重复 ${num(r.duplicates)}，无效 ${num(r.invalid)}，方向未知 ${num(r.unknown_sender)}，空内容 ${num(r.empty_content)}${r.source_may_have_more?'。源文件提示仍有更多记录':''}`;
function renderSessions() {
  const el=$('#session-picker'); if(!el)return;
  el.innerHTML=`<form data-form="bind"><div class="field"><label for="bind-session">选择对应的微信单人会话（重名请核对标识）</label><select id="bind-session" name="external_id">${state.sessions.map(s=>`<option value="${esc(s.id)}" ${s.name===state.detail.contact.display_name?'selected':''}>${esc(s.name)} · ${esc(s.id)}</option>`).join('')}</select></div><button class="secondary">确认绑定</button></form>`;
}
function contactModal(create=false) {
  const c=create?{}:state.detail.contact;
  showModal(`<h2>${create?'新增联系人':'联系人档案'}</h2><form data-form="profile"><input type="hidden" name="contact" value="${esc(c.id||'')}"><div class="field"><label for="profile-name">备注名</label><input id="profile-name" name="name" value="${esc(c.display_name||'')}" maxlength="100" required></div><div class="field"><label for="profile-goal">希望怎样相处 / 沟通目标</label><textarea id="profile-goal" name="goal" maxlength="4000">${esc(c.goal||'')}</textarea></div><div class="field"><label for="profile-bg">认识经过、关系背景与本人表达偏好</label><textarea id="profile-bg" name="background" maxlength="12000">${esc(c.background||'')}</textarea></div><div class="row end"><button>保存档案</button></div></form>`);
}
function memoryModal(item={}, candidate=false) {
  showModal(`<h2>${candidate?'审核记忆候选':item.id?'编辑记忆':'添加记忆'}</h2><form data-form="${candidate?'candidate':'memory'}"><input type="hidden" name="id" value="${item.id||''}"><input type="hidden" name="evidence" value="${esc(JSON.stringify(item.evidence_ids||[]))}"><div class="fields"><div><label for="memory-category">类别</label><input id="memory-category" name="category" value="${esc(item.category||'background')}" required maxlength="50"></div><div><label for="memory-status">可信状态</label><select id="memory-status" name="status">${Object.entries(statusName).map(([id,name])=>`<option value="${id}" ${(item.status||'unconfirmed')===id?'selected':''}>${name}</option>`).join('')}</select></div></div><div class="field"><label for="memory-content">记忆内容</label><textarea id="memory-content" name="content" maxlength="12000" required>${esc(item.content||'')}</textarea></div><p class="small muted">标为事实前请核对。没有消息证据的内容应来自你主动确认的背景。</p><div class="row end"><button>保存${candidate?'到长期记忆':''}</button></div></form>`);
}
async function previewRequest(data) {
  const cid=state.contact; const p=await api('preview',withContact(data));
  if(cid!==state.contact) return;
  state.preview=p;
  const raw=p.payload[1].content, marker='资料(JSON):\n';
  const ctx=JSON.parse(raw.slice(raw.lastIndexOf(marker)+marker.length));
  showModal(`<h2>确认这一次发送范围</h2><p class="muted small">${esc(p.model_info.endpoint)} · ${esc(p.model_info.model)}<br>联系人：${esc(state.detail.contact.display_name)} · 外部模型处理</p><div class="notice">${p.message_ids.length} 条近期消息 · ${p.memory_ids.length} 条记忆 · ${ctx.feedback.length} 条反馈 · ${ctx.prior_consultations.length} 轮咨询上下文${ctx.prior_context_reset?'<br>记忆或档案已变更，旧咨询上下文已排除。':''}</div><p class="small muted">下面是完整拟发送资料，含你输入的问题。预览15分钟内有效；数据变化后需重新预览。</p><textarea class="preview" aria-label="完整拟发送内容" readonly>${esc(JSON.stringify(p.payload,null,2))}</textarea><div class="field"><label class="check"><input id="confirm-send" type="checkbox">我确认将以上内容发送给所示模型</label></div><div class="row end"><button class="secondary" data-action="cancel-modal">取消</button><button id="send-preview" data-action="send" disabled>确认发送</button></div>`);
}
function renderTasks() {
  $('#tasks').innerHTML=[...state.jobs.values()].map(j=>`<div class="task"><span class="spinner"></span><span>${esc(j.name||'正在处理')} · ${esc(state.contacts.find(c=>c.id===j.contact)?.display_name||'本机')}<br><span class="muted">可继续浏览，完成后结果会保存在本机。</span></span></div>`).join('');
}
function watchJob(id) {
  if(state.jobs.has(id))return;
  state.jobs.set(id,{id,name:'正在处理'}); renderTasks();
  async function poll() {
    try {
      const job=await api('job?id='+encodeURIComponent(id)); state.jobs.set(id,job); renderTasks();
      if(['done','error'].includes(job.state)) {
        state.jobs.delete(id);renderTasks();
        if(job.state==='error')notice(job.error+'；可重新发起操作重试。',true);
        else if(job.result.sessions){state.sessions=job.result.sessions;renderSessions();notice(`已读取 ${state.sessions.length} 个单人会话，请选择对应联系人。`);}
        else {
          if(job.result.consultation_id && job.contact===state.contact)state.selected=job.result.consultation_id;
          if(job.result.memory_updates)notice(`已自动更新 ${job.result.memory_updates} 条记忆。`);
          else if(job.result.total===undefined && job.result.memory_updates===undefined && !job.result.skipped)notice(job.result.warning|| (job.result.candidate_count!==undefined?`已生成 ${job.result.candidate_count} 条候选，请审核。`:'已完成并保存。'));
          else if(job.result.imported)notice(importSummary(job.result));
        }
        if(!job.name.includes('监听') || job.result?.imported || job.state==='error')await refresh();
      } else setTimeout(poll,1800);
    } catch(e){state.jobs.delete(id);renderTasks();notice(e.message,true);}
  }
  poll();
}
async function download(path,filename) {
  const response=await fetch('/api/'+path,{headers:{'X-LoveShe-Token':token}});
  if(!response.ok){const v=await response.json();throw Error(v.error);}
  const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),3000);
}
document.addEventListener('input',event=>{if(event.target.closest('[data-form="ask"]'))saveAskDraft();});
document.addEventListener('change',event=>{if(event.target.closest('[data-form="ask"]'))saveAskDraft();});
document.addEventListener('change',event=>{if(event.target.id==='confirm-send')$('#send-preview').disabled=!event.target.checked;});
document.addEventListener('keydown',event=>{const item=event.target.closest('.consult-item');if(item && (event.key==='Enter'||event.key===' ')){event.preventDefault();item.click();}});
document.addEventListener('click',async event=>{
  const button=event.target.closest('button,[data-action]');if(!button)return;
  try {
    if(button.dataset.contact){state.contact=button.dataset.contact;state.selected=null;state.filters={query:'',start:'',end:'',offset:0,prefix:''};await refresh();return;}
    if(button.dataset.tab){saveAskDraft();state.selections.set(state.contact+':'+state.tab,state.selected);state.tab=button.dataset.tab;state.selected=state.selections.has(state.contact+':'+state.tab)?state.selections.get(state.contact+':'+state.tab):(state.detail.consultations.find(c=>state.tab==='replies'?c.mode==='advise':c.mode!=='advise')?.id||null);render();return;}
    const a=button.dataset.action;
    if(button.id==='add-contact'||a==='add-contact')return contactModal(true);
    if(button.id==='edit-profile'||a==='edit-profile')return contactModal();
    if(button.id==='close-modal'||a==='cancel-modal')return closeModal();
    if(a==='go-advisor'||a==='go-memory'){state.tab=a==='go-memory'?'memory':'advisor';render();return;}
    if(a==='discuss-reply'){const c=state.detail.consultations.find(c=>c.id===state.selected);state.tab='advisor';state.drafts.set(state.contact,{task:'consult',question:'请进一步帮我调整这次回复建议：\n'+c.answer,history:true,memory:true});state.selected=null;render();return;}
    if(a==='quick-analysis'){state.tab='advisor';render();$('#ask-task').value='analyze';$('#question').value='结合现有记录分析互动变化、关键事件和可能的沟通问题。请区分事实、推测和未知，并列出证据与下一步建议。';$('#question').focus();return;}
    if(a==='select-consult'){state.selected=Number(button.dataset.id);renderAdvisor();return;}
    if(a==='new-topic'){state.selected=null;state.drafts.delete(state.contact);renderAdvisor();return;}
    if(a==='follow-up'){$('#ask-parent').value=state.selected;saveAskDraft();$('#question').focus();return;}
    if(a==='older'||a==='newer'){state.filters.offset=Math.max(0,state.filters.offset+(a==='older'?100:-100));await loadMessages();return;}
    if(a==='clear-prefix'){state.filters.prefix='';state.filters.offset=0;render();return;}
    if(a==='evidence'){closeModal();state.filters={query:'',start:'',end:'',offset:0,prefix:button.dataset.prefix};state.tab='history';render();return;}
    if(a==='copy-id'){await navigator.clipboard.writeText(button.dataset.id);notice('消息 ID 已复制');return;}
    if(a==='add-memory')return memoryModal();
    if(a==='message-memory')return memoryModal({evidence_ids:[button.dataset.id],category:'event'});
    if(a==='edit-memory')return memoryModal(state.detail.memories.find(m=>m.id===Number(button.dataset.id)));
    if(a==='delete-memory')return showModal(`<h2>删除这条记忆？</h2><p>删除后不再用于新请求；版本记录仍可查看和恢复。</p><button class="danger" data-action="confirm-delete-memory" data-id="${button.dataset.id}">确认删除记忆</button>`);
    if(a==='confirm-delete-memory'){await api('memory',withContact({action:'delete',id:Number(button.dataset.id)}));closeModal();await refresh();notice('记忆已删除。旧咨询上下文将不再携带。');return;}
    if(a==='review-candidate'){const c=state.detail.candidates.find(c=>c.id===Number(button.dataset.id));return memoryModal({...c,evidence_ids:JSON.parse(c.evidence_ids)},true);}
    if(a==='reject-candidate'){await api('candidate',withContact({action:'reject',id:Number(button.dataset.id)}));await refresh();return;}
    if(a==='extract'||a==='extract-consult')return previewRequest({task:'extract',question:'从提供的历史消息、背景、咨询和反馈中提取最多五条重要事件或偏好记忆候选。只选有依据的信息，供用户审核。',history:true,memory:true,parent_id:a==='extract-consult'?state.selected:null});
    if(a==='send'){button.disabled=true;const result=await api('send',{preview_id:state.preview.preview_id,confirmed:$('#confirm-send').checked});closeModal();watchJob(result.job);return;}
    if(a==='edit-draft'){const c=state.detail.consultations.find(c=>c.id===state.selected);return showModal(`<h2>编辑并复制回复内容</h2><p class="small muted">修改仅保留在当前编辑框。采用后的实际内容可在反馈中记录。</p><textarea id="draft" class="draft-editor" aria-label="可编辑内容">${esc(c.answer)}</textarea><div class="field row end"><button data-action="copy-draft">复制编辑内容</button></div>`);}
    if(a==='copy-draft'){await navigator.clipboard.writeText($('#draft').value);notice('编辑后的内容已复制。');return;}
    if(a==='feedback')return showModal(`<h2>记录建议后的实际情况</h2><form data-form="feedback"><div class="field"><label for="feedback-outcome">采用情况</label><select id="feedback-outcome" name="outcome"><option value="observed">只记录观察 / 尚未发送</option><option value="used">直接采用</option><option value="edited">修改后采用</option><option value="rejected">未采用</option></select></div><div class="field"><label for="actual-reply">本人实际回复（可选）</label><textarea id="actual-reply" name="actual"></textarea></div><div class="field"><label for="contact-response">对方实际回应（可选）</label><textarea id="contact-response" name="response"></textarea></div><div class="field"><label for="feedback-note">说明与结果</label><textarea id="feedback-note" name="note" required placeholder="例如：尚未发送；或说明实际结果，避免把预期当成事实。"></textarea></div><button>保存反馈</button></form>`);
    if(a==='versions'){const versions=await api('versions?contact='+encodeURIComponent(state.contact));return showModal(`<h2>记忆修改历史</h2><p class="small muted">恢复也会形成新版本。删除记录不会参与模型召回。</p>${versions.map(v=>{const s=JSON.parse(v.snapshot);return `<div class="memory-item"><span class="metadata">记忆 #${v.memory_id} · ${esc(v.action)} · ${when(v.created_at)}</span><p>${esc(s.content)}</p><span class="badge ${s.status}">${statusName[s.status]}</span> ${s.deleted_at?'<span class="muted small">已删除状态</span>':`<button class="ghost" data-action="restore" data-version="${v.id}">恢复此版本</button>`}</div>`;}).join('')||empty('暂无修改历史','新版开始记录每次新增、修改与删除。')}`);}
    if(a==='restore'){await api('memory',withContact({action:'restore',version:Number(button.dataset.version)}));closeModal();await refresh();notice('已恢复所选版本。');return;}
    if(a==='memory-now'){const r=await api('memory-now',withContact({}));watchJob(r.job);if(r.already_running)notice('记忆正在更新，已显示现有任务进度，无需重复启动。');return;}
    if(a==='memory-runs'){const rows=await api('memory-runs?contact='+encodeURIComponent(state.contact));return showModal(`<h2>自动外发与更新记录</h2><p class="small muted">准备记录先于调用写入，失败不推进压缩进度。暂停后不发起新请求，在途请求可能已发送。</p>${rows.map(r=>`<details class="memory-item"><summary>#${r.id} · ${esc(r.status)} · ${when(r.created_at)}</summary>${r.error?`<p class="error">${esc(r.error)}</p>`:''}<label>本次完整拟发送资料</label><textarea class="preview" readonly>${esc(r.request)}</textarea><label>返回结果</label><textarea class="preview" readonly>${esc(r.result||'尚未应用')}</textarea></details>`).join('')||'<p>暂无记录。</p>'}`);}
    if(a==='sessions'){const r=await api('sessions',{});watchJob(r.job);return;}
    if(a==='export')return download('export?contact='+encodeURIComponent(state.contact),'LoveShe-联系人备份.json');
    if(a==='backup')return download('backup','LoveShe-完整备份.sqlite3');
    if(a==='delete-contact')return showModal(`<h2>删除 ${esc(state.detail.contact.display_name)} 的应用数据</h2><p>将删除聊天、档案、记忆版本、咨询和反馈。此前下载的备份与原始文件需要另行管理。</p><form data-form="delete"><label for="delete-name">输入完整备注名确认</label><input id="delete-name" name="confirmation" autocomplete="off" required><div class="field row end"><button class="danger">永久删除应用内数据</button></div></form>`);
  } catch(e){notice(e.message,true);if(button.id==='send-preview')button.disabled=false;}
});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.dataset.form)return;event.preventDefault();
  const submit=$('button[type="submit"],button:not([type])',form);if(submit)submit.disabled=true;
  const data=Object.fromEntries(new FormData(form));
  try {
    switch(form.dataset.form){
      case 'automation': {const body={...data,contact:state.contact};for(const k of ['capture_enabled','auto_memory','auto_overwrite','consent'])body[k]=form.elements[k].checked;await api('automation',body);await refresh();notice('自动跟进设置已保存。');break;}
      case 'profile': {const r=await api('contact',data);state.contact=r.contact;closeModal();await refresh();notice('联系人档案已保存。');break;}
      case 'history': state.filters={...data,offset:0,prefix:/^[a-f0-9]{8,64}$/i.test(data.query)?data.query:'',query:/^[a-f0-9]{8,64}$/i.test(data.query)?'':data.query};await loadMessages();break;
      case 'ask': await previewRequest({...data,parent_id:data.parent_id?Number(data.parent_id):null,history:form.elements.history.checked,memory:form.elements.memory.checked});break;
      case 'memory': await api('memory',withContact({...data,id:data.id?Number(data.id):null,action:data.id?'edit':'add',evidence_ids:JSON.parse(data.evidence)}));closeModal();await refresh();notice('记忆已保存。');break;
      case 'candidate': await api('candidate',withContact({id:Number(data.id),action:'accept',category:data.category,content:data.content,status:data.status}));closeModal();await refresh();notice('候选已保存到长期记忆。');break;
      case 'feedback': await api('feedback',withContact({consultation_id:state.selected,outcome:data.outcome,note:`说明：${data.note}\n本人实际回复：${data.actual||'未填写'}\n对方实际回应：${data.response||'未填写'}`}));closeModal();await refresh();notice('反馈已保存，后续咨询可以使用。');break;
      case 'import': {const file=form.elements.file.files[0];if(file.size>31*1024*1024)throw Error('文件超过31MB，请按时间拆分后导入。');const contact=state.contact;const payload=JSON.parse(await file.text());const r=await api('import',{contact,payload});await refresh();notice(importSummary(r));break;}
      case 'bind': await api('bind',withContact(data));await refresh();notice('已绑定，请选择日期更新。');break;
      case 'sync': {const r=await api('sync',withContact(data));watchJob(r.job);break;}
      case 'delete': await api('delete',withContact(data));closeModal();state.selected=null;await refresh();notice('联系人应用数据已删除。');break;
    }
  } catch(e){notice(e.message,true);}finally{if(submit)submit.disabled=false;}
});
refresh().catch(e=>notice('启动失败：'+e.message,true));

setInterval(async()=>{if(document.hidden)return;try{await loadState();if(['data','memory'].includes(state.tab)&&!$('#modal').open&&!document.activeElement?.closest('form'))await loadContact();}catch{}},8000);
