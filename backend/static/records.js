'use strict';
// Read-only history UI. Viewing a session never starts a workout or model job.
const exerciseNames={squat:'深蹲',push_up:'俯卧撑',lunge:'弓步蹲',bicep_curl:'二头弯举',plank:'平板支撑',pull_up:'引体向上',dumbbell_row:'哑铃划船',shoulder_press:'肩推'};
const workspace=document.getElementById('workspace'),main=document.getElementById('main');
const state={token:localStorage.getItem('auth_token')||'',epoch:0,user:null,filter:'all',exercises:[],rows:[],next:null,
 listMode:'loading',listError:'',moreLoading:false,selected:null,detail:null,detailMode:'idle',report:null,reportMode:'idle',
 detailOpen:false,listScroll:0,pageScroll:0,listSeq:0,detailSeq:0,loginMessage:''};
const pending=new Set();
let listRequest,detailRequest,reportRequest;
let lastHistory='',lastDetail='';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon=n=>`<svg aria-hidden="true"><use href="#i-${n}"/></svg>`;
const name=r=>exerciseNames[r.exercise_type]||r.exercise_type||'未命名动作';
const numeric=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
const number=v=>numeric(v)?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:1}).format(Number(v)):'—';
const date=(v,full=false)=>{if(!numeric(v))return '时间未记录';const d=new Date(Number(v)*1000);if(!Number.isFinite(d.getTime()))return '时间未记录';return new Intl.DateTimeFormat('zh-CN',full?{year:'numeric',month:'long',day:'numeric',hour:'2-digit',minute:'2-digit'}:{month:'long',day:'numeric'}).format(d);};
const seconds=r=>numeric(r.start_time)&&numeric(r.end_time)&&Number(r.end_time)>=Number(r.start_time)?Math.floor(Number(r.end_time)-Number(r.start_time)):null;
const duration=r=>{const s=seconds(r);return s===null?'—':`${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;};
const cancel=()=>{const e=new Error('cancelled');e.name='AbortError';return e;};
function resetAuth(message=''){
 state.epoch++;state.listSeq++;state.detailSeq++;pending.forEach(c=>c.abort());pending.clear();
 state.token='';state.user=null;state.rows=[];state.exercises=[];state.selected=null;state.detail=null;state.report=null;
 state.listMode='idle';state.detailMode='idle';state.reportMode='idle';state.filter='all';state.next=null;
 state.moreLoading=false;state.listError='';state.detailOpen=false;state.loginMessage=message;
 localStorage.removeItem('auth_token');localStorage.removeItem('auth_username');
 history.replaceState(null,'',location.pathname+location.search);render();
}
async function api(path,{method='GET',body,controller=new AbortController(),auth=true}={}){
 const epoch=state.epoch;pending.add(controller);
 const timer=setTimeout(()=>controller.abort('timeout'),15000);
 try{
  const response=await fetch(path,{method,signal:controller.signal,cache:'no-store',headers:{...(auth?{Authorization:`Bearer ${state.token}`}:{}) ,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
  if(epoch!==state.epoch)throw cancel();
  if(response.status===401&&auth){resetAuth('登录已过期，请重新登录。');throw cancel();}
  if(!response.ok){const e=new Error('request failed');e.status=response.status;throw e;}
  const data=await response.json();if(epoch!==state.epoch)throw cancel();return data;
 }catch(e){if(controller.signal.reason==='timeout')throw new Error('timeout');throw e;}
 finally{clearTimeout(timer);pending.delete(controller);}
}
function shellState(title,copy,action,label,kind='list'){
 return `<div class="list-state"><div class="state-icon">${icon(kind)}</div><h3>${esc(title)}</h3><p>${esc(copy)}</p>${action?`<button class="state-button secondary" data-action="${action}">${esc(label)}</button>`:''}</div>`;
}
function loginPanel(){return `<section class="login-panel" aria-labelledby="login-title"><div class="state-icon">${icon('lock')}</div><h2 id="login-title">你的努力，只对你可见</h2><p>登录后查看自己的训练记录与教练报告。</p>${state.loginMessage?`<p class="session-message" role="status">${esc(state.loginMessage)}</p>`:''}<form class="login-form" id="login-form"><label for="username">用户名</label><input id="username" name="username" autocomplete="username" required maxlength="100"><label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required><p class="form-error" id="login-error" role="alert" hidden></p><button class="state-button" type="submit">登录并查看记录${icon('arrow')}</button></form><span class="login-note">使用 Smart Fitness 已有账号。<a href="/static/index.html">前往实时训练 / 注册</a></span></section>`;}
function option(v,label){return `<option value="${esc(v)}"${v===state.filter?' selected':''}>${esc(label)}</option>`;}
function row(r){return `<button class="record-row" data-record="${esc(r.session_id)}" aria-current="${state.selected===r.session_id}" aria-label="查看${esc(date(r.start_time))}${esc(name(r))}训练详情"><span class="exercise-icon">${icon('dumbbell')}</span><span class="record-main"><span class="record-top"><strong>${esc(name(r))}</strong><span class="row-date">${esc(date(r.start_time))}</span></span><span class="record-data"><span>${number(r.total_reps)} 次</span><span class="dot">·</span><span>${seconds(r)===null?'时长未记录':`${Math.floor(seconds(r)/60)} 分钟`}</span><span class="dot">·</span><span>${numeric(r.avg_form_score)?`${number(r.avg_form_score)} 分`:'暂无评分'}</span></span><span class="report-indicator${r.has_report?'':' no-report'}">${icon(r.has_report?'report':'clock')}${r.has_report?'已有 AI 报告':'尚无 AI 报告'}</span></span><svg class="row-arrow" aria-hidden="true"><use href="#i-arrow"/></svg></button>`;}
function historyPanel(){
 let body,footer='';
 if(state.listMode==='loading')body='<div role="status" class="list-state">正在读取训练记录…</div>'+Array.from({length:3},()=>'<div class="skeleton-row"><span class="skeleton-block"></span><span class="skeleton-lines"><span class="skeleton-line" style="display:block"></span><span class="skeleton-line short" style="display:block"></span></span></div>').join('');
 else if(state.listMode==='error')body=shellState('训练记录暂时读不到','请检查网络后重试，已选动作会保留。','retry-list','重新加载','alert');
 else if(!state.rows.length)body=state.filter==='all'?shellState('还没有训练记录','完成一次训练后，每一份努力都会留在这里。'):shellState('这个动作还没有记录','试试其他动作，或查看全部训练。','clear-filter','清除筛选');
 else{body=state.rows.map(row).join('');footer=`${state.listError?'<div class="inline-error" role="alert">更多记录读取失败，已加载记录仍可查看。请再次加载。</div>':''}<div class="history-footer"><button class="load-more" id="load-more"${state.next===null||state.moreLoading?' disabled':''}>${state.moreLoading?'正在加载…':state.next===null?'已显示全部记录':'加载更多'}</button></div>`;}
 const options=[...new Set([...Object.keys(exerciseNames),...state.exercises,...(state.filter==='all'?[]:[state.filter])])];
 return `<section class="history-card" aria-label="训练记录列表"><div class="history-header"><div class="history-title"><h2>最近训练</h2><span class="record-count" role="status" aria-live="polite">${state.listMode==='ready'?`已加载 ${state.rows.length} 条`:''}</span></div><div class="filter">${icon('filter')}<label class="sr-only" for="exercise-filter">按动作筛选</label><select id="exercise-filter">${option('all','全部动作')}${options.map(v=>option(v,exerciseNames[v]||v)).join('')}</select>${icon('chevron')}</div></div><div class="list-label"><span>训练内容</span><span>按时间倒序</span></div><div class="record-list" id="record-list"${state.listMode==='loading'?' aria-busy="true"':''}>${body}</div>${footer}</section>`;
}
function listOf(title,items){const list=Array.isArray(items)?items.filter(x=>typeof x==='string'&&x.trim()):[];return list.length?`<h4>${esc(title)}</h4><ul class="tips">${list.map(x=>`<li>${icon('check')}<span>${esc(x)}</span></li>`).join('')}</ul>`:'';}
function paragraph(title,text){return typeof text==='string'&&text.trim()?`<h4>${esc(title)}</h4><p>${esc(text)}</p>`:'';}
function reportContent(){
 if(state.reportMode==='loading')return '<div class="no-report-panel" role="status" aria-busy="true"><p>正在读取已保存的报告…</p></div>';
 if(state.reportMode==='error')return `<div class="no-report-panel" role="status"><div class="state-icon">${icon('alert')}</div><h4>报告暂时无法读取</h4><p>训练数据可以正常查看。你可以稍后再试一次。</p><button class="state-button secondary" data-action="retry-report">重新读取报告</button></div>`;
 if(!state.report)return `<div class="no-report-panel"><div class="state-icon">${icon('report')}</div><h4>这次训练还没有 AI 报告</h4><p>训练数据已经保存，可以先回看上方的次数、时长和评分。</p></div>`;
 const r=state.report.report||{},a=r.overall_assessment||{},g=r.guidance||{};
 const issues=Array.isArray(a.common_issues)?a.common_issues.map(x=>typeof x==='string'?x:[x?.issue,x?.evidence].filter(v=>typeof v==='string').join('：')):[];
 const content=listOf('本次表现',a.strengths)+listOf('需要关注',issues)+listOf('动作一致性',a.inconsistencies)+listOf('即时调整',g.immediate_corrections)+listOf('下次训练关注',g.next_session_focus)+paragraph('进阶与调整',g.progression_or_regression)+listOf('注意事项',g.cautions)+listOf('逐次动作记录',r.rep_by_rep_notes)+listOf('数据局限',r.data_gaps);
 return `<div class="report-body">${content||'<h4>报告内容暂不可展示</h4><p>这份存档没有可识别的分析内容，可展开原始记录核对。</p>'}<details><summary>查看已保存的原始内容</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details><div class="report-foot">${icon('lock')}已保存的教练建议，打开页面不会重新生成。</div></div>`;
}
function detailPanel(){
 const back=`<button class="mobile-back" id="back-to-list">${icon('back')}返回训练记录</button>`;
 if(state.detailMode==='loading')return `<section class="detail-pane loading-detail" aria-label="正在加载训练详情" aria-busy="true">${back}<p role="status">正在读取训练详情…</p><div class="skeleton-line"></div><div class="skeleton-line short"></div></section>`;
 if(state.detailMode==='error'||state.detailMode==='missing')return `<section class="detail-pane" aria-label="训练详情">${back}${state.detailMode==='missing'?shellState('找不到这条训练','记录可能已删除，或不属于当前账号。'):shellState('训练详情暂时读不到','列表仍可使用，请重试。','retry-detail','重新读取详情','alert')}</section>`;
 const r=state.detail;if(!r)return `<section class="detail-pane quiet-detail" aria-label="训练详情"><div>${icon('report')}<h2>每一次训练，都值得回看</h2><p>选择一条记录，查看训练指标与报告。</p></div></section>`;
 const status={completed:'已完成',active:'训练中',ended:'已结束',cancelled:'已取消'}[r.status]||'状态未标记';
 return `<article class="detail-pane" aria-labelledby="detail-title">${back}<div class="detail-heading"><span class="detail-exercise">${icon('dumbbell')}</span><div><div class="detail-title-line"><h2 id="detail-title" tabindex="-1">${esc(name(r))}训练</h2><span class="complete-label">${status}</span></div><p class="detail-date">${icon('calendar')}${esc(date(r.start_time,true))}</p></div></div><div class="metrics" aria-label="单次训练指标"><div class="metric"><strong>${number(r.total_reps)}<small>次</small></strong><span class="metric-label">${numeric(r.total_reps)?'总次数':'次数未记录'}</span></div><div class="metric"><strong class="duration">${duration(r)}</strong><span class="metric-label">${seconds(r)===null?'时长未记录':'训练时长 · 分:秒'}</span></div><div class="metric"><strong>${number(r.avg_form_score)}<small>${numeric(r.avg_form_score)?'分':''}</small></strong><span class="metric-label">${numeric(r.avg_form_score)?'平均评分':'暂无评分'}</span></div></div><section aria-label="AI 教练报告"><div class="report-title-row"><div class="report-title">${icon('report')}<h3>AI 教练报告</h3></div>${state.report?`<span class="saved-label">${icon('check')}已保存</span>`:''}</div>${state.report?`<p class="report-meta">${esc(date(state.report.created_at,true))} · 最新一份存档</p>`:''}${reportContent()}</section></article>`;
}
function render(focus){
 const activeId=document.activeElement?.id;
 const preserveFocus=['exercise-filter','detail-title','load-more'].includes(activeId)?activeId:null;
 document.getElementById('account').hidden=!state.user;
 document.getElementById('account-name').textContent=state.user?.display_name||state.user?.username||'';
 workspace.dataset.mobileDetail=String(state.detailOpen&&!!state.user);main.classList.toggle('is-detail',state.detailOpen&&!!state.user);
 if(state.user){
  // Keep list nodes stable while detail/report requests complete, so clicks,
  // scroll and keyboard focus are not interrupted by unrelated async updates.
  if(!document.getElementById('history-slot')){workspace.innerHTML='<div id="history-slot"></div><div id="detail-slot"></div>';lastHistory='';lastDetail='';}
  const history=historyPanel(),detail=detailPanel();
  if(history!==lastHistory){const slot=document.getElementById('history-slot');slot.innerHTML=history;lastHistory=history;bind(slot);}
  if(detail!==lastDetail){const slot=document.getElementById('detail-slot');slot.innerHTML=detail;lastDetail=detail;bind(slot);}
 }else{workspace.innerHTML=state.token?'<section class="login-panel" aria-busy="true"><p role="status">正在验证登录状态…</p></section>':loginPanel();bind(workspace);}
 const list=document.getElementById('record-list');if(list)list.scrollTop=state.listScroll;
 if(focus||preserveFocus)document.getElementById(focus||preserveFocus)?.focus({preventScroll:true});
}
async function loadList(more=false){
 listRequest?.abort();listRequest=new AbortController();const seq=++state.listSeq;
 if(more)state.moreLoading=true;else{state.rows=[];state.next=null;state.listMode='loading';}
 state.listError='';render();
 try{
  const query=new URLSearchParams({limit:'20',offset:String(more?state.next:0)});if(state.filter!=='all')query.set('exercise',state.filter);
  const data=await api('/api/v2/sessions/history?'+query,{controller:listRequest});
  if(seq!==state.listSeq) return;
  if(!Array.isArray(data.sessions))throw new Error('invalid history');
  const previous=state.rows.length;
  state.rows=[...new Map([...state.rows,...data.sessions].map(r=>[r.session_id,r])).values()];
  state.exercises=Array.isArray(data.exercises)?data.exercises:[];
  state.next=data.has_more&&Number.isInteger(data.next_offset)?data.next_offset:null;
  state.listMode='ready';state.moreLoading=false;render();
  if(more)document.querySelectorAll('[data-record]')[previous]?.focus({preventScroll:true});
  if(!more){const id=new URLSearchParams(location.hash.slice(1)).get('session');if(id){state.detailOpen=true;choose(id);}else if(state.rows[0])choose(state.rows[0].session_id);}
 }catch(e){if(seq!==state.listSeq||e.name==='AbortError')return;state.moreLoading=false;if(more)state.listError='error';else state.listMode='error';render();}
}
async function choose(id){
 detailRequest?.abort();reportRequest?.abort();detailRequest=new AbortController();const seq=++state.detailSeq;
 state.selected=id;state.detail=null;state.report=null;state.detailMode='loading';state.reportMode='idle';render();
 try{
  const data=await api('/api/v2/sessions/detail/'+encodeURIComponent(id),{controller:detailRequest});
  if(seq!==state.detailSeq)return;if(data.session?.session_id!==id)throw new Error('session mismatch');
  state.detail=data.session;state.detailMode='ready';state.reportMode='loading';render(state.detailOpen?'detail-title':null);loadReport(id,seq);
 }catch(e){if(seq!==state.detailSeq||e.name==='AbortError')return;state.detailMode=e.status===404?'missing':'error';render();}
}
async function loadReport(id=state.selected,seq=state.detailSeq){
 reportRequest?.abort();reportRequest=new AbortController();const controller=reportRequest;
 state.report=null;state.reportMode='loading';render();
 try{
  const data=await api('/api/v2/ai_coach/reports?'+new URLSearchParams({session_id:id,limit:'1'}),{controller});
  if(seq!==state.detailSeq)return;if(!data.ok||!Array.isArray(data.reports))throw new Error('invalid reports');
  const latest=data.reports[0];let result=null;
  if(latest){result=await api('/api/v2/ai_coach/reports/'+encodeURIComponent(latest.report_id),{controller});if(!result.ok||result.session_id!==id)throw new Error('report mismatch');}
  if(seq!==state.detailSeq)return;state.report=result;state.reportMode='ready';const row=state.rows.find(r=>r.session_id===id);if(row)row.has_report=Boolean(result);render();
 }catch(e){if(seq!==state.detailSeq||e.name==='AbortError')return;state.reportMode='error';render();}
}
function selectRecord(id){state.pageScroll=scrollY;history.pushState({sfDetail:true},'',`${location.pathname}${location.search}#session=${encodeURIComponent(id)}`);state.detailOpen=true;choose(id);if(matchMedia('(max-width:760px)').matches)scrollTo({top:0,behavior:'instant'});}
function restoreList(){requestAnimationFrame(()=>{const list=document.getElementById('record-list');if(list)list.scrollTop=state.listScroll;if(matchMedia('(max-width:760px)').matches)scrollTo({top:state.pageScroll,behavior:'instant'});});}
function backToList(){if(history.state?.sfDetail)history.back();else{history.replaceState(null,'',location.pathname+location.search);state.detailOpen=false;render();restoreList();}}
function changeFilter(value){state.filter=value;state.listScroll=0;state.detailOpen=false;state.detailSeq++;detailRequest?.abort();reportRequest?.abort();state.selected=null;state.detail=null;state.report=null;state.detailMode='idle';history.replaceState(null,'',location.pathname+location.search);loadList();document.getElementById('exercise-filter')?.focus();}
async function login(event){
 event.preventDefault();const form=event.currentTarget,button=form.querySelector('button'),error=document.getElementById('login-error');button.disabled=true;button.textContent='正在登录…';error.hidden=true;
 try{const data=await api('/api/auth/login',{auth:false,method:'POST',body:{username:form.username.value.trim(),password:form.password.value}});if(!data.ok||!data.token){error.textContent=data.message||'用户名或密码不正确';error.hidden=false;return;}
  state.token=data.token;localStorage.setItem('auth_token',data.token);localStorage.setItem('auth_username',data.username||'');await boot();
 }catch(e){if(e.name!=='AbortError'){error.textContent='暂时无法登录，请检查网络后重试。';error.hidden=false;}}
 finally{button.disabled=false;button.innerHTML=`登录并查看记录${icon('arrow')}`;}
}
async function boot(){
 if(!state.token){render();return;}render();
 try{const data=await api('/api/auth/profile');if(!data.ok||!data.user){resetAuth('账号暂不可用，请重新登录。');return;}state.user=data.user;loadList();}
 catch(e){if(e.name==='AbortError')return;resetAuth('暂时无法验证账号，请重试登录。');}
}
function bind(root){
 root.querySelector('#login-form')?.addEventListener('submit',login);
 root.querySelector('#exercise-filter')?.addEventListener('change',e=>changeFilter(e.target.value));
 root.querySelectorAll('[data-record]').forEach(b=>b.addEventListener('click',()=>selectRecord(b.dataset.record)));
 root.querySelector('#record-list')?.addEventListener('scroll',e=>{state.listScroll=e.target.scrollTop;});
 root.querySelector('#back-to-list')?.addEventListener('click',backToList);
 root.querySelector('#load-more')?.addEventListener('click',()=>{if(!state.moreLoading&&state.next!==null)loadList(true);});
 root.querySelectorAll('[data-action]').forEach(b=>b.addEventListener('click',()=>{const a=b.dataset.action;if(a==='clear-filter')changeFilter('all');if(a==='retry-list')loadList();if(a==='retry-detail')choose(state.selected);if(a==='retry-report')loadReport();}));
}
document.getElementById('logout').addEventListener('click',()=>{resetAuth('已退出，训练记录已从页面清除。');document.getElementById('username')?.focus();});
document.getElementById('records-nav').addEventListener('click',()=>{if(state.detailOpen)backToList();else scrollTo({top:0,behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth'});});
addEventListener('popstate',()=>{if(!state.user)return;const id=new URLSearchParams(location.hash.slice(1)).get('session');state.detailOpen=Boolean(id);if(id&&id!==state.selected)choose(id);else render();if(!id)restoreList();});
addEventListener('storage',event=>{if(event.key==='auth_token'&&event.newValue!==state.token)resetAuth('登录状态已在其他页面变更，请重新登录。');});
boot();
