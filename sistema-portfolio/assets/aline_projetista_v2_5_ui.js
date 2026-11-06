(function(){
"use strict";

const C=window.A25Core;
const S=window.A24;
const MAIN_CITIES=["Biguaçu","Florianópolis","São José","Palhoça"];
const OTHER_CITIES=["Balneário Camboriú","Blumenau","Brusque","Chapecó","Criciúma","Itajaí","Jaraguá do Sul","Joinville","Lages","Navegantes","Rio do Sul","Tijucas","Tubarão"];
const CITIES=[...MAIN_CITIES,...OTHER_CITIES];
const DEPENDENCIES=["Prefeitura","Cartório","Cliente","Projetos","Diretor","Documentação"];
const legacyProcessForm=window.alineProcessFormV24;

function norm(value){
  return String(value||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim().toLowerCase();
}
function sameCity(value,city){
  const source=norm(value),target=norm(city);
  return source===target||source.includes(target)||target.includes(source);
}
function state(item){
  if(item.deadline_state)return item.deadline_state;
  const days=item.days_to_deadline;
  if(days===null||days===undefined)return "SEM PRAZO";
  if(Number(days)<0)return "VENCIDO";
  if(Number(days)<=2)return "PRAZO A VENCER";
  if(Number(days)<=10)return "PRÓXIMO";
  return "NORMAL";
}
function deadline(item){
  const status=state(item);
  const css=status==="VENCIDO"?"overdue":status==="PRAZO A VENCER"?"urgent":status==="PRÓXIMO"?"near":"normal";
  const label=item.deadline_label||C.deadlineLabel(item.days_to_deadline);
  const dates=item.deadline_dates?.length?item.deadline_dates:(item.deadline_date?[{kind:"Data do prazo",date:item.deadline_date}]:[]);
  return `<span class="aline-v25-deadline-stack"><span class="aline-v25-deadline ${css}">${status==="PRÓXIMO"?'<i></i>':""}${C.esc(label)}</span>${dates.map(entry=>`<span class="aline-v25-deadline-date">${C.esc(entry.kind)}: <strong>${C.date(entry.date)}</strong></span>`).join("")}</span>`;
}
function processNav(){
  const top=["",...MAIN_CITIES].map(city=>`<button class="${S.processCityMode!=="other"&&S.processCity===city?"active":""}" onclick="alineSetProcessCityV25('${C.esc(city)}')">${city||"Visão geral"}</button>`).join("")+`<button class="${S.processCityMode==="other"?"active":""}" onclick="alineSetOtherCitiesV26()">Outras</button>`;
  const other=S.processCityMode==="other"?`<div class="aline-v26-other-city"><label for="a26OtherCity"><strong>Outra cidade de Santa Catarina</strong><small>Escolha a cidade para visualizar os processos.</small></label><select id="a26OtherCity" onchange="alineSelectOtherCityV26(this.value)"><option value="">Selecione uma cidade</option>${OTHER_CITIES.map(city=>`<option value="${C.esc(city)}" ${S.processCity===city?"selected":""}>${C.esc(city)}</option>`).join("")}</select></div>`:"";
  const second=S.processCity?`<div class="aline-tabs aline-v25-secondary-tabs">${
    [["clients","Clientes"],["processes","Processos"],["works","Obras"],["registries","Cartórios e registros de imóveis"],["notifications","Notificações"],["finalized","Finalizados"]]
      .map(([key,label])=>`<button class="${S.citySection===key?"active":""}" onclick="alineSetCitySectionV25('${key}')">${label}</button>`).join("")
  }</div>`:"";
  return `<div class="aline-tabs aline-v25-primary-tabs">${top}</div>${other}${second}`;
}
window.alineProcessNavV26=processNav;
function workNav(){
  return `<div class="aline-tabs aline-v25-primary-tabs">${
    [["overview","Visão geral"],["demands","Demandas"],["progress","Em andamento"],["deadlines","Prazos"],["completed","Concluídas"]]
      .map(([key,label])=>`<button class="${S.workSection===key?"active":""}" onclick="alineSetWorkSectionV25('${key}')">${label}</button>`).join("")
  }</div>`;
}
function clientsGrid(rows){
  if(!rows.length)return C.empty("Nenhum cliente nesta seleção.");
  return `<div class="aline-v24-grid">${rows.map(item=>`<article class="aline-v24-card" data-a25-search="${C.esc(norm(`${item.name} ${item.cpf_cnpj}`))}">
    <div class="aline-v24-card-head"><div><h3>${C.esc(item.name)}</h3><div class="muted">${item.person_type==="PJ"?"Pessoa jurídica":"Pessoa física"} • ${C.esc(item.city||"Cidade não informada")}</div></div><span class="aline-v24-status ok">${C.esc(item.person_type)}</span></div>
    <div class="aline-v24-meta"><div><small>${item.person_type==="PJ"?"CNPJ":"CPF"}</small>${C.esc(item.cpf_cnpj||"Não informado")}</div><div><small>Processos</small>${item.process_count||0}</div><div><small>Obras</small>${item.work_count||0}</div></div>
    <div class="aline-v24-actions"><button class="btn btn-primary" onclick="alineClientDetailV24(${item.id})">Abrir pasta</button><button class="btn btn-light" onclick="alineClientFormV24(${item.id})">Editar</button><button class="btn btn-danger-outline" onclick="alineDeleteV25('client',${item.id})">Excluir</button></div>
  </article>`).join("")}</div>`;
}
function processesGrid(rows){
  if(!rows.length)return C.empty("Nenhum processo nesta seleção.");
  return `<div class="aline-v24-grid">${rows.map(item=>`<article class="aline-v24-card">
    <div class="aline-v24-card-head"><div><h3>${C.esc(item.title)}</h3><div class="muted">${C.esc(item.client_name||"")} • ${C.esc(item.process_type)} • ${C.esc(item.city)}</div></div>${C.status(item.waiting_for?"Aguardando":item.status)}</div>
    <div class="aline-v24-meta"><div><small>Serviço</small>${C.esc(item.service_type)}</div><div><small>Prazo</small>${deadline(item)}</div><div><small>Terceiro código</small>${C.esc(item.third_code||"Não informado")}</div><div><small>Dependência</small>${C.esc(item.waiting_for?`AGUARDANDO — ação de ${item.waiting_for}`:"Nenhuma")}</div></div>
    <div class="aline-v24-actions"><button class="btn btn-primary" onclick="alineProcessDetailV24(${item.id})">Abrir</button><button class="btn btn-light" onclick="alineProcessFormV24(${item.id})">Editar</button><button class="btn btn-light" onclick="alineWaitingV25('process',${item.id})">Dependência</button><button class="btn btn-danger-outline" onclick="alineDeleteV25('process',${item.id})">Excluir</button></div>
  </article>`).join("")}</div>`;
}
function processSearch(rows){
  S.a25SearchSource=rows;
  return `<div class="aline-v25-search"><label for="a25ProcessSearch"><strong>Pesquisar processos</strong><small>Localize pelos três códigos ou pelo cliente.</small></label><input id="a25ProcessSearch" type="search" placeholder="Digite um código ou o cliente" oninput="alineSearchProcessesV25()"></div><div id="a25ProcessSearchResults">${processesGrid(rows)}</div>`;
}
function worksGrid(rows){
  if(!rows.length)return C.empty("Nenhuma demanda de obra nesta seleção.");
  return `<div class="aline-v24-grid">${rows.map(item=>`<article class="aline-v24-card">
    <div class="aline-v24-card-head"><div><h3>${C.esc(item.work_name)}</h3><div class="muted">${C.esc(item.source_profile)} • ${C.esc(item.client_name||item.company||"Sem cliente")}</div></div>${C.status(item.progress_status||item.status_manual||item.status)}</div>
    <div class="aline-v24-meta"><div><small>Prazo</small>${deadline(item)}</div><div><small>Responsável</small>${C.esc(item.responsible||"Processos")}</div><div><small>Dependência</small>${C.esc(item.waiting_for?`AGUARDANDO — ação de ${item.waiting_for}`:"Nenhuma")}</div><div><small>Valor previsto</small>${C.money(item.expected_value)}</div></div>
    <div class="aline-v24-actions"><button class="btn btn-primary" onclick="alineWorkDetailV24(${item.id})">Abrir</button><button class="btn btn-light" onclick="alineWorkFormV24(${item.id})">Editar</button><button class="btn btn-light" onclick="alineWaitingV25('work',${item.id})">Dependência</button><button class="btn btn-danger-outline" onclick="alineDeleteV25('work',${item.id})">Excluir</button></div>
  </article>`).join("")}</div>`;
}
function registriesGrid(rows,vault){
  if(!rows.length)return C.empty("Nenhum cartório ou registro cadastrado nesta cidade.");
  return `<div class="aline-v24-grid">${rows.map(item=>`<article class="aline-v24-card">
    <div class="aline-v24-card-head"><div><h3>${C.esc(item.name)}</h3><div class="muted">${C.esc(item.city||"")} • ${C.esc(item.office_number||"")}</div></div><span class="aline-v24-status ${item.active?"ok":"done"}">${item.active?"Ativo":"Inativo"}</span></div>
    <div class="aline-v24-meta"><div><small>Credenciais</small>${item.has_credentials?"Protegidas no cofre":"Não cadastradas"}</div><div><small>Portal</small>${item.portal_url?"Configurado":"Não informado"}</div></div>
    <div class="aline-v24-actions">${item.portal_url?`<a class="btn btn-light" target="_blank" rel="noopener" href="${C.esc(item.portal_url)}">Abrir portal</a>`:""}<button class="btn btn-light" onclick="alineRegistryFormV24(${item.id})">Editar</button><button class="btn btn-primary" onclick="alineRegistryCredentialsV24(${item.id})">Credenciais</button><button class="btn btn-danger-outline" onclick="alineDeleteV25('registry',${item.id})">Excluir</button></div>
  </article>`).join("")}</div>${vault&&!vault.configured?`<button class="btn btn-light" onclick="alineVaultSetupV24()">Configurar cofre protegido</button>`:""}`;
}
function notificationsList(rows){
  if(!rows.length)return C.empty("Nenhuma pendência importante no momento.");
  return rows.map(item=>`<div class="aline-v25-notification ${C.esc(state(item).toLowerCase().replace(/\s/g,"-"))}">
    <div><div class="aline-v25-notification-title">${C.esc(item.title)}</div><div>${C.esc(item.message)}</div><small class="muted">${C.esc(item.city||"")}${item.deadline||item.due_date?` • prazo em <strong>${C.date(item.deadline||item.due_date)}</strong>`:""}</small></div>
    <div class="actions"><button class="btn btn-primary" onclick="alineOpenNotificationV24('${C.esc(item.key)}','${C.esc(item.entity_type)}',${item.entity_id})">Abrir</button><button class="btn btn-danger-outline" onclick="alineDeleteV25('notification','${C.esc(item.key)}')">Excluir</button></div>
  </div>`).join("");
}
function portalsGrid(rows,city=""){
  const filtered=rows.filter(item=>item.active!==false&&(!city||!item.city||sameCity(item.city,city)));
  if(!filtered.length)return C.empty("Nenhum link ativo para esta seleção.");
  return `<div class="aline-v25-portal-grid">${filtered.map(item=>`<a class="aline-v25-portal" target="_blank" rel="noopener" href="${C.esc(item.url)}"><span>${C.esc(item.portal_type||"Portal")}</span><strong>${C.esc(item.name)}</strong><small>${C.esc(item.city||"Todas as cidades")} ↗</small></a>`).join("")}</div>`;
}
async function loadAll(){
  const [clients,processes,works,registries,notifications,portals,archivedProcesses,archivedWorks,archivedClients,vault]=await Promise.all([
    C.api("/clients"),C.api("/processes"),C.api("/works"),C.api("/registries"),
    C.api("/notifications?unread_only=false"),C.api("/portal-links"),
    C.api("/processes?archived=true"),C.api("/works?archived=true"),C.api("/clients?archived=true"),C.api("/vault/status")
  ]);
  return {clients,processes,works,registries,notifications,portals,archivedProcesses,archivedWorks,archivedClients,vault};
}
function cityData(all,city){
  const processes=all.processes.filter(item=>sameCity(item.city,city));
  const clientIds=new Set(processes.map(item=>Number(item.client_id)));
  all.clients.forEach(item=>{if(sameCity(item.city,city))clientIds.add(Number(item.id))});
  const clients=all.clients.filter(item=>clientIds.has(Number(item.id)));
  const works=all.works.filter(item=>clientIds.has(Number(item.client_id))||sameCity(item.client_city,city));
  const registries=all.registries.filter(item=>sameCity(item.city,city));
  const notifications=all.notifications.filter(item=>sameCity(item.city,city));
  const archivedProcesses=all.archivedProcesses.filter(item=>sameCity(item.city,city));
  const archivedIds=new Set(archivedProcesses.map(item=>Number(item.client_id)));
  const archivedWorks=all.archivedWorks.filter(item=>archivedIds.has(Number(item.client_id))||sameCity(item.client_city,city));
  const archivedClients=all.archivedClients.filter(item=>archivedIds.has(Number(item.id))||sameCity(item.city,city));
  return {...all,clients,processes,works,registries,notifications,archivedProcesses,archivedWorks,archivedClients};
}
function valuesSummary(processes,works){
  const all=[...processes,...works];
  const expected=all.reduce((sum,item)=>sum+Number(item.expected_value||0),0);
  const billed=all.reduce((sum,item)=>sum+Number(item.billed_value||0),0);
  const paid=all.reduce((sum,item)=>sum+Number(item.paid_value||0),0);
  return `<div class="aline-v24-kpis">${C.kpi("Previsto",C.money(expected),"processos e obras")}${C.kpi("Cobrado",C.money(billed),"valor enviado")}${C.kpi("Pago",C.money(paid),"valor recebido")}</div>`;
}
function overview(all){
  const overdue=all.processes.filter(item=>state(item)==="VENCIDO").length;
  const near=all.processes.filter(item=>["PRAZO A VENCER","PRÓXIMO"].includes(state(item))).length;
  const waiting=all.processes.filter(item=>item.waiting_for).length;
  return `${C.banner("Visão geral dos processos","Todas as cidades reunidas, com prazos calculados pela data da consulta.",`<button class="btn" onclick="alineProcessFormV24()">Novo processo</button><button class="btn" onclick="alineImportSpreadsheetV24()">Importar planilha</button>`)}
    <div class="aline-v24-kpis">${C.kpi("Clientes",all.clients.length,"cadastros ativos")}${C.kpi("Processos ativos",all.processes.length,"prefeitura e cartório")}${C.kpi("Obras relacionadas",all.works.length,"demandas ativas")}${C.kpi("Vencidos",overdue,"exigem ação","overdue")}${C.kpi("Próximos do prazo",near,"até 10 dias","urgent")}${C.kpi("Aguardando",waiting,"dependência identificada","waiting")}</div>
    ${valuesSummary(all.processes,all.works)}
    <div class="aline-v24-panel"><div class="toolbar" style="margin:0 0 12px"><h3 style="margin:0">Links rápidos</h3><button class="btn btn-light" onclick="alinePortalSettingsV25()">Configurações dos links</button></div>${portalsGrid(all.portals)}</div>
    <div class="aline-v24-panel"><h3>Pesquisa rápida</h3>${processSearch(all.processes)}</div>
    <div class="aline-v24-panel"><h3>Prazos e pendências importantes</h3>${notificationsList(all.notifications.slice(0,12))}</div>`;
}
function cityView(all,city){
  const d=cityData(all,city);
  const header=C.banner(city,`Dados, documentos e prazos filtrados exclusivamente para ${city}.`,`<button class="btn" onclick="alineProcessFormV24()">Novo processo</button>`);
  if(S.citySection==="clients")return `${header}<div class="toolbar" style="margin:0 0 12px"><input id="a25ClientSearch" placeholder="Buscar cliente, CPF ou CNPJ" oninput="alineFilterCityClientsV25()"><button class="btn btn-primary" onclick="alineClientFormV24()">Novo cliente</button></div><div id="a25CityClients">${clientsGrid(d.clients)}</div>`;
  if(S.citySection==="processes")return `${header}${processSearch(d.processes)}`;
  if(S.citySection==="works")return `${header}${worksGrid(d.works)}`;
  if(S.citySection==="registries")return `${header}<div class="toolbar" style="margin:0 0 12px"><h3 style="margin:0">Cartórios e registros de imóveis</h3><button class="btn btn-primary" onclick="alineRegistryFormV24()">Novo cadastro</button></div>${registriesGrid(d.registries,d.vault)}`;
  if(S.citySection==="notifications")return `${header}<div class="aline-v24-panel">${notificationsList(d.notifications)}</div>`;
  return `${header}<div class="aline-v24-panel"><h3>Processos finalizados</h3>${processesGrid(d.archivedProcesses)}</div><div class="aline-v24-panel"><h3>Obras concluídas</h3>${worksGrid(d.archivedWorks)}</div><div class="aline-v24-panel"><h3>Clientes arquivados</h3>${clientsGrid(d.archivedClients)}</div>`;
}

window.alineSetProcessCityV25=function(city){
  S.processCity=city;S.processCityMode=city?"main":"";S.citySection="clients";S.selectedClient=null;S.selectedProcess=null;
  page="alineProcesses";alineProcessesV25();
};
window.alineSetOtherCitiesV26=function(){
  S.processCityMode="other";
  if(!OTHER_CITIES.includes(S.processCity))S.processCity="";
  S.citySection="clients";S.selectedClient=null;S.selectedProcess=null;page="alineProcesses";alineProcessesV25();
};
window.alineSelectOtherCityV26=function(city){
  S.processCityMode="other";S.processCity=OTHER_CITIES.includes(city)?city:"";
  S.citySection="clients";page="alineProcesses";alineProcessesV25();
};
window.alineSetCitySectionV25=function(section){S.citySection=section;page="alineProcesses";alineProcessesV25()};
window.alineFilterCityClientsV25=function(){
  const query=norm(document.getElementById("a25ClientSearch")?.value);
  document.querySelectorAll("[data-a25-search]").forEach(card=>card.hidden=query&&!card.dataset.a25Search.includes(query));
};
window.alineSearchProcessesV25=function(){
  const query=norm(document.getElementById("a25ProcessSearch")?.value);
  const rows=(S.a25SearchSource||[]).filter(item=>!query||norm(`${item.entry_code||""} ${item.secondary_code||""} ${item.third_code||""} ${item.client_name||""}`).includes(query));
  const target=document.getElementById("a25ProcessSearchResults");if(target)target.innerHTML=processesGrid(rows);
};
window.alineProcessesV25=function(){
  C.loading("Processos — Processos","Processos organizados por cidade, cliente e prazo.");
  loadAll().then(all=>{
    S.clients=all.clients;S.processes=all.processes;S.registries=all.registries;S.a25All=all;
    const body=S.processCity?cityView(all,S.processCity):S.processCityMode==="other"?C.empty("Selecione uma das outras cidades de Santa Catarina para continuar."):overview(all);
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${processNav()}${body}</div>`;
  }).catch(C.error);
};
window.alineSetWorkSectionV25=function(section){S.workSection=section;page="alineWorks";alineWorksV25()};
window.alineWorksV25=function(){
  C.loading("Processos — Obras","Demandas de conferência recebidas de Projetos e do Diretor.");
  Promise.all([C.api("/works"),C.api("/works?archived=true")]).then(([works,archived])=>{
    S.works=works;const section=S.workSection;
    const progress=works.filter(item=>["Iniciado","Em andamento"].includes(item.progress_status||item.status_manual));
    const deadlines=works.filter(item=>["VENCIDO","PRAZO A VENCER","PRÓXIMO"].includes(state(item))).sort((a,b)=>Number(a.days_to_deadline)-Number(b.days_to_deadline));
    let body="";
    if(section==="overview")body=`${C.banner("Visão geral das obras","Demandas, andamento e prazos em uma única visão.",`<button class="btn" onclick="alineWorkFormV24()">Nova demanda</button>`)}<div class="aline-v24-kpis">${C.kpi("Demandas",works.length,"ativas")}${C.kpi("Em andamento",progress.length,"iniciadas")}${C.kpi("Com alerta",deadlines.length,"prazo em até 10 dias")}${C.kpi("Concluídas",archived.length,"arquivadas")}</div><div class="aline-v24-panel"><h3>Demandas mais recentes</h3>${worksGrid(works.slice(0,9))}</div>`;
    if(section==="demands")body=`${C.banner("Demandas","Solicitações novas e aguardando início.",`<button class="btn" onclick="alineWorkFormV24()">Nova demanda</button>`)}${worksGrid(works.filter(item=>["Aguardando","OK"].includes(item.progress_status||item.status_manual||item.status)))}`;
    if(section==="progress")body=`${C.banner("Em andamento","Conferências que já foram iniciadas.")}${worksGrid(progress)}`;
    if(section==="deadlines")body=`${C.banner("Prazos","Situação calculada pela data atual, sem textos genéricos.")}${worksGrid(deadlines)}`;
    if(section==="completed")body=`${C.banner("Concluídas","Itens arquivados automaticamente, preservados para consulta e exclusão autorizada.")}${worksGrid(archived)}`;
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${workNav()}${body}</div>`;
  }).catch(C.error);
};
function calendarMonthValue(){
  if(S.calendarMonth)return S.calendarMonth;
  const now=new Date();S.calendarMonth=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}`;return S.calendarMonth;
}
function calendarRange(){
  const [year,month]=calendarMonthValue().split("-").map(Number);
  const last=new Date(year,month,0).getDate();
  return {year,month,from:`${year}-${String(month).padStart(2,"0")}-01`,to:`${year}-${String(month).padStart(2,"0")}-${String(last).padStart(2,"0")}`};
}
function calendarGrid(events,range){
  const offset=new Date(range.year,range.month-1,1).getDay(),last=Number(range.to.slice(-2));
  const cells=Array(offset).fill(null).concat(Array.from({length:last},(_,index)=>index+1));
  return `<div class="aline-v25-calendar-grid">${["Dom","Seg","Ter","Qua","Qui","Sex","Sáb"].map(day=>`<div class="aline-v25-calendar-head">${day}</div>`).join("")}${cells.map(day=>{
    if(!day)return '<div class="aline-v25-calendar-day empty"></div>';
    const iso=`${calendarMonthValue()}-${String(day).padStart(2,"0")}`,items=events.filter(item=>item.date===iso);
    return `<div class="aline-v25-calendar-day"><strong>${day}</strong>${items.map(item=>`<button class="aline-v25-calendar-event ${C.esc(String(item.status||"").toLowerCase().replace(/\s/g,"-"))}" onclick="alineOpenCalendarEventV25('${C.esc(item.entity_type)}',${Number(item.entity_id)})" title="${C.esc(item.deadline_label)}">${C.esc(item.title)}</button>`).join("")}</div>`;
  }).join("")}</div><div class="aline-v25-calendar-list">${events.map(item=>`<button onclick="alineOpenCalendarEventV25('${C.esc(item.entity_type)}',${Number(item.entity_id)})"><strong>${C.date(item.date)}</strong><span><b>${C.esc(item.title)}</b><small>${C.esc(item.client||item.city||"")} • ${C.esc(item.deadline_label)}</small></span></button>`).join("")||C.empty("Nenhum prazo ou vencimento neste mês.")}</div>`;
}
window.alineCalendarV25=function(){
  C.loading("Processos — Calendário","Prazos de processos, obras e validade de documentos.");
  const range=calendarRange(),city=S.calendarCity||"";
  C.api(`/calendar?date_from=${range.from}&date_to=${range.to}&city=${encodeURIComponent(city)}`).then(result=>{
    const events=result.events||[];
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${C.banner("Calendário de notificações e prazos","Eventos calculados diretamente dos registros, sem criar cópias no banco.")}
      <div class="aline-v25-calendar-toolbar"><div class="actions"><button class="btn btn-light" onclick="alineChangeCalendarMonthV25(-1)">←</button><input type="month" value="${calendarMonthValue()}" onchange="A24.calendarMonth=this.value;alineCalendarV25()"><button class="btn btn-light" onclick="alineChangeCalendarMonthV25(1)">→</button></div><label>Cidade ${C.select("calendar_city",CITIES,city,"Todas as cidades")}</label></div>
      <div class="aline-v24-kpis">${C.kpi("Eventos",events.length,"no mês")}${C.kpi("Vencidos",events.filter(item=>item.status==="VENCIDO").length,"exigem atenção","overdue")}${C.kpi("Até 2 dias",events.filter(item=>item.status==="PRAZO A VENCER").length,"urgentes","urgent")}${C.kpi("Documentos",events.filter(item=>item.kind==="document").length,"validade no período")}</div>
      ${calendarGrid(events,range)}</div>`;
    const select=document.querySelector('[name="calendar_city"]');if(select)select.onchange=()=>{S.calendarCity=select.value;alineCalendarV25()};
  }).catch(C.error);
};
window.alineChangeCalendarMonthV25=function(delta){
  const range=calendarRange(),next=new Date(range.year,range.month-1+delta,1);
  S.calendarMonth=`${next.getFullYear()}-${String(next.getMonth()+1).padStart(2,"0")}`;alineCalendarV25();
};
window.alineOpenCalendarEventV25=function(type,id){
  if(type==="process")alineProcessDetailV24(id);else if(type==="work")alineWorkDetailV24(id);else if(type==="client")alineClientDetailV24(id);
};
window.alineNotificationsV25=function(){
  C.loading("Processos — Notificações","Somente prazos e pendências que realmente exigem atenção.");
  C.api("/notifications?unread_only=false").then(rows=>{
    const overdue=rows.filter(item=>state(item)==="VENCIDO").length;
    const due=rows.filter(item=>state(item)==="PRAZO A VENCER").length;
    const near=rows.filter(item=>state(item)==="PRÓXIMO").length;
    const waiting=rows.filter(item=>String(item.message||"").includes("AGUARDANDO")).length;
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">${C.banner("Notificações importantes","A lista muda conforme os prazos e dependências reais cadastrados.")}<div class="aline-v24-kpis">${C.kpi("Vencidos",overdue,"prazo ultrapassado","overdue")}${C.kpi("Prazo a vencer",due,"hoje ou até 2 dias","urgent")}${C.kpi("Faltam 3 a 10 dias",near,"atenção programada","near")}${C.kpi("Aguardando",waiting,"terceiro precisa agir","waiting")}</div><div class="aline-v24-panel">${notificationsList(rows)}</div></div>`;
  }).catch(C.error);
};
window.alineWaitingV25=async function(type,id){
  try{
    const route=type==="process"?"processes":"works";
    const item=await C.api(`/${route}/${id}`);
    modal(`<h2>Dependência externa</h2><form id="a25WaitingForm" class="form-grid"><div class="form-group" style="grid-column:1/-1"><label>Aguardando ação de</label>${C.select("waiting_for",DEPENDENCIES,item.waiting_for||"","Nenhuma dependência")}</div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar</button></div></form>`);
    document.getElementById("a25WaitingForm").onsubmit=async event=>{
      event.preventDefault();
      const payload={...item,waiting_for:event.target.elements.waiting_for.value};
      ["documents","events","status","deadline_state","deadline_label","days_to_deadline","portal_url","ri_digital_url","registry_name","registry_portal_url","client_name","cpf_cnpj","client_city","progress_status","deadline_status"].forEach(key=>delete payload[key]);
      await C.api(`/${route}/${id}`,{method:"PUT",body:JSON.stringify({data:payload})});
      closeModal();showToast("Dependência atualizada.");type==="process"?alineProcessesV25():alineWorksV25();
    };
  }catch(error){alert(error.message)}
};
window.alineDeleteV25=async function(type,id,context="list"){
  try{
    const impact=await C.api(`/delete-impact/${type}/${encodeURIComponent(id)}`);
    modal(`<h2>Confirmar exclusão</h2><div class="aline-v25-delete-warning"><strong>${C.esc(impact.title)}</strong><p>${C.esc(impact.message)}</p><p>Esta ação é permanente e ficará registrada na auditoria.</p></div><form id="a25DeleteForm" class="form-grid"><div class="form-group" style="grid-column:1/-1"><label>Digite EXCLUIR para confirmar</label><input name="confirmation" autocomplete="off" required></div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-danger" type="submit" disabled>Excluir permanentemente</button></div></form>`);
    const form=document.getElementById("a25DeleteForm"),button=form.querySelector("button[type='submit']");
    form.elements.confirmation.oninput=()=>button.disabled=form.elements.confirmation.value.trim().toUpperCase()!=="EXCLUIR";
    form.onsubmit=async event=>{
      event.preventDefault();await C.api(`/delete/${type}/${encodeURIComponent(id)}?confirmed=true`,{method:"DELETE"});
      closeModal();showToast("Item excluído permanentemente.");
      if(context==="detail"){S.selectedClient=null;S.selectedProcess=null;S.selectedWork=null}
      if(type==="work")alineWorksV25();else if(type==="notification")alineNotificationsV25();else alineProcessesV25();
    };
  }catch(error){alert(error.message)}
};
window.alinePortalSettingsV25=async function(){
  try{
    const rows=await C.api("/portal-links");S.portals=rows;
    modal(`<h2>Configurações dos links</h2><p class="muted">Altere nome, cidade, tipo, endereço, ordem e visibilidade.</p><div class="toolbar"><button class="btn btn-primary" onclick="alinePortalFormV25()">Novo link</button></div><div class="aline-v25-portal-settings">${rows.map(item=>`<div><span><strong>${C.esc(item.name)}</strong><small>${C.esc(item.city||"Geral")} • ${C.esc(item.portal_type)} • ordem ${item.display_order}</small></span><div class="actions"><button class="btn btn-light" onclick="alinePortalFormV25(${item.id})">Editar</button><button class="btn btn-danger-outline" onclick="alineDeleteV25('portal_link',${item.id})">Excluir</button></div></div>`).join("")||C.empty("Nenhum link cadastrado.")}</div>`);
  }catch(error){alert(error.message)}
};
window.alinePortalFormV25=function(id){
  const item=(S.portals||[]).find(row=>Number(row.id)===Number(id));
  modal(`<h2>${item?"Editar link":"Novo link"}</h2><form id="a25PortalForm" class="form-grid"><div class="form-group"><label>Nome *</label><input name="name" value="${C.esc(item?.name||"")}" required></div><div class="form-group"><label>Cidade</label>${C.select("city",CITIES,item?.city||"","Geral")}</div><div class="form-group"><label>Tipo de portal</label>${C.select("portal_type",["Prefeitura","Cartório","RI Digital","Outro"],item?.portal_type||"Prefeitura")}</div><div class="form-group"><label>Ordem</label><input name="display_order" type="number" value="${Number(item?.display_order||0)}"></div><div class="form-group" style="grid-column:1/-1"><label>Endereço *</label><input name="url" type="url" value="${C.esc(item?.url||"")}" required></div><div class="form-group"><label>Visibilidade</label>${C.select("active",[{"id":"true","name":"Ativo"},{"id":"false","name":"Oculto"}],String(item?.active??true))}</div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar link</button></div></form>`);
  document.getElementById("a25PortalForm").onsubmit=async event=>{
    event.preventDefault();const data=Object.fromEntries(new FormData(event.target).entries());
    data.active=data.active==="true";data.display_order=Number(data.display_order||0);
    try{await C.api(id?`/portal-links/${id}`:"/portal-links",{method:id?"PUT":"POST",body:JSON.stringify({data})});closeModal();showToast("Link salvo.");alineProcessesV25()}catch(error){alert(error.message)}
  };
};

window.alineClientFormV24=async function(id){
  try{
    const rows=S.clients?.length?S.clients:await C.api("/clients");
    const item=id?rows.find(row=>Number(row.id)===Number(id)):null;
    modal(`<h2>${item?"Editar cliente":"Novo cliente"}</h2><form id="a25ClientForm" class="form-grid">
      <div class="form-group"><label>Tipo</label>${C.select("person_type",["PF","PJ"],item?.person_type||"PF")}</div>
      ${field("Nome do cliente","name",item?.name||"","text",true)}
      ${field("CPF/CNPJ","cpf_cnpj",item?.cpf_cnpj||"")}
      ${field("Razão social","legal_name",item?.legal_name||"")}
      ${field("Nome fantasia","trade_name",item?.trade_name||"")}
      ${field("Responsável / representante","responsible_name",item?.responsible_name||"")}
      ${field("WhatsApp / telefone","phone",item?.phone||"")}
      ${field("E-mail","email",item?.email||"","email")}
      <div class="form-group"><label>Cidade</label>${C.select("city",CITIES,item?.city||S.processCity||"","Selecione a cidade")}</div>
      ${field("UF","state",item?.state||"SC")}
      <div class="form-group" style="grid-column:1/-1"><label>Endereço</label><input name="address" value="${C.esc(item?.address||"")}"></div>
      <div class="form-group" style="grid-column:1/-1"><label>Observações</label><textarea name="notes">${C.esc(item?.notes||"")}</textarea></div>
      <div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar cliente</button></div>
    </form>`);
    document.getElementById("a25ClientForm").onsubmit=async event=>{
      event.preventDefault();const data=Object.fromEntries(new FormData(event.target).entries());
      try{await C.api(id?`/clients/${id}`:"/clients",{method:id?"PUT":"POST",body:JSON.stringify({data})});closeModal();showToast("Cliente salvo.");alineProcessesV25()}catch(error){alert(error.message)}
    };
  }catch(error){alert(error.message)}
};
window.alineProcessFormV24=async function(id,clientId){
  await legacyProcessForm(id,clientId);
  if(!id&&S.processCity){
    const form=document.getElementById("a24ProcessForm");
    if(form?.elements?.city)form.elements.city.value=S.processCity;
  }
};
window.alineRegistryFormV24=function(id){
  const item=(S.registries||[]).find(row=>Number(row.id)===Number(id));
  modal(`<h2>${item?"Editar cartório":"Novo cartório"}</h2><form id="a25RegistryForm" class="form-grid">
    ${field("Nome","name",item?.name||"","text",true)}
    <div class="form-group"><label>Cidade</label>${C.select("city",CITIES,item?.city||S.processCity||"","Selecione a cidade")}</div>
    ${field("Número / Ofício","office_number",item?.office_number||"")}
    ${field("Portal do cartório","portal_url",item?.portal_url||"","url")}
    ${field("RI Digital","ri_digital_url",item?.ri_digital_url||"https://ridigital.org.br/eProtocolo/listagem_contratos.aspx?comum=1","url")}
    <div class="form-group"><label>Situação</label>${C.select("active",[{"id":"true","name":"Ativo"},{"id":"false","name":"Inativo"}],String(item?.active??true))}</div>
    <div class="form-group" style="grid-column:1/-1"><label>Observações</label><textarea name="notes">${C.esc(item?.notes||"")}</textarea></div>
    <div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar</button></div>
  </form>`);
  document.getElementById("a25RegistryForm").onsubmit=async event=>{
    event.preventDefault();const data=Object.fromEntries(new FormData(event.target).entries());data.active=data.active==="true";
    try{await C.api(id?`/registries/${id}`:"/registries",{method:id?"PUT":"POST",body:JSON.stringify({data})});closeModal();showToast("Cartório salvo.");alineProcessesV25()}catch(error){alert(error.message)}
  };
};

function emailMessageRows(rows,processes){
  if(!rows.length)return C.empty("Nenhum e-mail nesta lista.");
  return `<div class="aline-v26-email-list">${rows.map(item=>`<article class="aline-v26-email-card">
    <div><strong>${C.esc(item.subject||"Mensagem sem assunto")}</strong><small>${C.esc(item.sender||"Remetente não informado")} • ${C.date(item.received_at)}</small><p>${C.esc(item.body_excerpt||item.error||"")}</p></div>
    ${item.status==="unmatched"?`<div class="aline-v26-email-assign"><select aria-label="Escolha o processo" id="a26EmailProcess${item.id}"><option value="">Selecione o processo</option>${processes.map(process=>`<option value="${process.id}">${C.esc(`${process.client_name} — ${process.title} — ${process.third_code||"sem terceiro código"}`)}</option>`).join("")}</select><button class="btn btn-primary" onclick="alineAssignEmailV26(${item.id})">Associar</button></div>`:`<div class="aline-v26-email-match"><span>Associado a</span><button class="btn btn-light" onclick="alineProcessDetailV24(${Number(item.process_id)})">${C.esc(item.client_name||"")} — ${C.esc(item.process_title||"Processo")}</button>${item.attachment_count?`<small>${item.attachment_count} anexo(s) importado(s)</small>`:""}</div>`}
  </article>`).join("")}</div>`;
}
window.alineEmailsV26=function(){
  C.loading("Processos — E-mails de processos","Leitura automática da caixa Terra pelo terceiro código.");
  Promise.all([C.api("/email/status"),C.api("/email/messages?limit=100"),C.api("/processes"),C.api("/vault/status")]).then(([status,messages,processes,vault])=>{
    S.emailStatus=status;S.emailMessages=messages;S.processes=processes;S.emailVault=vault;
    const unmatched=messages.filter(item=>item.status==="unmatched"&&item.third_code),matched=messages.filter(item=>item.status==="matched");
    const stateLabel=status.enabled?"Automação ativa":status.configured?"Pausada":"Ainda não configurada";
    document.getElementById("content").innerHTML=`<div class="aline-v24-shell">
      ${C.banner("E-mails de processos","O sistema procura o terceiro código no assunto e no texto dos novos e-mails do Terra.",`${vault.configured?"":`<button class="btn" onclick="alineEmailVaultSetupV26()">Criar cofre</button>`}<button class="btn" onclick="alineEmailSettingsV26()" ${vault.configured?"":"disabled"}>Configurar Terra</button>`)}
      <div class="aline-v24-kpis">${C.kpi("Situação",stateLabel,status.last_error?"Verifique o erro abaixo":"senha protegida no cofre")}${C.kpi("Falhas para revisar",unmatched.length,"somente e-mails relacionados",unmatched.length?"urgent":"")}${C.kpi("Associados",matched.length,"mensagens recentes")}${C.kpi("Última consulta",status.last_sync_at?C.date(status.last_sync_at):"Ainda não realizada",status.enabled?`a cada ${Math.round(status.interval_seconds/60)} min`:"automação pausada")}</div>
      ${status.last_error?`<div class="aline-v26-email-error"><strong>Não foi possível consultar o Terra.</strong><span>${C.esc(status.last_error)}</span></div>`:""}
      <div class="toolbar"><div><h3>Mensagens relacionadas</h3><p class="muted">O Terra permanece intacto. O Sistema Portfólio copia somente mensagens com terceiro código reconhecido.</p></div><button class="btn btn-primary" onclick="alineSyncEmailsV26()" ${status.enabled?"":"disabled"}>Consultar agora</button></div>
      ${unmatched.length?`<div class="aline-v24-panel"><h3>Falhas para revisar (${unmatched.length})</h3>${emailMessageRows(unmatched,processes)}</div>`:""}
      <div class="aline-v24-panel" style="margin-top:16px"><h3>Associados recentemente</h3>${emailMessageRows(matched,processes)}</div>
    </div>`;
  }).catch(C.error);
};
window.alineEmailVaultSetupV26=function(){
  modal(`<h2>Criar cofre da Processos</h2><p class="muted">Crie um PIN com pelo menos 6 caracteres para proteger a senha do e-mail.</p><form id="a26EmailVault" class="form-grid"><div class="form-group" style="grid-column:1/-1"><label>Novo PIN</label><input name="pin" type="password" minlength="6" required autocomplete="new-password"></div><div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Criar cofre</button></div></form>`);
  document.getElementById("a26EmailVault").onsubmit=async event=>{event.preventDefault();try{await C.api("/vault/setup",{method:"POST",body:JSON.stringify({pin:event.target.elements.pin.value})});closeModal();showToast("Cofre criado. Agora configure o Terra.");alineEmailsV26()}catch(error){alert(error.message)}};
};
window.alineEmailSettingsV26=function(){
  const item=S.emailStatus||{};
  modal(`<h2>Automação do e-mail Terra</h2><p class="muted">A senha fica criptografada. Para trocar usuário ou senha, informe também o PIN do cofre da Processos.</p><form id="a26EmailSettings" class="form-grid">
    ${field("E-mail Terra","username",item.username||"","email")}${field("Senha do e-mail","password","","password")}
    ${field("PIN do cofre","vault_pin","","password")}
    <div class="form-group"><label>Consulta automática</label>${C.select("enabled",[{id:"true",name:"Ativa"},{id:"false",name:"Pausada"}],String(Boolean(item.enabled)))}</div>
    <div class="form-group"><label>Intervalo (minutos)</label><input name="interval_minutes" type="number" min="1" max="60" value="${Math.round((item.interval_seconds||300)/60)}"></div>
    <div class="form-group" style="grid-column:1/-1"><label>Remetentes permitidos (opcional)</label><textarea name="allowed_senders" placeholder="Um e-mail ou domínio por linha. Vazio aceita todos.">${C.esc(item.allowed_senders||"")}</textarea></div>
    <div class="config-actions" style="grid-column:1/-1"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar</button></div>
  </form>`);
  document.getElementById("a26EmailSettings").onsubmit=async event=>{
    event.preventDefault();const values=Object.fromEntries(new FormData(event.target).entries());
    const payload={username:values.username,password:values.password,enabled:values.enabled==="true",interval_seconds:Number(values.interval_minutes||5)*60,allowed_senders:values.allowed_senders,mark_read:false};
    try{await C.api("/email/settings",{method:"PUT",headers:{"X-Processos-Vault-Pin":values.vault_pin},body:JSON.stringify(payload)});closeModal();showToast("Automação de e-mail atualizada.");alineEmailsV26()}catch(error){alert(error.message)}
  };
};
window.alineSyncEmailsV26=async function(){
  try{const result=await C.api("/email/sync",{method:"POST"});showToast(`${result.processed||0} e-mail(s) consultado(s).`);alineEmailsV26()}catch(error){alert(error.message)}
};
window.alineAssignEmailV26=async function(messageId){
  const processId=Number(document.getElementById(`a26EmailProcess${messageId}`)?.value||0);
  if(!processId){alert("Escolha o processo antes de associar.");return}
  try{await C.api(`/email/messages/${messageId}/assign`,{method:"POST",body:JSON.stringify({process_id:processId})});showToast("E-mail associado ao processo.");alineEmailsV26()}catch(error){alert(error.message)}
};

})();
