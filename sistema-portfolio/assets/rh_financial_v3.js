(function(){
"use strict";

const STATUS_LABELS={
  valid:"Válido",expiring:"Vencendo",expired:"Vencido",no_expiration:"Sem validade",
  pending:"Pendente",scheduled:"Agendado",paid:"Pago",overdue:"Vencido",cancelled:"Cancelado"
};
const CATEGORY_LABELS={
  vehicle:"Veículo",employee:"Funcionário",tax:"Imposto",supplier:"Fornecedor",
  payroll:"Folha",document:"Documento",maintenance:"Manutenção",other:"Outro"
};
let certificateCache=[];
let calendarEvents=[];
let financialCalendarMonth=localIsoDate().slice(0,7);
let calendarCategoryFilter="";
let calendarStatusFilter="";

function e(value){
  return String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"})[char]);
}
function d(value){
  if(!value)return "—";
  const parts=String(value).slice(0,10).split("-");
  return parts.length===3?`${parts[2]}/${parts[1]}/${parts[0]}`:String(value);
}
function localIsoDate(value=new Date()){
  return `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,"0")}-${String(value.getDate()).padStart(2,"0")}`;
}
function statusBadge(status){
  const normalized=String(status||"pending").toLowerCase();
  return `<span class="rf-status ${e(normalized)}">${e(STATUS_LABELS[normalized]||status||"Pendente")}</span>`;
}
function moneyValue(value){
  if(value===null||value===undefined||value==="")return "—";
  return Number(value).toLocaleString("pt-BR",{style:"currency",currency:"BRL"});
}
async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{"Content-Type":"application/json",...(options.headers||{})}});
  if(response.status===401){location.href="/login";throw new Error("Sessão expirada.")}
  const result=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(result.detail||"Não foi possível concluir a operação.");
  return result;
}
async function filePayload(file){
  if(!file)throw new Error("Selecione um arquivo PDF, JPG ou PNG.");
  if(file.size>10*1024*1024)throw new Error("O arquivo deve ter no máximo 10 MB.");
  const allowed=["application/pdf","image/jpeg","image/png"];
  if(file.type&&!allowed.includes(file.type))throw new Error("Formato não permitido. Use PDF, JPG ou PNG.");
  return{filename:file.name,mime_type:file.type||null,content_base64:await readFileAsBase64(file)};
}
function certificateData(form){
  const fd=new FormData(form),noExpiration=fd.get("no_expiration")==="on";
  return{
    course_name:String(fd.get("course_name")||"").trim(),
    issuing_institution:String(fd.get("issuing_institution")||"").trim()||null,
    issue_date:fd.get("issue_date")||null,
    expiration_date:noExpiration?null:(fd.get("expiration_date")||null),
    no_expiration:noExpiration,
    certificate_number:String(fd.get("certificate_number")||"").trim()||null,
    notes:String(fd.get("notes")||"").trim()||null
  };
}
function certificateFormHtml(item=null,withFile=false){
  return `<form id="rfCertificateForm" class="form-grid">
    <div class="form-group"><label>Nome do curso *</label><input name="course_name" maxlength="240" required value="${e(item?.course_name||"")}"></div>
    <div class="form-group"><label>Instituição emissora</label><input name="issuing_institution" maxlength="240" value="${e(item?.issuing_institution||"")}"></div>
    <div class="form-group"><label>Data de emissão/realização</label><input name="issue_date" type="date" value="${e(item?.issue_date||"")}"></div>
    <div class="form-group"><label>Data de validade</label><input id="rfCertificateExpiry" name="expiration_date" type="date" value="${e(item?.expiration_date||"")}" ${item?.no_expiration?"disabled":""}></div>
    <label class="rf-checkbox"><input id="rfCertificateNoExpiry" name="no_expiration" type="checkbox" ${item?.no_expiration?"checked":""}> Sem validade</label>
    <div class="form-group"><label>Número do certificado</label><input name="certificate_number" maxlength="160" value="${e(item?.certificate_number||"")}"></div>
    <div class="form-group" style="grid-column:1/-1"><label>Observações</label><textarea name="notes" maxlength="3000">${e(item?.notes||"")}</textarea></div>
    ${withFile?`<div class="form-group" style="grid-column:1/-1"><label>Arquivos privados * (PDF, JPG ou PNG)</label>${documentUploadField({inputId:"rfCertificateFiles",name:"files",accept:"application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png",required:true,help:"Arraste um ou vários certificados ou clique para selecionar."})}<small>Máximo de 10 MB por arquivo. Os dados do certificado serão aplicados a todos os arquivos selecionados.</small></div>`:""}
    <div class="rf-modal-actions"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Salvar certificado</button></div>
  </form>`;
}
function bindNoExpiration(){
  const checkbox=document.getElementById("rfCertificateNoExpiry"),expiry=document.getElementById("rfCertificateExpiry");
  checkbox?.addEventListener("change",()=>{expiry.disabled=checkbox.checked;if(checkbox.checked)expiry.value=""});
}

window.renderEmployeeCertificates=async function(employee){
  const target=document.getElementById("employeeTab");
  if(!target)return;
  target.innerHTML='<div class="card"><span class="fleet-spinner"></span> Carregando certificados...</div>';
  try{
    certificateCache=await api(`/api/employee-certificates?employee_id=${Number(employee.id)}`);
    target.innerHTML=`<div class="rf-certificate-toolbar">
      <div class="rf-filter-grid"><div class="form-group"><label>Status</label><select id="rfCertificateStatus"><option value="">Todos</option><option value="valid">Válido</option><option value="expiring">Vencendo</option><option value="expired">Vencido</option><option value="no_expiration">Sem validade</option></select></div><div class="form-group"><label>Buscar</label><input id="rfCertificateSearch" placeholder="Curso ou instituição"></div></div>
      <button class="btn btn-primary" onclick="newEmployeeCertificate(${Number(employee.id)})">+ Adicionar certificado</button>
    </div><div id="rfCertificateSummary"></div><div id="rfCertificateList"></div>`;
    document.getElementById("rfCertificateStatus").onchange=filterEmployeeCertificates;
    document.getElementById("rfCertificateSearch").oninput=filterEmployeeCertificates;
    filterEmployeeCertificates();
  }catch(error){target.innerHTML=`<div class="card"><span class="status bad">Erro</span><p>${e(error.message)}</p><small>Se este for o primeiro uso, aplique a migração SQL e crie o bucket privado no Supabase.</small></div>`}
};

window.filterEmployeeCertificates=function(){
  const status=document.getElementById("rfCertificateStatus")?.value||"";
  const search=String(document.getElementById("rfCertificateSearch")?.value||"").toLowerCase();
  const rows=certificateCache.filter(item=>(!status||item.status===status)&&(!search||`${item.course_name} ${item.issuing_institution||""}`.toLowerCase().includes(search)));
  const counts={valid:0,expiring:0,expired:0,no_expiration:0};certificateCache.forEach(item=>{if(counts[item.status]!==undefined)counts[item.status]++});
  document.getElementById("rfCertificateSummary").innerHTML=`<div class="grid grid-4" style="margin-bottom:14px">${metric("Válidos",counts.valid,"certificados")}${metric("Vencendo",counts.expiring,"até 30 dias","warning")}${metric("Vencidos",counts.expired,"exigem atenção","danger")}${metric("Sem validade",counts.no_expiration,"prazo indeterminado")}</div>`;
  document.getElementById("rfCertificateList").innerHTML=rows.length?`<div class="rf-certificate-grid">${rows.map(item=>`<article class="rf-certificate-card ${e(item.status)}"><div class="toolbar" style="margin:0"><div><h3>${e(item.course_name)}</h3>${statusBadge(item.status)}</div></div><div class="rf-certificate-meta"><span><b>Instituição:</b> ${e(item.issuing_institution||"Não informada")}</span><span><b>Emissão:</b> ${d(item.issue_date)}</span><span><b>Validade:</b> ${item.no_expiration?"Sem validade":d(item.expiration_date)}</span><span><b>Número:</b> ${e(item.certificate_number||"Não informado")}</span><span><b>Arquivo:</b> ${e(item.original_filename)} · ${formatStoredFileSize(item.file_size)}</span></div>${item.notes?`<p>${e(item.notes)}</p>`:""}<div class="actions"><button class="btn btn-light" onclick="downloadEmployeeCertificate('${e(item.id)}')">Visualizar / baixar</button><button class="btn btn-light" onclick="editEmployeeCertificate('${e(item.id)}')">Editar</button><button class="btn btn-light" onclick="replaceEmployeeCertificateFile('${e(item.id)}')">Substituir arquivo</button><button class="btn btn-danger" onclick="deleteEmployeeCertificate('${e(item.id)}')">Excluir</button></div></article>`).join("")}</div>`:'<div class="card">Nenhum certificado encontrado.</div>';
};

window.newEmployeeCertificate=function(employeeId){
  modal(`<h2>Novo certificado</h2>${certificateFormHtml(null,true)}`);bindNoExpiration();
  document.getElementById("rfCertificateForm").onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{const files=uniqueDocumentFiles(event.target.elements.files.files);for(const file of files)await api("/api/employee-certificates",{method:"POST",body:JSON.stringify({employee_id:Number(employeeId),data:certificateData(event.target),file:await filePayload(file)})});closeModal();showToast(`${files.length} certificado(s) cadastrado(s) com segurança.`);renderEmployeeCertificates(window.currentEmployeeData.emp)}catch(error){alert(error.message);button.disabled=false}};
};
window.editEmployeeCertificate=function(id){
  const item=certificateCache.find(row=>String(row.id)===String(id));if(!item)return;
  modal(`<h2>Editar certificado</h2>${certificateFormHtml(item,false)}`);bindNoExpiration();
  document.getElementById("rfCertificateForm").onsubmit=async event=>{event.preventDefault();try{await api(`/api/employee-certificates/${encodeURIComponent(id)}`,{method:"PUT",body:JSON.stringify({data:certificateData(event.target)})});closeModal();showToast("Certificado atualizado.");renderEmployeeCertificates(window.currentEmployeeData.emp)}catch(error){alert(error.message)}};
};
window.replaceEmployeeCertificateFile=function(id){
  modal(`<h2>Substituir arquivo</h2><form id="rfReplaceCertificateFile" class="form-grid"><div class="form-group" style="grid-column:1/-1"><label>Novo PDF, JPG ou PNG *</label><input name="file" type="file" accept="application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png" required></div><div class="rf-modal-actions"><button type="button" class="btn btn-light" onclick="closeModal()">Cancelar</button><button class="btn btn-primary">Substituir</button></div></form>`);
  document.getElementById("rfReplaceCertificateFile").onsubmit=async event=>{event.preventDefault();try{await api(`/api/employee-certificates/${encodeURIComponent(id)}/file`,{method:"PUT",body:JSON.stringify(await filePayload(event.target.elements.file.files[0]))});closeModal();showToast("Arquivo substituído.");renderEmployeeCertificates(window.currentEmployeeData.emp)}catch(error){alert(error.message)}};
};
window.downloadEmployeeCertificate=async function(id){try{const result=await api(`/api/employee-certificates/${encodeURIComponent(id)}/download`);window.open(result.url,"_blank","noopener,noreferrer")}catch(error){alert(error.message)}};
window.deleteEmployeeCertificate=async function(id){const item=certificateCache.find(row=>String(row.id)===String(id));if(!item||!confirm(`Excluir o certificado "${item.course_name}" e seu arquivo privado?`))return;try{await api(`/api/employee-certificates/${encodeURIComponent(id)}`,{method:"DELETE"});showToast("Certificado excluído.");renderEmployeeCertificates(window.currentEmployeeData.emp)}catch(error){alert(error.message)}};

function monthRange(month){const [year,number]=month.split("-").map(Number);return{from:`${month}-01`,to:localIsoDate(new Date(year,number,0)),year,number:number-1}}
function calendarFiltered(){return calendarEvents.filter(item=>(!calendarCategoryFilter||item.category===calendarCategoryFilter)&&(!calendarStatusFilter||item.status===calendarStatusFilter))}
function calendarHtml(events,range){
  const first=new Date(range.year,range.number,1),last=new Date(range.year,range.number+1,0),days=[];
  for(let i=0;i<first.getDay();i++)days.push(null);for(let day=1;day<=last.getDate();day++)days.push(new Date(range.year,range.number,day));
  const today=localIsoDate();
  return `<div class="rf-calendar-grid">${["Dom","Seg","Ter","Qua","Qui","Sex","Sáb"].map(label=>`<div class="rf-calendar-head">${label}</div>`).join("")}${days.map(day=>{if(!day)return'<div class="rf-calendar-day is-empty"></div>';const iso=`${range.year}-${String(range.number+1).padStart(2,"0")}-${String(day.getDate()).padStart(2,"0")}`;return `<div class="rf-calendar-day ${iso===today?"is-today":""}"><span class="rf-calendar-day-number">${day.getDate()}</span>${events.filter(item=>item.date===iso).map(item=>`<button class="rf-event ${e(item.category)}" data-rf-event="${e(item.id)}" title="${e(item.title)}">${e(item.title)}</button>`).join("")}</div>`}).join("")}</div><div class="rf-calendar-list">${events.map(item=>`<button class="rf-calendar-list-item" data-rf-event="${e(item.id)}"><b>${d(item.date)}</b><span><strong>${e(item.title)}</strong><br><small>${e(CATEGORY_LABELS[item.category]||item.category)}${item.amount!==null&&item.amount!==undefined?` · ${moneyValue(item.amount)}`:""}</small></span>${statusBadge(item.status)}</button>`).join("")||'<div class="card">Nenhum evento no período.</div>'}</div>`;
}
function bindCalendarEvents(){document.querySelectorAll("[data-rf-event]").forEach(button=>button.onclick=()=>openFinancialCalendarEvent(button.dataset.rfEvent))}

window.financialCalendar=async function(){
  setHeader("Calendário financeiro/RH","Vencimentos automáticos e compromissos manuais em uma visão mensal unificada.");
  const content=document.getElementById("content"),range=monthRange(financialCalendarMonth);
  content.innerHTML='<div class="card"><span class="fleet-spinner"></span> Agregando eventos das fontes...</div>';
  try{
    const result=await api(`/api/financial-calendar?date_from=${range.from}&date_to=${range.to}`);calendarEvents=result.events||[];
    content.innerHTML=`<div class="rf-calendar-shell"><div class="rf-calendar-toolbar"><div class="actions"><button class="btn btn-light" onclick="changeFinancialCalendarMonth(-1)">←</button><input type="month" value="${e(financialCalendarMonth)}" onchange="financialCalendarMonth=this.value;financialCalendar()"><button class="btn btn-light" onclick="changeFinancialCalendarMonth(1)">→</button></div><button class="btn btn-primary" onclick="manualFinancialEntryForm()">+ Adicionar compromisso</button></div>
      <div class="rf-filter-grid"><div class="form-group"><label>Categoria</label><select onchange="calendarCategoryFilter=this.value;renderFinancialCalendarEvents()"><option value="">Todas</option>${Object.entries(CATEGORY_LABELS).map(([value,label])=>`<option value="${value}" ${calendarCategoryFilter===value?"selected":""}>${label}</option>`).join("")}</select></div><div class="form-group"><label>Status</label><select onchange="calendarStatusFilter=this.value;renderFinancialCalendarEvents()"><option value="">Todos</option>${["pending","scheduled","paid","overdue","cancelled"].map(value=>`<option value="${value}" ${calendarStatusFilter===value?"selected":""}>${STATUS_LABELS[value]}</option>`).join("")}</select></div></div>
      ${(result.warnings||[]).map(message=>`<div class="rf-calendar-warning">${e(message)}</div>`).join("")}
      <div class="rf-category-legend">${Object.entries(CATEGORY_LABELS).map(([,label])=>`<span>${label}</span>`).join("")}</div><div id="rfCalendarBody"></div></div>`;
    renderFinancialCalendarEvents();
  }catch(error){content.innerHTML=`<div class="card"><span class="status bad">Erro</span><p>${e(error.message)}</p></div>`}
};
window.renderFinancialCalendarEvents=function(){const body=document.getElementById("rfCalendarBody");if(!body)return;body.innerHTML=calendarHtml(calendarFiltered(),monthRange(financialCalendarMonth));bindCalendarEvents()};
window.changeFinancialCalendarMonth=function(delta){const range=monthRange(financialCalendarMonth),next=new Date(range.year,range.number+delta,1);financialCalendarMonth=`${next.getFullYear()}-${String(next.getMonth()+1).padStart(2,"0")}`;financialCalendar()};

window.openFinancialCalendarEvent=function(eventId){
  const item=calendarEvents.find(row=>String(row.id)===String(eventId));if(!item)return;
  if(!item.automatic){manualFinancialEntryForm(item.source_id);return}
  if(["employee_exam","employee_certificate"].includes(item.source_type)){currentArea="admin";page="funcionarios";selectedEmployee=Number(item.employee_id)||selectedEmployee;window.RF_INITIAL_EMPLOYEE_TAB=item.source_type==="employee_certificate"?"certificados":"exames";render();return}
  if(["vehicle_debt","vehicle_document","stored_vehicle_document","vehicle_fine","maintenance"].includes(item.source_type)){currentArea="finance";page="veiculos";if(item.vehicle_id)selectedFleetVehicleIdentifier=String(item.vehicle_id);selectedFleetPage=item.source_type.includes("document")?"documents":item.source_type==="maintenance"?"costs":"debts";render();return}
  if(item.source_type==="financial_invoice"){currentArea="finance";page="financeInvoices";render();return}
  if(item.url)location.href=item.url;
};

function manualEntryData(form){const fd=new FormData(form);return{title:String(fd.get("title")||"").trim(),category:fd.get("category"),due_date:fd.get("due_date"),amount:fd.get("amount")?Number(fd.get("amount")):null,supplier:String(fd.get("supplier")||"").trim()||null,payment_method:String(fd.get("payment_method")||"").trim()||null,notes:String(fd.get("notes")||"").trim()||null,status:fd.get("status"),employee_id:fd.get("employee_id")?Number(fd.get("employee_id")):null,vehicle_id:String(fd.get("vehicle_id")||"").trim()||null}}
function manualFormHtml(item){return `<form id="rfManualEntryForm" class="form-grid"><div class="form-group"><label>Título *</label><input name="title" required maxlength="240" value="${e(item?.title||"")}"></div><div class="form-group"><label>Categoria</label><select name="category">${Object.entries(CATEGORY_LABELS).map(([value,label])=>`<option value="${value}" ${item?.category===value?"selected":""}>${label}</option>`).join("")}</select></div><div class="form-group"><label>Data de vencimento *</label><input name="due_date" type="date" required value="${e(item?.date||localIsoDate())}"></div><div class="form-group"><label>Valor</label><input name="amount" type="number" min="0" step="0.01" value="${e(item?.amount??"")}"></div><div class="form-group"><label>Fornecedor / favorecido</label><input name="supplier" maxlength="240" value="${e(item?.supplier||"")}"></div><div class="form-group"><label>Forma de pagamento</label><input name="payment_method" maxlength="120" value="${e(item?.payment_method||"")}"></div><div class="form-group"><label>Status</label><select name="status">${["pending","scheduled","paid","overdue","cancelled"].map(value=>`<option value="${value}" ${item?.status===value?"selected":""}>${STATUS_LABELS[value]}</option>`).join("")}</select></div><div class="form-group"><label>Funcionário (ID opcional)</label><input name="employee_id" type="number" min="1" value="${e(item?.employee_id||"")}"></div><div class="form-group"><label>Veículo (placa/ID opcional)</label><input name="vehicle_id" maxlength="100" value="${e(item?.vehicle_id||"")}"></div><div class="form-group" style="grid-column:1/-1"><label>Observações</label><textarea name="notes" maxlength="4000">${e(item?.notes||"")}</textarea></div>${item?"":`<div class="form-group"><label>Recorrência</label><select id="rfRecurrenceFrequency" name="recurrence_frequency"><option value="">Não repetir</option><option value="weekly">Semanal</option><option value="monthly">Mensal</option><option value="yearly">Anual</option></select></div><div class="form-group"><label>Quantidade (máx. 36)</label><input name="recurrence_count" type="number" min="2" max="36" value="2"></div><div class="form-group"><label>Intervalo</label><input name="recurrence_interval" type="number" min="1" max="12" value="1"></div><div class="form-group" style="grid-column:1/-1"><label>Anexo privado (opcional)</label><input name="attachment" type="file" accept="application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png"><small>Em uma série recorrente, o anexo fica na primeira ocorrência.</small></div>`}<div class="rf-modal-actions">${item?.has_attachment?`<button type="button" class="btn btn-light" onclick="downloadManualEntryAttachment('${e(item.source_id)}')">Baixar anexo</button>`:""}${item?`<button type="button" class="btn btn-light" onclick="setManualEntryStatus('${e(item.source_id)}','paid')">Marcar como pago</button><button type="button" class="btn btn-light" onclick="setManualEntryStatus('${e(item.source_id)}','cancelled')">Cancelar compromisso</button><button type="button" class="btn btn-danger" onclick="deleteManualFinancialEntry('${e(item.source_id)}')">Excluir</button>`:""}<button type="button" class="btn btn-light" onclick="closeModal()">Fechar</button><button class="btn btn-primary">Salvar</button></div></form>`}

window.manualFinancialEntryForm=function(id=null){const item=id?calendarEvents.find(row=>!row.automatic&&String(row.source_id)===String(id)):null;modal(`<h2>${item?"Editar compromisso":"Adicionar compromisso"}</h2>${manualFormHtml(item)}`);document.getElementById("rfManualEntryForm").onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{const data=manualEntryData(event.target);if(item){await api(`/api/financial-calendar/entries/${encodeURIComponent(id)}`,{method:"PUT",body:JSON.stringify({data})})}else{const fd=new FormData(event.target),frequency=fd.get("recurrence_frequency"),file=event.target.elements.attachment.files[0];const recurrence=frequency?{frequency,interval:Number(fd.get("recurrence_interval")||1),count:Number(fd.get("recurrence_count")||2)}:null;await api("/api/financial-calendar/entries",{method:"POST",body:JSON.stringify({data,recurrence,attachment:file?await filePayload(file):null})})}closeModal();showToast(item?"Compromisso atualizado.":"Compromisso adicionado.");financialCalendar()}catch(error){alert(error.message);button.disabled=false}}};
window.setManualEntryStatus=async function(id,status){try{await api(`/api/financial-calendar/entries/${encodeURIComponent(id)}/status`,{method:"POST",body:JSON.stringify({status})});closeModal();showToast(status==="paid"?"Compromisso marcado como pago.":"Compromisso cancelado.");financialCalendar()}catch(error){alert(error.message)}};
window.deleteManualFinancialEntry=async function(id){if(!confirm("Excluir definitivamente este compromisso manual?"))return;try{await api(`/api/financial-calendar/entries/${encodeURIComponent(id)}`,{method:"DELETE"});closeModal();showToast("Compromisso excluído.");financialCalendar()}catch(error){alert(error.message)}};
window.downloadManualEntryAttachment=async function(id){try{const result=await api(`/api/financial-calendar/entries/${encodeURIComponent(id)}/attachment`);window.open(result.url,"_blank","noopener,noreferrer")}catch(error){alert(error.message)}};
})();
