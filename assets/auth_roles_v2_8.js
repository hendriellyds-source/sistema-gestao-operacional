(function(){
"use strict";

const V28_ROLE_NAMES={
  diretor:"Diretor",
  adm:"Administrativo",
  helena:"Projetos",
  aline:"Processos",
  financeiro:"Financeiro"
};
const V28_STATUS=["Nova","Em andamento","Aguardando diretor","Concluída"];

function v28Esc(value){
  return String(value??"").replace(/[&<>"']/g,char=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  })[char]);
}
function v28Date(value){
  if(!value)return "Sem prazo";
  const [y,m,d]=String(value).slice(0,10).split("-");
  return y&&m&&d?`${d}/${m}/${y}`:String(value);
}
function v28Due(value){
  if(!value)return {label:"Sem prazo",css:"normal",days:null};
  const due=new Date(`${value}T12:00:00`),today=new Date();today.setHours(12,0,0,0);
  const days=Math.round((due-today)/86400000);
  if(days<0)return {label:`VENCIDA há ${Math.abs(days)} dia(s)`,css:"overdue",days};
  if(days<=2)return {label:days===0?"Vence hoje":`Faltam ${days} dia(s)`,css:"urgent",days};
  if(days<=10)return {label:`Faltam ${days} dias`,css:"near",days};
  return {label:`Faltam ${days} dias`,css:"normal",days};
}
function v28StatusClass(status){
  if(status==="Em andamento")return "progress";
  if(status==="Aguardando diretor")return "waiting";
  if(status==="Concluída")return "done";
  return "new";
}
async function v28Api(path,options={}){
  const response=await fetch(path,{...options,headers:{"Content-Type":"application/json",...(options.headers||{})}});
  if(response.status===401){location.href="/login";throw new Error("Sessão expirada.")}
  const result=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(result.detail||"Não foi possível concluir a operação.");
  return result;
}

window.logoutSystemV28=async function(){
  try{await v28Api("/api/auth/logout",{method:"POST"})}catch(_){}
  location.href="/login";
};

function v28Card(item){
  const due=v28Due(item.due_date);
  const director=CURRENT_USER.role==="diretor";
  const received=item.target_role===CURRENT_USER.role;
  const sent=item.created_by===CURRENT_USER.username;
  const canManage=director||received||(CURRENT_USER.role==="adm"&&sent);
  const canDelete=director||(sent&&item.status==="Nova");
  const direction=sent&&!received?"Enviada":received&&!sent?"Recebida":sent&&received?"Própria":"Acompanhamento";
  return `<article class="v28-demand-card">
    <div class="v28-demand-head"><div><span class="v28-target">${v28Esc(direction)} · ${v28Esc(V28_ROLE_NAMES[item.target_role]||item.target_role)}</span><h3>${v28Esc(item.title)}</h3></div><span class="v28-status ${v28StatusClass(item.status)}">${v28Esc(item.status==="Aguardando diretor"?"Aguardando retorno":item.status)}</span></div>
    <p>${v28Esc(item.description||"Sem descrição.")}</p>
    <div class="v28-demand-meta">
      <div><small>Prazo</small><span class="v28-deadline ${due.css}">${v28Esc(due.label)}</span></div>
      <div><small>Prioridade</small><strong>${v28Esc(item.priority)}</strong></div>
      <div><small>Origem</small><strong>${v28Esc(V28_ROLE_NAMES[item.created_by]||item.created_by)}</strong></div>
      <div><small>Destino</small><strong>${v28Esc(V28_ROLE_NAMES[item.target_role]||item.target_role)}</strong></div>
    </div>
    ${item.response_note?`<div class="v28-role-note"><b>Retorno:</b> ${v28Esc(item.response_note)}</div>`:""}
    <div class="v28-demand-actions">
      ${canManage&&item.status!=="Em andamento"&&item.status!=="Concluída"?`<button class="btn btn-primary" onclick="updateInternalDemandV28(${item.id},'Em andamento')">Iniciar</button>`:""}
      ${canManage&&item.status!=="Aguardando diretor"&&item.status!=="Concluída"?`<button class="btn btn-light" onclick="respondInternalDemandV28(${item.id},'Aguardando diretor')">Enviar retorno</button>`:""}
      ${canManage&&item.status!=="Concluída"?`<button class="btn btn-light" onclick="respondInternalDemandV28(${item.id},'Concluída')">Concluir</button>`:""}
      ${!canManage&&sent?`<span class="v28-status waiting">Aguardando ${v28Esc(V28_ROLE_NAMES[item.target_role]||item.target_role)}</span>`:""}
      ${canDelete?`<button class="btn btn-danger-outline" onclick="deleteInternalDemandV28(${item.id})">Excluir</button>`:""}
    </div>
  </article>`;
}

window.internalDemands=async function(){
  const role=CURRENT_USER.role;
  const isDirector=role==="diretor";
  const isAdmin=role==="adm";
  setHeader(
    isDirector?"Diretor — Demandas aos setores":isAdmin?"Administrativo — Central de apoio":"Demandas e apoio administrativo",
    isDirector?"Distribuição e acompanhamento das solicitações internas.":isAdmin?"Receba dúvidas de todos os setores e encaminhe demandas.":"Acompanhe o que recebeu e solicite ajuda ao Administrativo."
  );
  const content=document.getElementById("content");
  content.innerHTML=`<div class="card">Carregando demandas…</div>`;
  try{
    const rows=await v28Api("/api/director-demands");
    const active=rows.filter(item=>item.status!=="Concluída");
    const received=isDirector?active:active.filter(item=>item.target_role===role);
    const sent=isDirector?active.filter(item=>item.created_by===CURRENT_USER.username):active.filter(item=>item.created_by===CURRENT_USER.username);
    const overdue=received.filter(item=>v28Due(item.due_date).days<0).length;
    const newCount=received.filter(item=>item.status==="Nova").length;
    content.innerHTML=`<div class="v28-demand-shell">
      <div class="v28-demand-hero">
        <div><small>${isDirector?"DIREÇÃO":isAdmin?"PONTE ENTRE SETORES":"CAIXA DE ENTRADA E SAÍDA"}</small><h2>${isDirector?"Demandas para todos os setores":isAdmin?"Central de apoio administrativo":`Demandas — ${v28Esc(CURRENT_USER.display_name)}`}</h2><p>${isDirector?"Envie tarefas para Administrativo, Projetos, Processos e Financeiro.":isAdmin?"Receba pedidos de ajuda e distribua tarefas para os demais setores.":"Receba suas demandas e encaminhe dúvidas ou pedidos de ajuda ao Administrativo."}</p></div>
        ${CURRENT_USER.can_dispatch_demands?`<button class="btn btn-primary" onclick="newInternalDemandV28()">${isDirector||isAdmin?"Nova demanda":"Solicitar ajuda ao Administrativo"}</button>`:""}
      </div>
      <div class="grid grid-4">
        ${metric("Recebidas",received.length,"demandas ativas")}
        ${metric("Enviadas",sent.length,"aguardando ou em execução")}
        ${metric("Novas",newCount,"aguardando início",newCount?"warning":"success")}
        ${metric("Vencidas",overdue,"prazo ultrapassado",overdue?"danger":"success")}
      </div>
      <div class="v28-demand-grid">${rows.length?rows.map(v28Card).join(""):`<div class="card">Nenhuma demanda pendente.</div>`}</div>
    </div>`;
    refreshInternalDemandBadgeV28(rows);
  }catch(error){content.innerHTML=`<div class="card"><h3>Não foi possível abrir as demandas</h3><p>${v28Esc(error.message)}</p></div>`}
};

window.newInternalDemandV28=function(){
  if(!CURRENT_USER.can_dispatch_demands)return;
  const targets=Array.isArray(CURRENT_USER.dispatch_targets)?CURRENT_USER.dispatch_targets:[];
  modal(`<h2>${CURRENT_USER.role==="helena"||CURRENT_USER.role==="aline"||CURRENT_USER.role==="financeiro"?"Solicitar ajuda ao Administrativo":"Nova demanda interna"}</h2><form id="v28DemandForm" class="form-grid">
    <div class="form-group"><label>Setor de destino *</label><select name="target_role" required>${targets.map(value=>`<option value="${value}">${v28Esc(V28_ROLE_NAMES[value]||value)}</option>`).join("")}</select></div>
    <div class="form-group"><label>Prioridade</label><select name="priority"><option>Normal</option><option>Alta</option><option>Urgente</option></select></div>
    <div class="form-group" style="grid-column:1/-1"><label>Título *</label><input name="title" required></div>
    <div class="form-group"><label>Prazo</label><input name="due_date" type="date"></div>
    <div class="form-group" style="grid-column:1/-1"><label>Descrição e instruções</label><textarea name="description"></textarea></div>
    <div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Enviar demanda</button></div>
  </form>`);
  document.getElementById("v28DemandForm").onsubmit=async event=>{
    event.preventDefault();const data=Object.fromEntries(new FormData(event.target).entries());
    try{await v28Api("/api/director-demands",{method:"POST",body:JSON.stringify(data)});closeModal();showToast("Demanda enviada ao setor.");internalDemands()}catch(error){alert(error.message)}
  };
};

window.updateInternalDemandV28=async function(id,status){
  try{
    await v28Api(`/api/director-demands/${id}`,{method:"PUT",body:JSON.stringify({status,response_note:""})});
    showToast(`Demanda atualizada: ${status}.`);internalDemands();
  }catch(error){alert(error.message)}
};

window.respondInternalDemandV28=function(id,status){
  modal(`<h2>${status==="Concluída"?"Concluir demanda":"Enviar retorno"}</h2><form id="v28ResponseForm" class="form-grid"><div class="form-group" style="grid-column:1/-1"><label>Retorno / observação</label><textarea name="response_note"></textarea></div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar retorno</button></div></form>`);
  document.getElementById("v28ResponseForm").onsubmit=async event=>{
    event.preventDefault();
    try{await v28Api(`/api/director-demands/${id}`,{method:"PUT",body:JSON.stringify({status,response_note:event.target.elements.response_note.value})});closeModal();showToast("Retorno enviado.");internalDemands()}catch(error){alert(error.message)}
  };
};

window.deleteInternalDemandV28=async function(id){
  if(!confirm("Excluir definitivamente esta demanda interna?"))return;
  try{await v28Api(`/api/director-demands/${id}`,{method:"DELETE"});showToast("Demanda excluída.");internalDemands()}catch(error){alert(error.message)}
};

window.refreshInternalDemandBadgeV28=async function(rows=null){
  try{
    rows=rows||await v28Api("/api/director-demands");
    const relevant=CURRENT_USER.role==="diretor"?rows:rows.filter(item=>item.target_role===CURRENT_USER.role);
    const count=relevant.filter(item=>item.status==="Nova"||v28Due(item.due_date).days<0).length;
    document.querySelectorAll(".internal-demand-badge").forEach(badge=>{
      badge.textContent=String(count);badge.classList.toggle("hidden",!count);
    });
    document.querySelectorAll('[data-page="internalDemands"] .nav-label').forEach(label=>{
      label.textContent=CURRENT_USER.role==="diretor"?"Distribuir demandas":CURRENT_USER.role==="adm"?"Central de apoio":"Demandas e ajuda";
    });
  }catch(_){}
};

setTimeout(()=>refreshInternalDemandBadgeV28(),1200);
setInterval(()=>refreshInternalDemandBadgeV28(),60000);
})();
