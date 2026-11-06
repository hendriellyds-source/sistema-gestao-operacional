(function(){
"use strict";

const G26_KIND="CONSTRUTORA DEMO_CONFERENCE";
const G26_STATUSES=[
  "Aguardando conferência",
  "Em conferência",
  "Correção solicitada",
  "Aguardando Projetista",
  "Conferida",
  "Concluído"
];
const G26={
  demands:[],
  search:"",
  status:"",
  selectedId:null
};

function g26Esc(value){
  return String(value??"").replace(/[&<>"']/g,char=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  })[char]);
}
function g26Norm(value){
  return String(value||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"")
    .replace(/[^a-zA-Z0-9]+/g," ").trim().toUpperCase();
}
function g26Date(value){
  if(!value)return "Sem prazo";
  const [y,m,d]=String(value).slice(0,10).split("-");
  return y&&m&&d?`${d}/${m}/${y}`:String(value);
}
function g26Deadline(value){
  if(!value)return {label:"Sem prazo",css:"normal",days:null};
  const due=new Date(`${String(value).slice(0,10)}T12:00:00`);
  const today=new Date();today.setHours(12,0,0,0);
  const days=Math.round((due-today)/86400000);
  if(days<0)return {label:`VENCIDO há ${Math.abs(days)} dia(s)`,css:"overdue",days};
  if(days<=2)return {label:days===0?"PRAZO A VENCER — hoje":`PRAZO A VENCER — ${days} dia(s)`,css:"urgent",days};
  if(days<=10)return {label:`Faltam ${days} dias`,css:"near",days};
  return {label:`Faltam ${days} dias`,css:"normal",days};
}
async function g26Api(path,options={}){
  const headers={"Content-Type":"application/json","X-Gestão Operacional-Profile":"Projetista",...(options.headers||{})};
  const response=await fetch(`/api/projects${path}`,{...options,headers});
  const body=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(body.detail||"Não foi possível concluir a operação.");
  return body;
}
function g26CompanyId(){
  const source=typeof data!=="undefined"?data:{companies:[],works:[]};
  const company=(source.companies||[]).find(item=>g26Norm(item.name).includes("CONSTRUTORA DEMO"));
  return company?.id??null;
}
function g26Works(){
  const companyId=g26CompanyId();
  const source=typeof data!=="undefined"?data:{companies:[],works:[]};
  return (source.works||[])
    .filter(item=>Number(item.companyId)===Number(companyId))
    .sort((a,b)=>String(a.name||"").localeCompare(String(b.name||""),"pt-BR"));
}
function g26StatusClass(status){
  const key=g26Norm(status);
  if(key.includes("CORRECAO"))return "correction";
  if(key.includes("CONCLUID")||key==="CONFERIDA")return "done";
  if(key.includes("CONFERENCIA")&&key.includes("EM"))return "progress";
  return "waiting";
}
function g26DriveLink(url,label="Abrir pasta no Drive"){
  if(!url)return `<span class="muted">Pasta do Drive não informada</span>`;
  return `<a class="btn btn-drive" href="${g26Esc(url)}" target="_blank" rel="noopener noreferrer">▸ ${g26Esc(label)}</a>`;
}
function g26Card(item,projectist=true){
  const due=g26Deadline(item.deadline);
  const waiting=item.waiting_for?`Aguardando ação de ${item.waiting_for}`:"";
  return `<article class="g26-card" data-search="${g26Esc(g26Norm(`${item.work_name} ${item.status_manual} ${item.requested_files}`))}">
    <div class="g26-card-head">
      <div><small>CONSTRUTORA DEMO</small><h3>${g26Esc(item.work_name)}</h3></div>
      <span class="g26-status ${g26StatusClass(item.status_manual)}">${g26Esc(item.status_manual)}</span>
    </div>
    <div class="g26-meta">
      <div><small>Prazo</small><strong class="g26-deadline ${due.css}">${g26Esc(due.label)}</strong></div>
      <div><small>Prioridade</small><strong>${g26Esc(item.priority||"Normal")}</strong></div>
      <div><small>Solicitante</small><strong>${g26Esc(item.source_profile||"Projetista")}</strong></div>
      <div><small>Dependência</small><strong>${g26Esc(waiting||"Nenhuma")}</strong></div>
    </div>
    ${item.requested_files?`<p><b>Arquivos para conferência:</b> ${g26Esc(item.requested_files)}</p>`:""}
    ${item.observations?`<p class="muted">${g26Esc(item.observations)}</p>`:""}
    <div class="g26-actions">
      ${g26DriveLink(item.drive_folder_url)}
      ${projectist?`<button class="btn btn-light" onclick="client_demoDemandFormV26(${item.id})">Editar</button>`:`<button class="btn btn-primary" onclick="projectsWorkDetailV24(${item.id})">Abrir demanda</button>`}
      ${projectist&&item.status_manual==="Correção solicitada"?`<button class="btn btn-primary" onclick="client_demoResendV26(${item.id})">Reenviar</button>`:""}
      ${projectist?`<button class="btn btn-danger-outline" onclick="client_demoDeleteV26(${item.id})">Excluir</button>`:""}
    </div>
  </article>`;
}
function g26FilterRows(){
  const query=g26Norm(document.getElementById("g26Search")?.value||"");
  const status=document.getElementById("g26Status")?.value||"";
  document.querySelectorAll("#g26DemandGrid .g26-card").forEach(card=>{
    card.hidden=Boolean((query&&!card.dataset.search.includes(query))||(status&&!card.querySelector(".g26-status")?.textContent.includes(status)));
  });
}
async function g26Load(){
  const rows=await g26Api("/works?archived=false");
  G26.demands=rows.filter(item=>item.demand_kind===G26_KIND||g26Norm(item.company)==="CONSTRUTORA DEMO"&&String(item.source_reference||"").startsWith("CONSTRUTORA DEMO:"));
  return G26.demands;
}

window.projectistClientDemo=async function(){
  setHeader("Projetista — Conferências Construtora Demo","Envio obrigatório das pastas de obras para conferência da Projetos.");
  const content=document.getElementById("content");
  content.innerHTML=`<div class="card">Carregando obras e demandas…</div>`;
  try{
    const demands=await g26Load();
    const works=g26Works();
    const withDemand=new Set(demands.map(item=>Number(item.admin_work_id)).filter(Boolean));
    const overdue=demands.filter(item=>g26Deadline(item.deadline).days<0).length;
    const corrections=demands.filter(item=>item.status_manual==="Correção solicitada").length;
    const pending=demands.filter(item=>!["Conferida","Concluído"].includes(item.status_manual)).length;
    content.innerHTML=`<div class="g26-shell">
      <div class="g26-hero">
        <div><span>FLUXO SEPARADO</span><h2>Conferências obrigatórias — Construtora Demo</h2><p>Os arquivos permanecem no Google Drive. O sistema registra a pasta, o prazo, o andamento e as notificações.</p></div>
        <button class="btn btn-primary" onclick="client_demoDemandFormV26()">Nova demanda para Projetos</button>
      </div>
      <div class="grid grid-4">
        ${metric("Obras Construtora Demo",works.length,"Cadastro administrativo")}
        ${metric("Demandas ativas",pending,"Aguardando conclusão",pending?"warning":"success")}
        ${metric("Correções",corrections,"Ação necessária da Projetista",corrections?"danger":"success")}
        ${metric("Vencidas",overdue,"Prazo ultrapassado",overdue?"danger":"success")}
      </div>
      <div class="g26-panel">
        <div class="toolbar">
          <div><h3 style="margin:0">Obras disponíveis</h3><span class="muted">${works.length-withDemand.size} sem demanda vinculada</span></div>
          <button class="btn btn-light" onclick="client_demoWorksListV26()">Ver lista completa</button>
        </div>
      </div>
      <div class="toolbar g26-toolbar">
        <input id="g26Search" placeholder="Buscar obra ou arquivo" oninput="client_demoFilterV26()">
        <select id="g26Status" onchange="client_demoFilterV26()"><option value="">Todos os status</option>${G26_STATUSES.map(s=>`<option>${g26Esc(s)}</option>`).join("")}</select>
      </div>
      <div id="g26DemandGrid" class="g26-grid">${demands.length?demands.map(item=>g26Card(item,true)).join(""):`<div class="card">Nenhuma demanda Construtora Demo enviada para Projetos.</div>`}</div>
    </div>`;
    client_demoRefreshBadgeV26();
  }catch(error){
    content.innerHTML=`<div class="card"><h3>Não foi possível carregar as conferências</h3><p>${g26Esc(error.message)}</p></div>`;
  }
};
window.client_demoFilterV26=g26FilterRows;

window.client_demoWorksListV26=function(){
  const works=g26Works();
  const linked=new Map();
  G26.demands.forEach(item=>{
    const id=Number(item.admin_work_id);
    if(id)linked.set(id,(linked.get(id)||0)+1);
  });
  modal(`<h2>Obras da Construtora Demo</h2><p class="muted">Esta lista vem do cadastro administrativo de empresas e obras.</p>
    <div class="g26-work-list">${works.map(work=>`<div><span><strong>${g26Esc(work.name)}</strong><small>${linked.get(Number(work.id))||0} demanda(s) de conferência</small></span><button class="btn btn-primary" onclick="client_demoDemandFormV26(null,${work.id})">Criar demanda</button></div>`).join("")||"Nenhuma obra vinculada à empresa CONSTRUTORA DEMO."}</div>`);
};

window.client_demoDemandFormV26=async function(id=null,preferredWorkId=null){
  try{
    const item=id?await g26Api(`/works/${id}`):null;
    const works=g26Works();
    const selected=Number(preferredWorkId||item?.admin_work_id||works[0]?.id||0);
    if(!works.length){alert("Nenhuma obra da CONSTRUTORA DEMO foi encontrada no cadastro administrativo.");return}
    modal(`<h2>${item?"Editar demanda":"Nova conferência Construtora Demo"}</h2>
      <div class="g26-info">Primeiro envie os arquivos para a pasta do Google Drive. Depois cole o link abaixo para a Projetos abrir diretamente.</div>
      <form id="g26Form" class="form-grid">
        <div class="form-group" style="grid-column:1/-1"><label>Obra da Construtora Demo *</label><select name="admin_work_id" required>${works.map(work=>`<option value="${work.id}" ${Number(work.id)===selected?"selected":""}>${g26Esc(work.name)}</option>`).join("")}</select></div>
        <div class="form-group" style="grid-column:1/-1"><label>Link da pasta no Google Drive *</label><input name="drive_folder_url" type="url" required placeholder="https://drive.google.com/drive/folders/..." value="${g26Esc(item?.drive_folder_url||"")}"></div>
        <div class="form-group"><label>Nome da pasta</label><input name="drive_folder_name" value="${g26Esc(item?.drive_folder_name||"")}"></div>
        <div class="form-group"><label>Prazo da conferência *</label><input name="deadline" type="date" required value="${g26Esc(item?.deadline||"")}"></div>
        <div class="form-group"><label>Prioridade</label><select name="priority">${["Normal","Alta","Urgente"].map(value=>`<option ${value===(item?.priority||"Normal")?"selected":""}>${value}</option>`).join("")}</select></div>
        <div class="form-group"><label>Status</label><select name="status_manual">${G26_STATUSES.filter(value=>value!=="Concluído"||item).map(value=>`<option ${value===(item?.status_manual||"Aguardando conferência")?"selected":""}>${value}</option>`).join("")}</select></div>
        <div class="form-group" style="grid-column:1/-1"><label>Arquivos disponibilizados para conferência</label><textarea name="requested_files" placeholder="Ex.: planta, memorial, levantamento, arquivos DWG e PDF">${g26Esc(item?.requested_files||"")}</textarea></div>
        <div class="form-group" style="grid-column:1/-1"><label>Instruções e particularidades</label><textarea name="observations">${g26Esc(item?.observations||"")}</textarea></div>
        <div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar e enviar para Projetos</button></div>
      </form>`);
    document.getElementById("g26Form").onsubmit=async event=>{
      event.preventDefault();
      const form=event.target,adminWorkId=Number(form.elements.admin_work_id.value);
      const work=works.find(row=>Number(row.id)===adminWorkId);
      const status=form.elements.status_manual.value;
      const payload={
        ...(item||{}),
        client_id:null,
        source_profile:"Projetista",
        source_reference:`CONSTRUTORA DEMO:${adminWorkId}`,
        company:"CONSTRUTORA DEMO",
        work_name:work.name,
        description:"Conferência obrigatória dos arquivos da obra Construtora Demo.",
        deadline:form.elements.deadline.value,
        responsible:"Projetos",
        priority:form.elements.priority.value,
        status_manual:status,
        waiting_for:["Correção solicitada","Aguardando Projetista","Conferida"].includes(status)?"Projetista":"Projetos",
        expected_value:0,billed_value:0,paid_value:0,
        observations:form.elements.observations.value,
        demand_kind:G26_KIND,
        admin_work_id:adminWorkId,
        drive_folder_url:form.elements.drive_folder_url.value.trim(),
        drive_folder_name:form.elements.drive_folder_name.value.trim()||work.name,
        requested_files:form.elements.requested_files.value.trim(),
        transition_note:item?"Demanda e pasta do Drive atualizadas pela Projetista.":"Pasta do Drive enviada para conferência da Projetos."
      };
      await g26Api(id?`/works/${id}`:"/works",{method:id?"PUT":"POST",body:JSON.stringify({data:payload})});
      closeModal();showToast("Demanda enviada para a Projetos.");projectistClientDemo();
    };
  }catch(error){alert(error.message)}
};

window.client_demoResendV26=async function(id){
  try{
    const item=await g26Api(`/works/${id}`);
    await g26Api(`/works/${id}`,{method:"PUT",body:JSON.stringify({data:{
      ...item,status_manual:"Aguardando conferência",waiting_for:"Projetos",
      transition_note:"Correções realizadas no Drive; demanda reenviada para a Projetos."
    }})});
    showToast("Demanda reenviada para conferência.");projectistClientDemo();
  }catch(error){alert(error.message)}
};

window.client_demoDeleteV26=async function(id){
  try{
    const impact=await g26Api(`/delete-impact/work/${id}`);
    if(!confirm(`Excluir permanentemente a demanda “${impact.title}”?\\n\\n${impact.message}\\n\\nA pasta e os arquivos do Google Drive não serão excluídos.`))return;
    await g26Api(`/delete/work/${id}?confirmed=true`,{method:"DELETE"});
    showToast("Demanda excluída. A pasta do Drive não foi alterada.");projectistClientDemo();
  }catch(error){alert(error.message)}
};

window.client_demoRefreshBadgeV26=async function(){
  try{
    const rows=await g26Load();
    const count=rows.filter(item=>item.status_manual==="Correção solicitada"||g26Deadline(item.deadline).days<0).length;
    const badge=document.getElementById("client_demoProjetistaBadge");
    if(badge){badge.textContent=String(count);badge.classList.toggle("hidden",!count)}
  }catch(_){}
};

async function g26Transition(id,status,waitingFor,note){
  const item=await g26Api(`/works/${id}`);
  const saved=await g26Api(`/works/${id}`,{method:"PUT",headers:{"X-Gestão Operacional-Profile":"Projetos"},body:JSON.stringify({data:{
    ...item,status_manual:status,waiting_for:waitingFor||"",transition_note:note||`Status alterado para ${status}.`
  }})});
  showToast(`Demanda atualizada: ${status}.`);
  projectsWorkDetailV24(saved.id);
}
window.client_demoStartV26=id=>g26Transition(id,"Em conferência","","Projetos iniciou a conferência dos arquivos no Google Drive.");
window.client_demoCheckedV26=id=>g26Transition(id,"Conferida","Projetista","Projetos concluiu a conferência; Projetista deve revisar o retorno.");
window.client_demoConcludeV26=id=>g26Transition(id,"Concluído","","Conferência finalizada e arquivada.");
window.client_demoCorrectionV26=async function(id){
  const item=await g26Api(`/works/${id}`);
  modal(`<h2>Solicitar correção à Projetista</h2><form id="g26CorrectionForm" class="form-grid"><div class="form-group" style="grid-column:1/-1"><label>O que precisa ser corrigido? *</label><textarea name="note" required></textarea></div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Enviar correção</button></div></form>`);
  document.getElementById("g26CorrectionForm").onsubmit=async event=>{
    event.preventDefault();const note=event.target.elements.note.value.trim();
    await g26Api(`/works/${id}`,{method:"PUT",headers:{"X-Gestão Operacional-Profile":"Projetos"},body:JSON.stringify({data:{
      ...item,status_manual:"Correção solicitada",waiting_for:"Projetista",
      observations:[item.observations,note].filter(Boolean).join("\\n\\nCorreção solicitada: "),
      transition_note:`Correção solicitada à Projetista: ${note}`
    }})});
    closeModal();showToast("Correção enviada para a Projetista.");projectsWorkDetailV24(id);
  };
};

const g26LegacyWorkDetail=window.projectsWorkDetailV24;
window.projectsWorkDetailV24=function(id){
  G26.selectedId=id;
  const content=document.getElementById("content");
  setHeader("Projetos — Conferência de obra","Pasta do Drive, orientações, prazo e histórico da demanda.");
  content.innerHTML=`<div class="card">Carregando demanda…</div>`;
  g26Api(`/works/${id}`,{headers:{"X-Gestão Operacional-Profile":"Projetos"}}).then(item=>{
    if(item.demand_kind!==G26_KIND){g26LegacyWorkDetail(id);return}
    const due=g26Deadline(item.deadline);
    content.innerHTML=`<div class="g26-shell">
      <div class="toolbar"><button class="btn btn-light" onclick="currentArea='projects';page='projectsWorks';render()">← Voltar para Obras</button>${g26DriveLink(item.drive_folder_url)}</div>
      <div class="g26-hero"><div><span>CONFERÊNCIA CONSTRUTORA DEMO</span><h2>${g26Esc(item.work_name)}</h2><p>Enviada por ${g26Esc(item.source_profile)} para conferência obrigatória.</p></div><span class="g26-status ${g26StatusClass(item.status_manual)}">${g26Esc(item.status_manual)}</span></div>
      <div class="grid grid-4">
        ${metric("Prazo",g26Date(item.deadline),due.label,due.css==="overdue"?"danger":due.css==="urgent"?"warning":"")}
        ${metric("Prioridade",item.priority||"Normal","Definida por Projetista")}
        ${metric("Pasta",item.drive_folder_name||item.work_name,item.drive_folder_url?"Link disponível":"Link ausente")}
        ${metric("Dependência",item.waiting_for||"Nenhuma","Quem precisa agir")}
      </div>
      <div class="g26-detail-grid">
        <section class="g26-panel"><h3>Orientações da Projetista</h3><p>${g26Esc(item.observations||"Sem observações.")}</p><h4>Arquivos esperados</h4><p>${g26Esc(item.requested_files||"Não informado.")}</p>${g26DriveLink(item.drive_folder_url,"Abrir arquivos no Google Drive")}</section>
        <section class="g26-panel"><h3>Ações da conferência</h3><div class="g26-action-stack">
          <button class="btn btn-primary" onclick="client_demoStartV26(${id})">Iniciar conferência</button>
          <button class="btn btn-light" onclick="client_demoCorrectionV26(${id})">Solicitar correção à Projetista</button>
          <button class="btn btn-light" onclick="client_demoCheckedV26(${id})">Marcar como conferida</button>
          <button class="btn btn-primary" onclick="client_demoConcludeV26(${id})">Concluir e arquivar</button>
        </div></section>
      </div>
      <section class="g26-panel"><h3>Histórico completo</h3><div class="projects-v24-timeline">${(item.events||[]).map(event=>`<div class="projects-v24-event"><strong>${g26Esc(event.event_type)}</strong><div>${g26Esc(event.description)}</div><small class="muted">${g26Esc(g26Date(event.event_date))} • ${g26Esc(event.actor)}</small></div>`).join("")||"Sem movimentações."}</div></section>
    </div>`;
  }).catch(error=>{content.innerHTML=`<div class="card">${g26Esc(error.message)}</div>`});
};

setTimeout(client_demoRefreshBadgeV26,1200);
setInterval(client_demoRefreshBadgeV26,60000);
})();
