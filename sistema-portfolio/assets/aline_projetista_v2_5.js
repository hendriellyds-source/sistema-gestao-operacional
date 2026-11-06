(function(){
"use strict";

const A24={
  selectedClient:null,
  selectedProcess:null,
  selectedWork:null,
  processCity:"",
  processCityMode:"",
  citySection:"overview",
  workSection:"overview",
  filters:{search:"",city:"",service:"",type:"",status:"",registry:""},
  money:new Intl.NumberFormat("pt-BR",{style:"currency",currency:"BRL"}),
  cities:["Balneário Camboriú","Biguaçu","Blumenau","Brusque","Chapecó","Criciúma","Florianópolis","Itajaí","Jaraguá do Sul","Joinville","Lages","Navegantes","Palhoça","Rio do Sul","São José","Tijucas","Tubarão"],
  services:["Retificação de área","Amembramento","Desmembramento"]
};
window.A24=A24;

function a24Esc(value){
  return String(value??"").replace(/[&<>"']/g,char=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  }[char]));
}
function a24Date(value){
  if(!value) return "—";
  const [year,month,day]=String(value).slice(0,10).split("-");
  return year&&month&&day?`${day}/${month}/${year}`:String(value);
}
function a24Money(value){return A24.money.format(Number(value)||0)}
function a24StatusClass(status){
  return status==="VENCIDO"?"overdue":
    status==="PRAZO A VENCER"?"urgent":
    status==="Urgente"?"urgent":
    status==="Perto do vencimento"?"near":
    status==="Aguardando"?"waiting":
    status==="Concluído"?"done":"ok";
}
function a24Status(status){
  return `<span class="aline-v24-status ${a24StatusClass(status)}">${a24Esc(status||"OK")}</span>`;
}
function a24Select(name,values,selected="",placeholder="Selecione"){
  return `<select name="${a24Esc(name)}"><option value="">${a24Esc(placeholder)}</option>${
    values.map(value=>{
      const key=typeof value==="object"?value.id:value;
      const label=typeof value==="object"?value.name:value;
      return `<option value="${a24Esc(key)}" ${String(key)===String(selected)?"selected":""}>${a24Esc(label)}</option>`;
    }).join("")
  }</select>`;
}
function a24Profile(){
  try{return currentArea==="admin"?"Diretor":currentArea==="projectist"?"Projetos":"Processos"}
  catch(_){return "Processos"}
}
async function a24Api(path,options={}){
  const headers={"Content-Type":"application/json","X-Sistema Portfólio-Profile":a24Profile(),...(options.headers||{})};
  const response=await fetch(`/api/aline${path}`,{...options,headers});
  const text=await response.text();
  let body={};
  try{body=text?JSON.parse(text):{}}catch(_){body={detail:text||"Resposta inválida do servidor."}}
  if(!response.ok) throw new Error(body.detail||body.message||`Erro ${response.status}`);
  return body;
}
function a24Loading(title,subtitle){
  setHeader(title,subtitle);
  document.getElementById("content").innerHTML=`
    <div class="aline-v24-shell">
      ${a24Nav()}
      <div class="aline-v24-empty">Carregando dados da Processos…</div>
    </div>`;
}
function a24Error(error){
  document.getElementById("content").innerHTML=`
    <div class="aline-v24-shell">${a24Nav()}
      <div class="card"><h3>Não foi possível carregar esta área</h3>
      <p class="muted">${a24Esc(error?.message||error)}</p>
      <button class="btn btn-primary" onclick="render()">Tentar novamente</button></div>
    </div>`;
}
function a24Nav(){
  let current="";
  try{current=page}catch(_){}
  if(current==="alineProcesses"||A24.selectedProcess||A24.selectedClient){
    return a25ProcessNav();
  }
  if(current==="alineWorks"||A24.selectedWork){
    return a25WorkNav();
  }
  return "";
}
function a24Kpi(label,value,caption,kind=""){
  return `<div class="aline-v24-kpi ${kind}"><small>${a24Esc(label)}</small><strong>${a24Esc(value)}</strong><small>${a24Esc(caption)}</small></div>`;
}
function a24Banner(title,caption,actions=""){
  return `<div class="aline-v24-banner"><div><h2>${a24Esc(title)}</h2><p>${a24Esc(caption)}</p></div><div class="actions">${actions}</div></div>`;
}
function a24Empty(text){return `<div class="aline-v24-empty">${a24Esc(text)}</div>`}
function a24ClientLabel(item){
  const doc=item.cpf_cnpj?` • ${item.person_type==="PJ"?"CNPJ":"CPF"} ${a24Esc(item.cpf_cnpj)}`:"";
  return `${a24Esc(item.name||item.client_name||"Cliente")}${doc}`;
}
function a24DeadlineText(item){
  const due=item.next_deadline||item.document_submission_due||item.deadline;
  if(!due) return "Sem prazo definido";
  return `${a24Date(due)} • ${item.deadline_label||a25DeadlineLabel(item.days_to_deadline)}`;
}
function a24Deadline(item){
  const dates=item.deadline_dates?.length?item.deadline_dates:(item.deadline_date?[{kind:"Data do prazo",date:item.deadline_date}]:[]);
  if(!dates.length)return '<span class="aline-v25-deadline normal">Sem prazo definido</span>';
  return `<span class="aline-v25-deadline-stack"><span>${a24Esc(item.deadline_label||a25DeadlineLabel(item.days_to_deadline))}</span>${dates.map(entry=>`<span class="aline-v25-deadline-date">${a24Esc(entry.kind)}: <strong>${a24Date(entry.date)}</strong></span>`).join("")}</span>`;
}

function a25DeadlineLabel(days){
  if(days===null||days===undefined) return "sem cálculo";
  if(days<0) return `VENCIDO há ${Math.abs(days)} dia(s)`;
  if(days===0) return "PRAZO A VENCER — vence hoje";
  if(days<=2) return `PRAZO A VENCER — faltam ${days} dia(s)`;
  return `Faltam ${days} dias`;
}

function a25ProcessNav(){
  if(typeof window.alineProcessNavV26==="function")return window.alineProcessNavV26();
  const cities=["","Biguaçu","Florianópolis","Palhoça","São José"];
  return `<div class="aline-tabs aline-v25-primary-tabs">${cities.map(city=>`<button class="${A24.processCity===city?"active":""}" onclick="alineSetProcessCityV25('${a24Esc(city)}')">${city||"Visão geral"}</button>`).join("")}</div>`;
}

function a25WorkNav(){
  const items=[
    ["overview","Visão geral"],["demands","Demandas"],["progress","Em andamento"],
    ["deadlines","Prazos"],["completed","Concluídas"]
  ];
  return `<div class="aline-tabs aline-v25-primary-tabs">${items.map(([key,label])=>
    `<button class="${A24.workSection===key?"active":""}" onclick="alineSetWorkSectionV25('${key}')">${label}</button>`
  ).join("")}</div>`;
}

window.alineDashboardV24=function(){
  a24Loading("Processos — Projetista","Processos, conferências de obras, prazos e documentos.");
  Promise.all([
    a24Api("/summary"),a24Api("/notifications?unread_only=true"),
    a24Api("/processes"),a24Api("/works")
  ]).then(([summary,notifications,processes,works])=>{
    if(typeof page!=="undefined"&&page!=="alineDashboard") return;
    document.getElementById("content").innerHTML=`
      <div class="aline-v24-shell">${a24Nav()}
        ${a24Banner("Central de trabalho da Processos","Os prazos são calculados automaticamente: 10 dias para alerta e 2 dias para urgência.",
          `<button class="btn" onclick="alineProcessFormV24()">Novo processo</button>
           <button class="btn" onclick="alineWorkFormV24()">Nova demanda de obra</button>`)}
        <div class="aline-v24-kpis">
          ${a24Kpi("Clientes",summary.clients,"cadastros ativos")}
          ${a24Kpi("Processos",summary.active_processes,"prefeitura e cartório")}
          ${a24Kpi("Obras",summary.active_works,"demandas de conferência")}
          ${a24Kpi("Urgentes",summary.urgent,"vencidos ou até 2 dias","urgent")}
          ${a24Kpi("Perto do prazo",summary.near,"até 10 dias","near")}
          ${a24Kpi("Aguardando",summary.waiting,"órgão, cliente ou documento","waiting")}
        </div>
        <div class="aline-v24-split">
          <div class="aline-v24-panel">
            <div class="toolbar" style="margin:0 0 12px"><h3 style="margin:0">Pendências prioritárias</h3>
              <button class="btn btn-light" onclick="currentArea='aline';page='alineNotifications';render()">Ver todas</button></div>
            ${notifications.length?notifications.slice(0,7).map(a24NotificationRow).join(""):a24Empty("Nenhuma pendência prioritária.")}
          </div>
          <div class="aline-v24-panel">
            <h3>Resumo de valores</h3>
            <div class="aline-v24-meta">
              <div><small>Previsto</small><strong class="aline-v24-money">${a24Money(summary.expected_value)}</strong></div>
              <div><small>Cobrado</small><strong class="aline-v24-money">${a24Money(summary.billed_value)}</strong></div>
              <div><small>Pago</small><strong class="aline-v24-money">${a24Money(summary.paid_value)}</strong></div>
              <div><small>Em aberto</small><strong class="aline-v24-money">${a24Money(summary.billed_value-summary.paid_value)}</strong></div>
            </div>
            <h3 style="margin-top:18px">Atalhos oficiais</h3>
            <div style="display:grid;gap:8px">
              <a class="aline-v24-portal" target="_blank" rel="noopener" href="https://bigua.atende.net/">Prefeitura de Biguaçu ↗</a>
              <a class="aline-v24-portal" target="_blank" rel="noopener" href="https://saojose.atende.net/cidadao">Prefeitura de São José ↗</a>
              <a class="aline-v24-portal" target="_blank" rel="noopener" href="https://servicos.floripa.sc.gov.br/atendimento/inicio">Prefeitura de Florianópolis ↗</a>
              <a class="aline-v24-portal" target="_blank" rel="noopener" href="https://palhoca.atende.net/cidadao">Prefeitura de Palhoça ↗</a>
              <a class="aline-v24-portal" target="_blank" rel="noopener" href="https://ridigital.org.br/eProtocolo/listagem_contratos.aspx?comum=1">RI Digital — e-Protocolo ↗</a>
            </div>
          </div>
        </div>
        <div class="aline-v24-panel"><h3>Processos atualizados recentemente</h3>
          ${a24ProcessGrid(processes.slice(0,6))}</div>
        <div class="aline-v24-panel"><h3>Obras com prazo</h3>${a24WorkGrid(works.slice(0,6))}</div>
      </div>`;
  }).catch(a24Error);
};

function a24NotificationRow(item){
  return `<div class="aline-v24-notification ${item.read?"":"unread"}">
    <span class="aline-v24-dot ${a24StatusClass(item.status)}"></span>
    <div><strong>${a24Esc(item.title)}</strong><div class="muted">${a24Esc(item.client||"")} • ${a24Esc(item.message)} ${item.deadline?`• prazo em <strong>${a24Date(item.deadline)}</strong>`:""}</div></div>
    <div class="actions"><button class="btn btn-light" onclick="alineOpenNotificationV24('${a24Esc(item.key)}','${item.entity_type}',${item.entity_id})">Abrir</button></div>
  </div>`;
}
window.alineOpenNotificationV24=async function(key,type,id){
  try{await a24Api(`/notifications/${encodeURIComponent(key)}/read`,{method:"POST"});}catch(_){}
  if(type==="process") alineProcessDetailV24(id); else alineWorkDetailV24(id);
};

window.alineClientsV24=function(){
  a24Loading("Processos — Clientes","Cadastro central de pessoas físicas e jurídicas.");
  a24Api("/clients").then(rows=>{
    if(page!=="alineClients") return;
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${a24Nav()}
      ${a24Banner("Clientes e documentação","Cada cliente possui pasta individual, processos, obras e versões de documentos.",
        `<button class="btn" onclick="alineClientFormV24()">Novo cliente</button>`)}
      <div class="toolbar" style="margin:0"><input id="a24ClientSearch" placeholder="Buscar por cliente, CPF ou CNPJ" oninput="alineFilterClientsV24()" style="max-width:430px">
        <button class="btn btn-light" onclick="alineImportSpreadsheetV24()">Importar planilha de processos</button></div>
      <div id="a24ClientGrid" class="aline-v24-grid">${a24ClientGrid(rows)}</div>
    </div>`;
    A24.clients=rows;
  }).catch(a24Error);
};
function a24ClientGrid(rows){
  if(!rows.length) return a24Empty("Nenhum cliente cadastrado.");
  return rows.map(item=>`<div class="aline-v24-card" data-client-search="${a24Esc(`${item.name} ${item.cpf_cnpj||""} ${item.legal_name||""}`.toUpperCase())}">
    <div class="aline-v24-card-head"><div><h3>${a24Esc(item.name)}</h3><div class="muted">${item.person_type==="PJ"?"Pessoa jurídica":"Pessoa física"}</div></div>
      <span class="aline-v24-status ok">${item.person_type}</span></div>
    <div class="aline-v24-meta">
      <div><small>${item.person_type==="PJ"?"CNPJ":"CPF"}</small>${a24Esc(item.cpf_cnpj||"Não informado")}</div>
      <div><small>Contato</small>${a24Esc(item.phone||item.email||"Não informado")}</div>
      <div><small>Processos</small>${item.process_count||0}</div>
      <div><small>Obras</small>${item.work_count||0}</div>
    </div>
    <div class="aline-v24-actions"><button class="btn btn-primary" onclick="alineClientDetailV24(${item.id})">Abrir pasta</button>
      <button class="btn btn-light" onclick="alineClientFormV24(${item.id})">Editar</button></div>
  </div>`).join("");
}
window.alineFilterClientsV24=function(){
  const query=String(document.getElementById("a24ClientSearch")?.value||"").trim().toUpperCase();
  document.querySelectorAll("[data-client-search]").forEach(card=>card.hidden=!card.dataset.clientSearch.includes(query));
};
window.alineClientFormV24=async function(id){
  try{
    const clients=A24.clients?.length?A24.clients:await a24Api("/clients");
    const item=id?clients.find(row=>Number(row.id)===Number(id)):null;
    modal(`<h2>${item?"Editar cliente":"Novo cliente"}</h2>
      <form id="a24ClientForm" class="form-grid">
        <div class="form-group"><label>Tipo</label>${a24Select("person_type",["PF","PJ"],item?.person_type||"PF")}</div>
        ${field("Nome do cliente","name",item?.name||"","text",true)}
        ${field("CPF/CNPJ","cpf_cnpj",item?.cpf_cnpj||"")}
        ${field("Data de nascimento","birth_date",item?.birth_date||"","date")}
        ${field("Razão social","legal_name",item?.legal_name||"")}
        ${field("Nome fantasia","trade_name",item?.trade_name||"")}
        ${field("Responsável / representante","responsible_name",item?.responsible_name||"")}
        ${field("Inscrição estadual","state_registration",item?.state_registration||"")}
        ${field("Inscrição municipal","municipal_registration",item?.municipal_registration||"")}
        ${field("WhatsApp / telefone","phone",item?.phone||"")}
        ${field("E-mail","email",item?.email||"","email")}
        ${field("Cidade","city",item?.city||"")}
        ${field("UF","state",item?.state||"SC")}
        <div class="form-group" style="grid-column:1/-1"><label>Endereço</label><input name="address" value="${a24Esc(item?.address||"")}"></div>
        <div class="form-group" style="grid-column:1/-1"><label>Observações</label><textarea name="notes">${a24Esc(item?.notes||"")}</textarea></div>
        <div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar cliente</button></div>
      </form>`);
    document.getElementById("a24ClientForm").onsubmit=async event=>{
      event.preventDefault();
      const data=Object.fromEntries(new FormData(event.target).entries());
      try{
        await a24Api(id?`/clients/${id}`:"/clients",{method:id?"PUT":"POST",body:JSON.stringify({data})});
        closeModal();showToast("Cliente salvo no banco central.");alineClientsV24();
      }catch(error){alert(error.message)}
    };
  }catch(error){alert(error.message)}
};
window.alineClientDetailV24=function(id){
  A24.selectedClient=id;
  a24Loading("Pasta do cliente","Processos, obras e documentos em um único lugar.");
  Promise.all([
    a24Api("/clients"),a24Api(`/processes?search=${encodeURIComponent("")}`),
    a24Api("/works"),a24Api(`/documents?owner_type=client&owner_id=${id}`)
  ]).then(([clients,processes,works,documents])=>{
    const client=clients.find(row=>Number(row.id)===Number(id));
    if(!client) throw new Error("Cliente não encontrado.");
    const clientProcesses=processes.filter(row=>Number(row.client_id)===Number(id));
    const clientWorks=works.filter(row=>Number(row.client_id)===Number(id));
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${a24Nav()}
      <div class="toolbar"><button class="btn btn-light" onclick="currentArea='aline';page='alineClients';render()">← Clientes</button>
        <div class="actions"><button class="btn btn-light" onclick="alineClientFormV24(${id})">Editar cadastro</button>
        <button class="btn btn-primary" onclick="alineUploadDocumentV24('client',${id})">Adicionar documento</button></div></div>
      ${a24Banner(client.name,`${client.person_type==="PJ"?"CNPJ":"CPF"}: ${client.cpf_cnpj||"não informado"} • ${client.city||"cidade não informada"}`)}
      <div class="aline-v24-split">
        <div>
          <div class="aline-v24-panel"><div class="toolbar" style="margin:0 0 12px"><h3 style="margin:0">Processos</h3><button class="btn btn-primary" onclick="alineProcessFormV24(null,${id})">Novo processo</button></div>${a24ProcessGrid(clientProcesses)}</div>
          <div class="aline-v24-panel" style="margin-top:16px"><div class="toolbar" style="margin:0 0 12px"><h3 style="margin:0">Obras</h3><button class="btn btn-primary" onclick="alineWorkFormV24(null,${id})">Nova obra</button></div>${a24WorkGrid(clientWorks)}</div>
        </div>
        <div class="aline-v24-panel"><h3>Documentos do cliente</h3>${a24DocumentList(documents)}
          <div class="finance-note" style="margin-top:14px">Padrão automático: ${client.person_type==="PJ"?"PJ_CNPJ":"PF_CPF"} + documento + versão. O arquivo original também fica registrado.</div></div>
      </div>
    </div>`;
  }).catch(a24Error);
};

function a24ProcessGrid(rows){
  if(!rows.length) return a24Empty("Nenhum processo nesta seleção.");
  return `<div class="aline-v24-grid">${rows.map(item=>`<div class="aline-v24-card">
    <div class="aline-v24-card-head"><div><h3>${a24Esc(item.title)}</h3><div class="muted">${a24Esc(item.client_name||"")} • ${a24Esc(item.process_type)}</div></div>${a24Status(item.status)}</div>
    <div class="aline-v24-meta">
      <div><small>Serviço</small>${a24Esc(item.service_type)}</div><div><small>Cidade</small>${a24Esc(item.city)}</div>
      <div><small>Prazo</small>${a24Deadline(item)}</div><div><small>Protocolo</small>${a24Esc(item.protocol_p||item.protocol_s||item.entry_code||item.legacy_reference||"Não informado")}</div>
    </div>
    <div class="aline-v24-actions"><button class="btn btn-primary" onclick="alineProcessDetailV24(${item.id})">Abrir</button><button class="btn btn-light" onclick="alineProcessFormV24(${item.id})">Editar</button></div>
  </div>`).join("")}</div>`;
}
window.alineProcessesV24=function(){
  a24Loading("Processos — Processos","Prefeitura e cartório organizados por cliente.");
  Promise.all([a24Api("/processes"),a24Api("/registries")]).then(([rows,registries])=>{
    A24.processes=rows;A24.registries=registries;
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${a24Nav()}
      ${a24Banner("Processos de Prefeitura e Cartório","Filtros por cidade, serviço, tipo, status, cartório, protocolo e matrícula.",
        `<button class="btn" onclick="alineProcessFormV24()">Novo processo</button><button class="btn" onclick="alineImportSpreadsheetV24()">Importar XLSX</button>`)}
      <div class="aline-v24-filters">
        <input id="a24ProcessSearch" placeholder="Cliente, CPF/CNPJ, protocolo, código ou matrícula" oninput="alineApplyProcessFiltersV24()">
        ${a24Select("city",A24.cities,"","Todas as cidades")}
        ${a24Select("service",A24.services,"","Todos os serviços")}
        ${a24Select("type",["Prefeitura","Cartório"],"","Todos os tipos")}
        ${a24Select("status",["OK","Aguardando","Perto do vencimento","Urgente"],"","Todos os status")}
        ${a24Select("registry",registries,"","Todos os cartórios")}
      </div><div id="a24ProcessGrid">${a24ProcessGrid(rows)}</div>
    </div>`;
    document.querySelectorAll(".aline-v24-filters select").forEach(select=>select.onchange=alineApplyProcessFiltersV24);
  }).catch(a24Error);
};
window.alineApplyProcessFiltersV24=function(){
  const container=document.getElementById("content");
  const search=String(container.querySelector("#a24ProcessSearch")?.value||"").toUpperCase();
  const values=Object.fromEntries([...container.querySelectorAll(".aline-v24-filters select")].map(select=>[select.name,select.value]));
  const filtered=(A24.processes||[]).filter(item=>
    (!search||`${item.client_name} ${item.cpf_cnpj} ${item.title} ${item.entry_code} ${item.secondary_code} ${item.protocol_s} ${item.protocol_p} ${item.access_code_ac} ${item.property_registration} ${item.legacy_reference}`.toUpperCase().includes(search))&&
    (!values.city||item.city===values.city)&&(!values.service||item.service_type===values.service)&&
    (!values.type||item.process_type===values.type)&&(!values.status||item.status===values.status)&&
    (!values.registry||String(item.registry_id)===String(values.registry))
  );
  document.getElementById("a24ProcessGrid").innerHTML=a24ProcessGrid(filtered);
};

window.alineProcessFormV24=async function(id,clientId){
  try{
    const [clients,registries,item]=await Promise.all([
      a24Api("/clients"),a24Api("/registries?active_only=true"),
      id?a24Api(`/processes/${id}`):Promise.resolve(null)
    ]);
    const value=(name,fallback="")=>item?.[name]??fallback;
    modal(`<h2>${item?"Editar processo":"Novo processo"}</h2>
      <form id="a24ProcessForm" class="form-grid">
        <div class="form-group"><label>Cliente *</label>${a24Select("client_id",clients,item?.client_id||clientId||"","Selecione o cliente")}</div>
        ${field("Título do processo","title",value("title"),"text",true)}
        <div class="form-group"><label>Tipo *</label>${a24Select("process_type",["Prefeitura","Cartório"],value("process_type","Prefeitura"))}</div>
        <div class="form-group"><label>Serviço *</label>${a24Select("service_type",A24.services,value("service_type","Retificação de área"))}</div>
        <div class="form-group"><label>Cidade *</label>${a24Select("city",A24.cities,value("city","Biguaçu"))}</div>
        ${field("Responsável","responsible",value("responsible","Processos"))}
        <div id="a24PrefeituraFields" style="display:contents">
          ${field("Código de entrada","entry_code",value("entry_code"))}
          ${field("Segundo código / verificador","secondary_code",value("secondary_code"))}
        </div>
        ${field("Terceiro código (e-mails)","third_code",value("third_code"))}
        <div id="a24CartorioFields" style="display:contents">
          ${field("Protocolo S","protocol_s",value("protocol_s"))}
          ${field("Protocolo P","protocol_p",value("protocol_p"))}
          ${field("Código AC","access_code_ac",value("access_code_ac"))}
          ${field("Pré-anotações (separe por vírgula)","pre_annotation_numbers",(value("pre_annotation_numbers",[])||[]).join(", "))}
          <div class="form-group"><label>Registro de imóveis / cartório</label>${a24Select("registry_id",registries,value("registry_id"),"Selecione o cartório")}</div>
          ${field("Prazo de envio dos documentos","document_submission_due",value("document_submission_due"),"date")}
        </div>
        ${field("Próximo prazo","next_deadline",value("next_deadline"),"date")}
        <div class="form-group"><label>Status base</label>${a24Select("status_manual",["OK","Aguardando","Concluído"],value("status_manual","OK"))}</div>
        <div class="form-group"><label>Aguardando ação de</label>${a24Select("waiting_for",["Prefeitura","Cartório","Cliente","Projetos","Diretor","Documentação"],value("waiting_for"),"Nenhuma dependência")}</div>
        ${field("Matrícula do imóvel","property_registration",value("property_registration"))}
        ${field("Endereço do imóvel","property_address",value("property_address"))}
        ${field("Valor previsto","expected_value",value("expected_value",0),"number")}
        ${field("Valor cobrado","billed_value",value("billed_value",0),"number")}
        ${field("Valor pago","paid_value",value("paid_value",0),"number")}
        ${field("Referência histórica","legacy_reference",value("legacy_reference"))}
        <div class="form-group" style="grid-column:1/-1"><label>Observações</label><textarea name="observations">${a24Esc(value("observations"))}</textarea></div>
        <div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar processo</button></div>
      </form>`);
    const form=document.getElementById("a24ProcessForm");
    const toggle=()=>{
      const cartorio=form.elements.process_type.value==="Cartório";
      document.getElementById("a24PrefeituraFields").style.display=cartorio?"none":"contents";
      document.getElementById("a24CartorioFields").style.display=cartorio?"contents":"none";
    };
    form.elements.process_type.onchange=toggle;toggle();
    form.onsubmit=async event=>{
      event.preventDefault();
      const values=Object.fromEntries(new FormData(form).entries());
      try{
        const saved=await a24Api(id?`/processes/${id}`:"/processes",{method:id?"PUT":"POST",body:JSON.stringify({data:values})});
        closeModal();showToast(saved.status==="Concluído"?"Processo concluído e arquivado.":"Processo salvo.");alineProcessDetailV24(saved.id);
      }catch(error){alert(error.message)}
    };
  }catch(error){alert(error.message)}
};
window.alineProcessDetailV24=function(id){
  A24.selectedProcess=id;a24Loading("Ficha do processo","Códigos, prazos, documentos e cronologia.");
  a24Api(`/processes/${id}`).then(item=>{
    const portal=item.process_type==="Cartório"?(item.registry_portal_url||item.ri_digital_url):item.portal_url;
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${a24Nav()}
      <div class="toolbar"><button class="btn btn-light" onclick="currentArea='aline';page='alineProcesses';render()">← Processos</button>
        <div class="actions"><button class="btn btn-light" onclick="alineProcessFormV24(${id})">Editar</button>
        <button class="btn btn-primary" onclick="alineUploadDocumentV24('process',${id})">Adicionar documento</button></div></div>
      ${a24Banner(item.title,`${item.client_name} • ${item.process_type} • ${item.city}`,
        portal?`<a class="btn" href="${a24Esc(portal)}" target="_blank" rel="noopener">Abrir portal oficial ↗</a>`:"")}
      <div class="aline-v24-kpis">
        ${a24Kpi("Status",item.status,item.deadline_label||a25DeadlineLabel(item.days_to_deadline),a24StatusClass(item.status))}
        ${a24Kpi("Previsto",a24Money(item.expected_value),"apoio comercial")}
        ${a24Kpi("Cobrado",a24Money(item.billed_value),"valor enviado")}
        ${a24Kpi("Pago",a24Money(item.paid_value),"valor recebido")}
        ${a24Kpi("Documentos",item.documents.length,"com versões")}
        ${a24Kpi("Atualizações",item.events.length,"linha do tempo")}
      </div>
      <div class="aline-v24-panel"><h3>Datas do prazo</h3>${a24Deadline(item)}</div>
      <div class="aline-v24-split">
        <div>
          <div class="aline-v24-panel"><h3>Dados do processo</h3>
            <div class="aline-v24-meta">
              <div><small>Serviço</small>${a24Esc(item.service_type)}</div><div><small>Cidade</small>${a24Esc(item.city)}</div>
              <div><small>Código de entrada</small>${a24Esc(item.entry_code||"—")}</div><div><small>Segundo código</small>${a24Esc(item.secondary_code||"—")}</div>
              <div><small>Terceiro código</small>${a24Esc(item.third_code||"—")}</div>
              <div><small>Protocolo S</small>${a24Esc(item.protocol_s||"—")}</div><div><small>Protocolo P</small>${a24Esc(item.protocol_p||"—")}</div>
              <div><small>Código AC</small>${a24Esc(item.access_code_ac||"—")}</div><div><small>Pré-anotações</small>${a24Esc((item.pre_annotation_numbers||[]).join(", ")||"—")}</div>
              <div><small>Matrícula</small>${a24Esc(item.property_registration||"—")}</div><div><small>Cartório</small>${a24Esc(item.registry_name||"—")}</div>
            </div>
            ${item.process_type==="Cartório"?`<div class="aline-v24-secret" style="margin-top:12px"><strong>Credenciais protegidas do protocolo</strong><p class="muted">O sistema não automatiza login. Número de acesso e senha ficam criptografados e só abrem com o PIN do cofre.</p><div class="actions"><button class="btn btn-light" onclick="alineProcessCredentialsV24(${item.id})">Salvar / substituir</button><button class="btn btn-light" onclick="alineRevealProcessV24(${item.id})">Revelar</button>${item.registry_id?`<button class="btn btn-light" onclick="alineRevealRegistryV24(${item.registry_id})">Credenciais gerais do cartório</button>`:""}</div></div>`:""}
            ${item.observations?`<p>${a24Esc(item.observations)}</p>`:""}
          </div>
          <div class="aline-v24-panel" style="margin-top:16px"><h3>Cronologia</h3><div class="aline-v24-timeline">${
            item.events.length?item.events.map(event=>`<div class="aline-v24-event"><strong>${a24Esc(event.event_type)}</strong><div>${a24Esc(event.description)}</div><small class="muted">${a24Date(event.event_date)} • ${a24Esc(event.actor)}</small></div>`).join(""):"Sem histórico."
          }</div></div>
        </div>
        <div class="aline-v24-panel"><h3>Documentos e versões</h3>${a24DocumentList(item.documents)}</div>
      </div>
    </div>`;
  }).catch(a24Error);
};

function a24WorkGrid(rows){
  if(!rows.length) return a24Empty("Nenhuma demanda de obra nesta seleção.");
  return `<div class="aline-v24-grid">${rows.map(item=>`<div class="aline-v24-card">
    <div class="aline-v24-card-head"><div><h3>${a24Esc(item.work_name)}</h3><div class="muted">${a24Esc(item.source_profile)} • ${a24Esc(item.client_name||item.company||"Sem cliente")}</div></div><div>${a24Status(item.progress_status||item.status_manual||item.status)}${item.deadline_status&&item.deadline_status!=="OK"&&item.deadline_status!=="Concluído"?` ${a24Status(item.deadline_status)}`:""}</div></div>
    <div class="aline-v24-meta"><div><small>Prazo</small>${a24Deadline(item)}</div><div><small>Responsável</small>${a24Esc(item.responsible)}</div><div><small>Prioridade</small>${a24Esc(item.priority)}</div><div><small>Valor previsto</small>${a24Money(item.expected_value)}</div></div>
    <div class="aline-v24-actions"><button class="btn btn-primary" onclick="alineWorkDetailV24(${item.id})">Abrir</button><button class="btn btn-light" onclick="alineWorkFormV24(${item.id})">Editar</button></div>
  </div>`).join("")}</div>`;
}
window.alineWorksV24=function(){
  a24Loading("Processos — Obras","Demandas enviadas por Projetos e Diretor/Exemplo.");
  Promise.all([a24Api("/works"),a24Api("/clients")]).then(([rows,clients])=>{
    A24.works=rows;A24.clients=clients;
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${a24Nav()}
      ${a24Banner("Conferência de obras","Prazo definido pela Projetos ou pelo Diretor, documentos, observações e histórico preservado.",
        `<button class="btn" onclick="alineWorkFormV24()">Nova demanda</button>`)}
      <div class="aline-v24-filters" style="grid-template-columns:minmax(260px,2fr) repeat(2,minmax(150px,1fr))">
        <input id="a24WorkSearch" placeholder="Obra, empresa ou cliente" oninput="alineApplyWorkFiltersV24()">
        ${a24Select("status",["Aguardando","Iniciado","Em andamento","Perto do vencimento","Urgente"],"","Todos os status")}
        ${a24Select("source",["Projetos","Diretor","Exemplo"],"","Todas as origens")}
      </div><div id="a24WorkGrid">${a24WorkGrid(rows)}</div></div>`;
    document.querySelectorAll(".aline-v24-filters select").forEach(select=>select.onchange=alineApplyWorkFiltersV24);
  }).catch(a24Error);
};
window.alineApplyWorkFiltersV24=function(){
  const search=String(document.getElementById("a24WorkSearch")?.value||"").toUpperCase();
  const selects=document.querySelectorAll(".aline-v24-filters select");
  const status=selects[0]?.value||"",source=selects[1]?.value||"";
  const rows=(A24.works||[]).filter(item=>(!search||`${item.work_name} ${item.company} ${item.client_name}`.toUpperCase().includes(search))&&(!status||item.status===status||item.status_manual===status||item.progress_status===status||item.deadline_status===status)&&(!source||item.source_profile===source));
  document.getElementById("a24WorkGrid").innerHTML=a24WorkGrid(rows);
};
window.alineWorkFormV24=async function(id,clientId,prefill={}){
  try{
    const [clients,item]=await Promise.all([a24Api("/clients"),id?a24Api(`/works/${id}`):Promise.resolve(null)]);
    const value=(name,fallback="")=>item?.[name]??prefill[name]??fallback;
    modal(`<h2>${item?"Editar demanda de obra":"Nova demanda para Processos"}</h2><form id="a24WorkForm" class="form-grid">
      <div class="form-group"><label>Cliente</label>${a24Select("client_id",clients,item?.client_id||clientId||prefill.client_id||"","Sem cliente vinculado")}</div>
      ${field("Nome da obra / demanda","work_name",value("work_name"),"text",true)}
      ${field("Empresa","company",value("company"))}
      <div class="form-group"><label>Origem</label>${a24Select("source_profile",["Projetos","Diretor","Exemplo","Processos"],value("source_profile",a24Profile()))}</div>
      ${field("Referência de origem","source_reference",value("source_reference"))}
      ${field("Prazo","deadline",value("deadline"),"date")}
      ${field("Responsável","responsible",value("responsible","Processos"))}
      <div class="form-group"><label>Prioridade</label>${a24Select("priority",["Baixa","Normal","Alta","Urgente"],value("priority","Normal"))}</div>
      <div class="form-group"><label>Status</label>${a24Select("status_manual",["Aguardando","Iniciado","Em andamento","Concluído"],value("status_manual","Aguardando"))}</div>
      <div class="form-group"><label>Aguardando ação de</label>${a24Select("waiting_for",["Prefeitura","Cartório","Cliente","Projetos","Diretor","Documentação"],value("waiting_for"),"Nenhuma dependência")}</div>
      ${field("Valor previsto","expected_value",value("expected_value",0),"number")}
      ${field("Valor cobrado","billed_value",value("billed_value",0),"number")}
      ${field("Valor pago","paid_value",value("paid_value",0),"number")}
      <div class="form-group" style="grid-column:1/-1"><label>Descrição da conferência</label><textarea name="description">${a24Esc(value("description"))}</textarea></div>
      <div class="form-group" style="grid-column:1/-1"><label>Observações de Projetos/Diretor/Processos</label><textarea name="observations">${a24Esc(value("observations"))}</textarea></div>
      <div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar demanda</button></div>
    </form>`);
    document.getElementById("a24WorkForm").onsubmit=async event=>{
      event.preventDefault();const values=Object.fromEntries(new FormData(event.target).entries());
      try{
        const saved=await a24Api(id?`/works/${id}`:"/works",{method:id?"PUT":"POST",body:JSON.stringify({data:values})});
        closeModal();showToast(saved.status==="Concluído"?"Demanda concluída e arquivada.":"Demanda salva para Processos.");alineWorkDetailV24(saved.id);
      }catch(error){alert(error.message)}
    };
  }catch(error){alert(error.message)}
};
window.alineWorkDetailV24=function(id){
  A24.selectedWork=id;a24Loading("Conferência de obra","Prazo, arquivos, observações e histórico.");
  a24Api(`/works/${id}`).then(item=>{
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${a24Nav()}
      <div class="toolbar"><button class="btn btn-light" onclick="currentArea='aline';page='alineWorks';render()">← Obras</button><div class="actions"><button class="btn btn-light" onclick="alineWorkFormV24(${id})">Editar</button><button class="btn btn-primary" onclick="alineUploadDocumentV24('work',${id})">Adicionar documento</button></div></div>
      ${a24Banner(item.work_name,`${item.source_profile} • ${item.client_name||item.company||"Sem cliente"}`)}
      <div class="aline-v24-split"><div>
        <div class="aline-v24-panel"><h3>Conferência</h3><div class="aline-v24-meta"><div><small>Andamento</small>${a24Status(item.progress_status||item.status_manual||item.status)}</div><div><small>Alerta do prazo</small>${a24Status(item.deadline_status||"OK")}</div><div><small>Responsável</small>${a24Esc(item.responsible)}</div><div><small>Prazo</small>${a24Deadline(item)}</div><div><small>Origem</small>${a24Esc(item.source_profile)}</div></div><p>${a24Esc(item.description||"Sem descrição.")}</p><p class="muted">${a24Esc(item.observations||"")}</p></div>
        <div class="aline-v24-panel" style="margin-top:16px"><h3>Histórico</h3><div class="aline-v24-timeline">${item.events.map(event=>`<div class="aline-v24-event"><strong>${a24Esc(event.event_type)}</strong><div>${a24Esc(event.description)}</div><small class="muted">${a24Date(event.event_date)} • ${a24Esc(event.actor)}</small></div>`).join("")||"Sem histórico."}</div></div>
      </div><div class="aline-v24-panel"><h3>Documentos de conferência</h3>${a24DocumentList(item.documents)}</div></div>
    </div>`;
  }).catch(a24Error);
};

function a24DocumentList(documents){
  if(!documents?.length) return a24Empty("Nenhum documento anexado.");
  return documents.map(document=>`<div class="aline-v24-doc"><div><strong>${a24Esc(document.title||document.category)}</strong><div class="muted">${a24Esc(document.canonical_name)} • versão ${document.version}${document.expiry_date?` • vence ${a24Date(document.expiry_date)}`:""}</div></div><a class="btn btn-light" href="${a24Esc(document.download_url)}" target="_blank">Abrir</a></div>`).join("");
}
window.alineUploadDocumentV24=function(ownerType,ownerId){
  modal(`<h2>Adicionar documento</h2><form id="a24DocumentForm" class="form-grid">
    ${field("Categoria","category","Documento","text",true)}${field("Título","title","")}
    ${field("Número / referência","document_number","")}${field("Data de emissão","issue_date","","date")}
    ${field("Validade / vencimento","expiry_date","","date")}
    <div class="form-group" style="grid-column:1/-1"><label>Arquivos *</label>${documentUploadField({inputId:"a24DocumentFiles",name:"files",required:true,help:"Arraste vários arquivos ou clique para selecionar. As informações acima serão aplicadas a todos."})}</div>
    <div class="form-group" style="grid-column:1/-1"><label>Observações</label><textarea name="notes"></textarea></div>
    <div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar nova versão</button></div>
  </form>`);
  document.getElementById("a24DocumentForm").onsubmit=async event=>{
    event.preventDefault();const form=event.target,files=uniqueDocumentFiles(form.elements.files.files);if(!files.length)return;
    try{
      const values=Object.fromEntries(new FormData(form).entries());delete values.files;
      for(const file of files){
        await a24Api("/documents",{method:"POST",body:JSON.stringify({...values,owner_type:ownerType,owner_id:ownerId,filename:file.name,data_url:await a24FileDataUrl(file)})});
      }
      closeModal();showToast(`${files.length} documento(s) e versão(ões) salvos.`);
      ownerType==="process"?alineProcessDetailV24(ownerId):ownerType==="work"?alineWorkDetailV24(ownerId):alineClientDetailV24(ownerId);
    }catch(error){alert(error.message)}
  };
};
function a24FileDataUrl(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file)})}

window.alineRegistriesV24=function(){
  a24Loading("Processos — Cartórios","Registros de imóveis, portais e cofre de credenciais.");
  Promise.all([a24Api("/registries"),a24Api("/vault/status")]).then(([rows,vault])=>{
    A24.registries=rows;
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${a24Nav()}
      ${a24Banner("Cartórios e Registros de Imóveis","O sistema guarda credenciais criptografadas e nunca realiza login automático.",
        `<button class="btn" onclick="alineRegistryFormV24()">Novo cartório</button>${vault.configured?"":`<button class="btn" onclick="alineVaultSetupV24()">Configurar cofre</button>`}`)}
      <div class="finance-note">Credenciais somente podem ser salvas ou reveladas com o PIN do cofre. O PIN não fica no navegador nem no histórico.</div>
      <div class="aline-v24-grid">${rows.map(item=>`<div class="aline-v24-card">
        <div class="aline-v24-card-head"><div><h3>${a24Esc(item.name)}</h3><div class="muted">${a24Esc(item.city||"")} • ${a24Esc(item.office_number||"")}</div></div><span class="aline-v24-status ${item.active?"ok":"done"}">${item.active?"Ativo":"Inativo"}</span></div>
        <div class="aline-v24-meta"><div><small>Credenciais</small>${item.has_credentials?"Protegidas no cofre":"Não cadastradas"}</div><div><small>Portal</small>${item.portal_url?"Configurado":"Não informado"}</div></div>
        <div class="aline-v24-actions">${item.portal_url?`<a class="btn btn-light" target="_blank" rel="noopener" href="${a24Esc(item.portal_url)}">Abrir portal</a>`:""}<button class="btn btn-light" onclick="alineRegistryFormV24(${item.id})">Editar</button><button class="btn btn-primary" onclick="alineRegistryCredentialsV24(${item.id})">Credenciais</button></div>
      </div>`).join("")}</div>
    </div>`;
  }).catch(a24Error);
};
window.alineRegistryFormV24=function(id){
  const item=(A24.registries||[]).find(row=>Number(row.id)===Number(id));
  modal(`<h2>${item?"Editar cartório":"Novo cartório"}</h2><form id="a24RegistryForm" class="form-grid">
    ${field("Nome","name",item?.name||"","text",true)}${field("Cidade","city",item?.city||"")}
    ${field("Número / Ofício","office_number",item?.office_number||"")}${field("Portal do cartório","portal_url",item?.portal_url||"","url")}
    ${field("RI Digital","ri_digital_url",item?.ri_digital_url||"https://ridigital.org.br/eProtocolo/listagem_contratos.aspx?comum=1","url")}
    <div class="form-group"><label>Situação</label>${a24Select("active",[{"id":"true","name":"Ativo"},{"id":"false","name":"Inativo"}],String(item?.active??true))}</div>
    <div class="form-group" style="grid-column:1/-1"><label>Observações</label><textarea name="notes">${a24Esc(item?.notes||"")}</textarea></div>
    <div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar</button></div>
  </form>`);
  document.getElementById("a24RegistryForm").onsubmit=async event=>{
    event.preventDefault();const data=Object.fromEntries(new FormData(event.target).entries());data.active=data.active==="true";
    try{await a24Api(id?`/registries/${id}`:"/registries",{method:id?"PUT":"POST",body:JSON.stringify({data})});closeModal();showToast("Cartório salvo.");alineRegistriesV24()}catch(error){alert(error.message)}
  };
};
window.alineVaultSetupV24=function(){
  modal(`<h2>Configurar cofre da Processos</h2><div class="finance-note">Crie um PIN com pelo menos 6 caracteres. Guarde-o em local seguro: ele não poderá ser recuperado pelo navegador.</div><form id="a24VaultForm" class="form-grid"><div class="form-group" style="grid-column:1/-1"><label>Novo PIN *</label><input name="pin" type="password" minlength="6" required autocomplete="new-password"></div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Criar cofre</button></div></form>`);
  document.getElementById("a24VaultForm").onsubmit=async event=>{event.preventDefault();try{await a24Api("/vault/setup",{method:"POST",body:JSON.stringify({pin:event.target.elements.pin.value})});closeModal();showToast("Cofre configurado.");alineRegistriesV24()}catch(error){alert(error.message)}};
};
window.alineRegistryCredentialsV24=function(id){
  modal(`<h2>Salvar credenciais do cartório</h2><div class="finance-note">Os dados serão criptografados no servidor. O sistema não fará login automático.</div><form id="a24CredentialsForm" class="form-grid"><div class="form-group"><label>Número de acesso / usuário</label><input name="access_number" autocomplete="off"></div><div class="form-group"><label>Senha</label><input name="password" type="password" autocomplete="new-password"></div><div class="form-group" style="grid-column:1/-1"><label>PIN do cofre *</label><input name="pin" type="password" required autocomplete="off"></div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar criptografado</button></div></form>`);
  document.getElementById("a24CredentialsForm").onsubmit=async event=>{event.preventDefault();const form=event.target;try{await a24Api(`/registries/${id}/credentials`,{method:"PUT",headers:{"X-Processos-Vault-Pin":form.elements.pin.value},body:JSON.stringify({access_number:form.elements.access_number.value,password:form.elements.password.value})});closeModal();showToast("Credenciais salvas no cofre.");alineRegistriesV24()}catch(error){alert(error.message)}};
};
window.alineRevealRegistryV24=function(id){
  modal(`<h2>Abrir credenciais</h2><form id="a24RevealForm" class="form-grid"><div class="form-group" style="grid-column:1/-1"><label>PIN do cofre *</label><input name="pin" type="password" required autocomplete="off"></div><div id="a24Revealed" style="grid-column:1/-1"></div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Fechar</button><button class="btn btn-primary">Revelar</button></div></form>`);
  document.getElementById("a24RevealForm").onsubmit=async event=>{event.preventDefault();try{const result=await a24Api(`/registries/${id}/credentials/reveal`,{method:"POST",headers:{"X-Processos-Vault-Pin":event.target.elements.pin.value}});document.getElementById("a24Revealed").innerHTML=`<div class="aline-v24-secret"><b>Número de acesso:</b> ${a24Esc(result.access_number||"não informado")}<br><b>Senha:</b> ${a24Esc(result.password||"não informada")}</div>`}catch(error){alert(error.message)}};
};
window.alineProcessCredentialsV24=function(id){
  modal(`<h2>Credenciais deste protocolo</h2><div class="finance-note">Use para número de acesso e senha específicos do processo. Os valores serão criptografados e não aparecem no histórico.</div><form id="a24ProcessCredentialsForm" class="form-grid"><div class="form-group"><label>Número de acesso / usuário</label><input name="access_number" autocomplete="off"></div><div class="form-group"><label>Senha</label><input name="password" type="password" autocomplete="new-password"></div><div class="form-group" style="grid-column:1/-1"><label>PIN do cofre *</label><input name="pin" type="password" required autocomplete="off"></div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar criptografado</button></div></form>`);
  document.getElementById("a24ProcessCredentialsForm").onsubmit=async event=>{event.preventDefault();const form=event.target;try{await a24Api(`/processes/${id}/credentials`,{method:"PUT",headers:{"X-Processos-Vault-Pin":form.elements.pin.value},body:JSON.stringify({access_number:form.elements.access_number.value,password:form.elements.password.value})});closeModal();showToast("Credenciais do protocolo salvas.");alineProcessDetailV24(id)}catch(error){alert(error.message)}};
};
window.alineRevealProcessV24=function(id){
  modal(`<h2>Abrir credenciais do protocolo</h2><form id="a24RevealProcessForm" class="form-grid"><div class="form-group" style="grid-column:1/-1"><label>PIN do cofre *</label><input name="pin" type="password" required autocomplete="off"></div><div id="a24RevealedProcess" style="grid-column:1/-1"></div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Fechar</button><button class="btn btn-primary">Revelar</button></div></form>`);
  document.getElementById("a24RevealProcessForm").onsubmit=async event=>{event.preventDefault();try{const result=await a24Api(`/processes/${id}/credentials/reveal`,{method:"POST",headers:{"X-Processos-Vault-Pin":event.target.elements.pin.value}});document.getElementById("a24RevealedProcess").innerHTML=`<div class="aline-v24-secret"><b>Número de acesso:</b> ${a24Esc(result.access_number||"não informado")}<br><b>Senha:</b> ${a24Esc(result.password||"não informada")}</div>`}catch(error){alert(error.message)}};
};

window.alineNotificationsV24=function(){
  a24Loading("Processos — Notificações","Prazos e pendências que precisam de atenção.");
  a24Api("/notifications").then(rows=>{
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${a24Nav()}${a24Banner("Notificações de prazos","Urgente: vencido ou até 2 dias. Perto do vencimento: até 10 dias. Aguardando: depende de terceiro.")}<div class="aline-v24-panel">${rows.length?rows.map(a24NotificationRow).join(""):a24Empty("Nenhuma notificação pendente.")}</div></div>`;
  }).catch(a24Error);
};
window.alineFinalizedV24=function(){
  a24Loading("Processos — Finalizados","Histórico arquivado, preservado e consultável.");
  Promise.all([a24Api("/processes?archived=true"),a24Api("/works?archived=true"),a24Api("/clients?archived=true")]).then(([processes,works,clients])=>{
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${a24Nav()}
      ${a24Banner("Finalizados","Itens concluídos ficam ocultos das demandas ativas, mas nunca são apagados.")}
      <div class="aline-v24-panel"><h3>Processos concluídos (${processes.length})</h3>${a24ProcessGrid(processes)}</div>
      <div class="aline-v24-panel"><h3>Obras concluídas (${works.length})</h3>${a24WorkGrid(works)}</div>
      <div class="aline-v24-panel"><h3>Clientes arquivados (${clients.length})</h3>${clients.length?a24ClientGrid(clients):a24Empty("Nenhum cliente arquivado.")}</div>
    </div>`;
  }).catch(a24Error);
};

window.alineImportSpreadsheetV24=function(){
  modal(`<h2>Importar relação de processos</h2><div class="finance-note">A importação é idempotente: a mesma planilha não cria processos duplicados. Os protocolos S/P só são preenchidos quando a origem os identifica explicitamente; os demais ficam em referência histórica para revisão.</div><form id="a24ImportForm" class="form-grid"><div class="form-group" style="grid-column:1/-1"><label>Planilha XLSX *</label><input name="file" type="file" accept=".xlsx" required></div><div id="a24ImportResult" style="grid-column:1/-1"></div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Fechar</button><button class="btn btn-primary">Importar e migrar</button></div></form>`);
  document.getElementById("a24ImportForm").onsubmit=async event=>{event.preventDefault();const file=event.target.elements.file.files[0];if(!file)return;try{const result=await a24Api("/imports/spreadsheet",{method:"POST",body:JSON.stringify({filename:file.name,data_url:await a24FileDataUrl(file)})});document.getElementById("a24ImportResult").innerHTML=`<div class="aline-v24-secret"><b>${result.imported}</b> processo(s) importado(s), <b>${result.skipped}</b> ignorado(s). ${result.review_required} registro(s) exigem revisão dos códigos S/P.</div>`;showToast("Migração concluída.")}catch(error){alert(error.message)}};
};

async function a24EnsureProjectClient(client){
  const rows=await a24Api(`/clients?search=${encodeURIComponent(client.name||"")}`);
  let match=rows.find(row=>String(row.name).toUpperCase()===String(client.name||"").toUpperCase());
  if(match) return match;
  return a24Api("/clients",{method:"POST",headers:{"X-Sistema Portfólio-Profile":"Projetos"},body:JSON.stringify({data:{
    person_type:String(client.cpfCnpj||client.cpf_cnpj||"").replace(/\D/g,"").length>11?"PJ":"PF",
    name:client.name||"Cliente da Projetos",cpf_cnpj:client.cpfCnpj||client.cpf_cnpj||"",
    phone:client.whatsapp||client.phone||"",email:client.email||"",
    city:client.landCityState||"",address:client.landLocation||"",
    notes:"Cliente vinculado automaticamente a uma demanda enviada por Projetos/Diretor."
  }})});
}
window.alineCreateWorkDemandFromHelenaV24=async function(clientId,sourceProfile="Projetos"){
  try{
    const client=typeof projectClientById==="function"?projectClientById(clientId):null;
    if(!client) throw new Error("Cliente da Projetos não encontrado.");
    const central=await a24EnsureProjectClient(client);
    alineWorkFormV24(null,central.id,{
      source_profile:sourceProfile,source_reference:`Projetos:${clientId}`,
      work_name:`${client.serviceType||"Conferência"} — ${client.name}`,
      company:client.company||"",description:`Conferir documentos e projeto após etapa de campo. Local: ${client.landLocation||"não informado"}.`,
      observations:[client.directorNote,client.notes,client.teamObservation].filter(Boolean).join("\n"),
      status_manual:"Aguardando"
    });
  }catch(error){alert(error.message)}
};
window.alineCreateWorkDemandFromDirectorV24=function(requestId){
  const request=(data.projectScheduleRequests||[]).find(item=>Number(item.id)===Number(requestId));
  if(!request){alert("Solicitação não encontrada.");return}
  alineCreateWorkDemandFromHelenaV24(request.clientId,"Diretor");
};

/* API interna exposta apenas para a camada visual 2.5. */
window.A25Core={
  api:a24Api,esc:a24Esc,date:a24Date,money:a24Money,status:a24Status,
  statusClass:a24StatusClass,select:a24Select,kpi:a24Kpi,banner:a24Banner,
  empty:a24Empty,deadline:a24Deadline,deadlineLabel:a25DeadlineLabel,
  loading:a24Loading,error:a24Error,profile:a24Profile
};
a24DocumentList=function(documents){
  if(!documents?.length) return a24Empty("Nenhum documento anexado.");
  return documents.map(document=>`<div class="aline-v24-doc"><div><strong>${a24Esc(document.title||document.category)}</strong><div class="muted">${a24Esc(document.canonical_name)} • versão ${document.version}${document.expiry_date?` • vence ${a24Date(document.expiry_date)}`:""}</div></div><div class="actions"><a class="btn btn-light" href="${a24Esc(document.download_url)}" target="_blank" rel="noopener">Abrir</a><button class="btn btn-danger-outline" onclick="alineDeleteV25('document',${document.id})">Excluir</button></div></div>`).join("");
};
window.alineRefreshBadgeV24=function(){
  a24Api("/notifications?unread_only=true").then(rows=>{
    const button=document.querySelector('[data-area="aline"][data-page="alineNotifications"]');
    if(!button)return;
    let badge=button.querySelector(".nav-badge");
    if(!badge){badge=document.createElement("span");badge.className="nav-badge";button.appendChild(badge)}
    badge.textContent=String(rows.length);badge.classList.toggle("hidden",!rows.length);
  }).catch(()=>{});
};

document.addEventListener("DOMContentLoaded",()=>{
  setTimeout(alineRefreshBadgeV24,900);
  setInterval(alineRefreshBadgeV24,60000);
});
})();
