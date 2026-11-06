/* Historical pages use the existing schedule state and central synchronization. */
const historicalPages = new Map();
function historicalRegisteredValue(row) {
  const work=data.works.find(item=>Number(item.id)===Number(row.workId) && Number(item.companyId)===Number(row.companyId));
  const full=Number(work?.dailyRate)||0, half=Number(work?.halfDailyRate)||0;
  const value=row.dailyType==="Meia"?(half || full/2):row.dailyType==="Inteira"?full:0;
  return Number.isFinite(value)&&value>0?Math.round(value*100)/100:0;
}
function historicalRefreshLinkedPrices() {
  const refresh=row=>{
    if(!(row.historicalPriceLinked===true || (row.historical && !Number(row.value))) || (row.id && invoiceForScheduleEntry(row.id)))return;
    row.historicalPriceLinked=true;
    const value=historicalRegisteredValue(row);
    if(Number(row.value)!==value){row.value=value;row.reviewed=false;row.historicalPending=true;}
    if(!value)row.historicalPending=true;
  };
  data.workScheduleEntries.forEach(refresh);
  historicalPages.forEach(page=>page.rows.forEach(refresh));
}
async function historicalCreateRegistration(button) {
  const tr=button.closest("[data-row]"),page=historicalPages.get(historicalDate),row=page.rows[Number(tr.dataset.row)];
  const message=document.getElementById("historicalMessage");
  if(row.id && invoiceForScheduleEntry(row.id)){message.textContent="Lançamento vinculado a cobrança; use a rotina financeira para ajustes.";return;}
  const companyButton=button.hasAttribute("data-create-company");
  const name=tr.querySelector(companyButton?"[data-new-company]":"[data-new-work]").value.trim().replace(/\s+/g," ");
  if(!name){message.textContent="Informe o nome para cadastrar.";return;}
  if(companyButton){
    const matches=data.companies.filter(item=>normalizedText(companyName(item.id))===normalizedText(name));
    if(matches.length>1){message.textContent="Há empresas com o mesmo nome. Selecione pelo número do cadastro.";return;}
    const company=matches[0] || {id:nextId(data.companies),name,tradeName:name,legalName:name,notes:""};
    if(!matches.length)data.companies.push(company);
    if(Number(row.companyId)!==Number(company.id)){row.workId="";row.value=0;}
    row.companyId=company.id;row.historicalCompanyText=name;
  }else{
    if(!data.companies.some(item=>Number(item.id)===Number(row.companyId))){message.textContent="Selecione ou cadastre a empresa antes de cadastrar a obra.";return;}
    const rate=Number(tr.querySelector("[data-new-rate]").value),half=Number(tr.querySelector("[data-new-half]").value);
    if(!Number.isFinite(rate)||rate<0||!Number.isFinite(half)||half<0){message.textContent="Informe valores válidos ou deixe em branco.";return;}
    const matches=data.works.filter(item=>Number(item.companyId)===Number(row.companyId)&&normalizedText(item.name)===normalizedText(name));
    if(matches.length>1){message.textContent="Há obras com o mesmo nome. Selecione pelo número do cadastro.";return;}
    const work=matches[0] || {id:nextId(data.works),companyId:Number(row.companyId),name,dailyRate:rate,halfDailyRate:half,location:"",notes:""};
    if(!matches.length)data.works.push(work);
    row.workId=work.id;row.historicalWorkText=name;row.historicalPriceLinked=true;row.value=historicalRegisteredValue(row);
  }
  row.reviewed=false;page.dirty=true;save();historicalRenderRows();
  message.textContent="Cadastro selecionado. Sincronizando… As linhas da agenda continuam como rascunho até Salvar dia.";
  try{await syncSystemState();message.textContent=systemStateDirty?"Cadastro com sincronização pendente. Salve o dia antes de sair.":"Cadastro salvo e selecionado. Agora revise a linha e use Salvar dia.";}
  catch{message.textContent="Cadastro com sincronização pendente. Salve o dia antes de sair.";}
}
function historicalRowsFromPhoto(result) {
  return result.rows.map(line=>{
    const row=historicalNewRow({historicalTeamText:line.team,historicalCompanyText:line.company,historicalWorkText:line.work,
      dailyType:line.dailyType,originalText:line.originalText,observation:[line.observation,line.uncertain?"Leitura incerta: conferir foto":""].filter(Boolean).join(" • ")});
    row.teamId=historicalChoice(data.workTeams,line.team,x=>x.name);
    row.companyId=historicalChoice(data.companies,line.company,x=>companyName(x.id));
    row.workId=historicalChoice(data.works.filter(work=>Number(work.companyId)===Number(row.companyId)),line.work,x=>x.name);
    row.value=historicalRegisteredValue(row);
    return row;
  });
}
async function historicalPhotoData(file) {
  const bitmap=await createImageBitmap(file);
  try{
    const scale=Math.min(1,2560/Math.max(bitmap.width,bitmap.height));
    const canvas=document.createElement("canvas");canvas.width=Math.round(bitmap.width*scale);canvas.height=Math.round(bitmap.height*scale);
    const context=canvas.getContext("2d");context.fillStyle="#fff";context.fillRect(0,0,canvas.width,canvas.height);
    context.drawImage(bitmap,0,0,canvas.width,canvas.height);return canvas.toDataURL("image/jpeg",0.92);
  }finally{bitmap.close();}
}
async function historicalReadPhoto() {
  const page=historicalPages.get(historicalDate),date=historicalDate,file=page.photoFile;
  if(!file||page.reading||page.photoRead)return;
  page.reading=true;historicalRenderPhoto();
  try{
    const image=await historicalPhotoData(file);
    const response=await fetch("/api/schedules/historical/photo-preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({image})});
    const result=await response.json();
    if(!response.ok)throw new Error(typeof result.detail==="string"?result.detail:"Não foi possível ler a foto. Tente novamente.");
    if(page.photoFile!==file)return;
    const rows=historicalRowsFromPhoto(result);
    if(!rows.length)throw new Error("Não foram encontrados serviços. Tente uma foto mais nítida ou preencha manualmente.");
    page.rows=page.rows.filter(historicalHasContent).concat(rows);page.dirty=true;page.photoRead=true;page.photoNotes=result.pageNotes;
  }catch(error){page.photoError=error.message;}
  finally{
    page.reading=false;
    if(date===historicalDate && document.getElementById("historicalRows")){
      historicalRenderRows();historicalRenderPhoto();
      if(page.photoError){document.getElementById("historicalPhotoStatus").textContent=page.photoError;page.photoError="";}
    }
  }
}
let historicalDate = "";
let historicalSaving = false;

function historicalNewRow(source = {}) {
  return {teamId:"", companyId:"", workId:"", dailyType:"Inteira", value:0,
    status:"Confirmado", observation:"", originalText:"", reviewed:false,
    generateFinancial:false, historicalPriceLinked:true, historicalTeamText:"", historicalCompanyText:"",
    historicalWorkText:"", ...source, historicalRowKey:crypto.randomUUID()};
}
function historicalHasContent(row) {
  return Boolean(row.id || row.teamId || row.companyId || row.workId || row.observation.trim() ||
    row.originalText.trim() || row.historicalTeamText || row.historicalCompanyText || row.historicalWorkText);
}
function historicalIssues(row, date) {
  const issues = [];
  const work = data.works.find(item => Number(item.id) === Number(row.workId));
  if (!data.workTeams.some(item => Number(item.id) === Number(row.teamId))) issues.push("Equipe a revisar");
  if (!data.companies.some(item => Number(item.id) === Number(row.companyId))) issues.push("Empresa a revisar");
  if (!work || Number(work.companyId) !== Number(row.companyId)) issues.push("Obra a revisar");
  if (work?.registrationPending) issues.push("Cadastro da obra pendente");
  if (!["Inteira", "Meia"].includes(row.dailyType)) issues.push("Tipo a revisar");
  if (!(Number(row.value) > 0) || !Number.isFinite(Number(row.value))) issues.push("Valor cadastrado a revisar");
  if (!row.reviewed) issues.push("Não revisado");
  if (row.teamId && teamUnavailableOnDate(Number(row.teamId), date)) issues.push("Equipe indisponível na data");
  return issues;
}
function historicalPayload(row, date) {
  const work = data.works.find(item => Number(item.id) === Number(row.workId));
  return {...row, date, type:"Obra", historical:true,
    historicalPending:historicalIssues(row,date).length > 0,
    teamId:Number(row.teamId)||"", companyId:Number(row.companyId)||"", workId:Number(row.workId)||"",
    value:Number(row.value)||0, billingEntityId:row.billingEntityId || work?.billingEntityId || "",
    client:"", location:"", serviceType:""};
}
function historicalDraftKey(row) {
  return JSON.stringify([row.date,row.teamId,row.companyId,row.workId,row.dailyType,row.value,
    row.historicalTeamText,row.historicalCompanyText,row.historicalWorkText,row.originalText,row.observation]
    .map(value=>normalizedText(String(value??""))));
}
function historicalPrepare(rows, date) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || isoFromLocalDate(localDateFromIso(date)) !== date)
    throw new Error("Informe uma data válida para a página.");
  const payloads = rows.filter(historicalHasContent).map(row => historicalPayload(row,date));
  const keys = new Set();
  payloads.forEach((row,index) => {
    if (!["Confirmado","Planejado","Cancelado"].includes(row.status)) throw new Error(`Linha ${index+1}: status inválido.`);
    const existing = data.workScheduleEntries.find(item => item.id === row.id);
    if (existing && invoiceForScheduleEntry(existing.id)) {
      const changed = Object.keys(row).some(key => JSON.stringify(row[key]) !== JSON.stringify(existing[key]));
      if (changed) throw new Error(`Linha ${index+1}: lançamento vinculado a cobrança. Ajuste pela rotina financeira existente.`);
    }
    const issues = historicalIssues(row,date);
    if (row.generateFinancial && issues.length)
      throw new Error(`Linha ${index+1}: revise os dados antes de gerar financeiro (${issues.join(", ")}).`);
    // Missing identifiers are drafts, not a shared "unknown work" duplicate key.
    if (row.teamId && row.companyId && row.workId) {
      const key = scheduleWorkKey(row);
      const duplicate = findWorkScheduleDuplicate(row,row.id);
      if (keys.has(key) || duplicate)
        throw new Error(`Linha ${index+1}: serviço duplicado${duplicate ? ` do lançamento #${duplicate.id}` : " nesta página"}. Remova ou corrija a linha.`);
      keys.add(key);
    } else {
      const key = historicalDraftKey(row);
      const duplicate = data.workScheduleEntries.find(item => item.historical && item.id !== row.id && historicalDraftKey(item) === key);
      if (keys.has(key) || duplicate) throw new Error(`Linha ${index+1}: rascunho duplicado. Remova ou corrija a linha.`);
      keys.add(key);
    }
  });
  return payloads;
}
function historicalPage(date) {
  historicalRefreshLinkedPrices();
  if (!historicalPages.has(date)) historicalPages.set(date,{rows:[],dirty:false,photo:""});
  const page = historicalPages.get(date);
  if (!page.dirty) {
    page.rows = data.workScheduleEntries.filter(row => row.historical && row.date === date).map(row => ({...row}));
    if (!page.rows.length) page.rows = [historicalNewRow()];
  }
  return page;
}
function historicalOptions(items, label) {
  return items.map(item => `<option value="${esc(`${label(item)} [#${item.id}]`)}"></option>`).join("");
}
function historicalChoice(items, text, label) {
  const number = String(text).trim().match(/^(?:\[#(\d+)\]|#?(\d+))$/);
  if (number) {
    const id = Number(number[1] || number[2]);
    return items.find(item => Number(item.id) === id)?.id || "";
  }
  const matches = items.filter(item => normalizedText(`${label(item)} [#${item.id}]`) === normalizedText(text) || normalizedText(label(item)) === normalizedText(text));
  return matches.length === 1 ? matches[0].id : "";
}
function historicalDisplay(row, field, items, label) {
  const item = items.find(item => Number(item.id) === Number(row[field]));
  return item ? `${label(item)} [#${item.id}]` : row[{teamId:"historicalTeamText",companyId:"historicalCompanyText",workId:"historicalWorkText"}[field]] || "";
}
function historicalScheduleForm(date = historicalDate || scheduleWeekStart || plusDays(0)) {
  historicalDate = date;
  historicalPage(date);
  modal(`<div id="historicalSchedule">
    <h2>Importar agenda antiga</h2>
    <p class="muted">Uma página por dia. Leia a foto e corrija as linhas. Sem valor: laranja, precisa de atenção e revisão. Outras pendências: amarelo. Pendências ficam fora dos totais.</p>
    <div class="actions"><label>Data da página <input id="historicalDate" type="date" value="${esc(date)}"></label>
    <button type="button" class="btn btn-light" id="historicalAdd">+ Adicionar linha</button>
    <button type="button" class="btn btn-light" id="historicalAddMany">+ 5 linhas</button>
    <label><input type="checkbox" id="historicalReviewedAll"> Revisado em massa</label>
    <label><input type="checkbox" id="historicalFinancialAll"> Gerar lançamento financeiro para todas as linhas?</label></div>
    <p class="muted">Digite o nome ou o número do cadastro (#). Tab ou Enter confirma a seleção e avança. Enter no último campo adiciona outra linha; Shift+Enter volta. O financeiro só recebe linhas completas, revisadas e com status Feito.</p>
    <div class="historical-layout"><div class="historical-grid"><table><thead><tr>
    ${["Equipe","Empresa","Obra/Cliente","Tipo","Valor automático","Status","Observação","Texto original","Revisado","Gerar lançamento financeiro?","Ações"].map(x=>`<th scope="col">${x}</th>`).join("")}
    </tr></thead><tbody id="historicalRows"></tbody></table></div>
    <aside id="historicalPhotoArea"><strong>Usar foto como referência</strong>
    <p>Arraste ou selecione uma foto e clique em Ler foto. A imagem será enviada para leitura por IA. Corrija as linhas antes de Salvar dia.</p>
    <input id="historicalPhotoInput" aria-label="Foto da página" type="file" accept="image/jpeg,image/png,image/webp">
    <button type="button" class="btn btn-light" id="historicalPhotoRemove">Remover foto</button>
    <button type="button" class="btn btn-primary" id="historicalPhotoRead">Ler foto e preencher rascunho</button><p id="historicalPhotoStatus" role="status"></p><p id="historicalPhotoNotes"></p>
    <img id="historicalPhoto" alt="Foto de referência da página da agenda">
    </aside></div>
    <p id="historicalMessage" role="status" aria-live="polite"></p>
    <div class="actions historical-footer"><button class="btn btn-light" id="historicalPrevious">Dia anterior</button>
    <button class="btn btn-primary" id="historicalSave">Salvar dia</button>
    <button class="btn btn-primary" id="historicalNext">Salvar e ir para próximo dia</button>
    <button class="btn btn-light" onclick="closeModal();render()">Fechar</button></div>
    <small>Ao trocar de dia ou fechar, linhas ainda não salvas ficam disponíveis nesta sessão. Salve antes de recarregar ou sair do sistema.</small>
    </div>`);
  document.getElementById("historicalDate").onchange = event => {
    if (!event.target.value) {event.target.value=historicalDate; return;}
    historicalScheduleForm(event.target.value);
  };
  document.getElementById("historicalAdd").onclick = () => historicalAddRows(1);
  document.getElementById("historicalAddMany").onclick = () => historicalAddRows(5);
  document.getElementById("historicalFinancialAll").onchange = event => {
    const page=historicalPages.get(historicalDate);
    page.rows.forEach(row=>row.generateFinancial=event.target.checked); page.dirty=true; historicalRenderRows();
  };
  document.getElementById("historicalReviewedAll").onchange = event => {
    const page=historicalPages.get(historicalDate);
    page.rows.filter(historicalHasContent).forEach(row=>{if(!row.id || !invoiceForScheduleEntry(row.id))row.reviewed=event.target.checked;});
    page.dirty=true; historicalRenderRows();
  };
  document.getElementById("historicalPhotoRead").onclick=historicalReadPhoto;
  document.getElementById("historicalPrevious").onclick = () => historicalScheduleForm(addIsoDays(historicalDate,-1));
  document.getElementById("historicalSave").onclick = () => historicalSaveDay(false);
  document.getElementById("historicalNext").onclick = () => historicalSaveDay(true);
  document.getElementById("historicalPhotoInput").onchange = event => historicalSetPhoto(event.target.files[0]);
  document.getElementById("historicalPhotoRemove").onclick = () => historicalSetPhoto(null);
  const photoArea=document.getElementById("historicalPhotoArea");
  photoArea.ondragover=event=>event.preventDefault();
  photoArea.ondrop=event=>{event.preventDefault(); historicalSetPhoto(event.dataTransfer.files[0]);};
  historicalRenderRows(); historicalRenderPhoto();
}
function historicalRenderRows() {
  const page=historicalPages.get(historicalDate);
  const teams=data.workTeams, companies=data.companies;
  document.getElementById("historicalRows").innerHTML=page.rows.map((row,index)=>{
    const works=data.works.filter(work=>Number(work.companyId)===Number(row.companyId));
    const issues=historicalIssues(row,historicalDate);
    const input=(field,value,attrs="")=>`<input data-field="${field}" aria-label="${field} linha ${index+1}" value="${esc(value)}" ${attrs}>`;
    return `<tr data-row="${index}" class="${issues.length?"historical-pending":""} ${!(Number(row.value)>0)?"historical-attention":""}">
      <td>${input("teamId",historicalDisplay(row,"teamId",teams,x=>x.name),`list="historicalTeams"`)}<small data-issues>${esc(issues.join(" • "))}</small></td>
      <td>${input("companyId",historicalDisplay(row,"companyId",companies,x=>companyName(x.id)),`list="historicalCompanies"`)}<details><summary tabindex="-1">+ Nova empresa</summary><input data-new-company aria-label="Nova empresa linha ${index+1}" placeholder="Nome da nova empresa"><button type="button" data-create-company="${index}" class="btn btn-light">Cadastrar empresa</button></details></td>
      <td>${input("workId",historicalDisplay(row,"workId",works,x=>x.name),`list="historicalWorks${index}" placeholder="${row.companyId?"Selecione ou transcreva":"Empresa libera sugestões"}"`)}<datalist id="historicalWorks${index}">${historicalOptions(works,x=>x.name)}</datalist><details><summary tabindex="-1">+ Nova obra</summary><input data-new-work aria-label="Nova obra linha ${index+1}" placeholder="Nome da nova obra"><label>Diária (opcional)<input data-new-rate aria-label="Diária nova obra linha ${index+1}" type="number" min="0" step="0.01" placeholder="Sem valor"></label><label>Meia (opcional)<input data-new-half aria-label="Meia nova obra linha ${index+1}" type="number" min="0" step="0.01" placeholder="Metade da diária"></label><button type="button" data-create-work="${index}" class="btn btn-light">Cadastrar obra</button></details></td>
      <td><select data-field="dailyType" aria-label="Tipo linha ${index+1}">${[["","A revisar"],["Inteira","Diária"],["Meia","Meia"]].map(([v,t])=>`<option value="${v}" ${row.dailyType===v?"selected":""}>${t}</option>`).join("")}</select></td>
      <td><input data-value aria-label="Valor automático linha ${index+1}" value="${esc(money(row.value))}" readonly tabindex="-1"></td>
      <td><select data-field="status" aria-label="Status linha ${index+1}">${[["Confirmado","Feito"],["Planejado","Planejado"],["Cancelado","Cancelado"]].map(([v,t])=>`<option value="${v}" ${row.status===v?"selected":""}>${t}</option>`).join("")}</select></td>
      <td>${input("observation",row.observation)}</td><td>${input("originalText",row.originalText)}</td>
      <td><input data-field="reviewed" aria-label="Revisado linha ${index+1}" type="checkbox" ${row.reviewed?"checked":""}></td>
      <td><input data-field="generateFinancial" aria-label="Gerar financeiro linha ${index+1}" type="checkbox" ${row.generateFinancial?"checked":""}></td>
      <td><button type="button" data-duplicate="${index}" class="btn btn-light">Duplicar</button><button type="button" data-remove="${index}" class="btn btn-light">Remover</button></td></tr>`;
  }).join("")+`<tr hidden><td><datalist id="historicalTeams">${historicalOptions(teams,x=>x.name)}</datalist><datalist id="historicalCompanies">${historicalOptions(companies,x=>companyName(x.id))}</datalist></td></tr>`;
  const body=document.getElementById("historicalRows");
  body.oninput=historicalEdit;
  body.onchange=historicalEdit;
  body.onclick=event=>{
    const create=event.target.closest("[data-create-company],[data-create-work]");
    if(create){historicalCreateRegistration(create);return;}
    const duplicate=event.target.closest("[data-duplicate]");
    const remove=event.target.closest("[data-remove]");
    if(duplicate){
      const index=Number(duplicate.dataset.duplicate);
      const {id,historicalRowKey,...source}=page.rows[index];
      page.rows.splice(index+1,0,historicalNewRow({...source,reviewed:false,generateFinancial:false}));
      page.dirty=true; historicalRenderRows(); historicalFocus(index+1);
    }
    if(remove){
      const index=Number(remove.dataset.remove);
      if(page.rows[index].id){alert("Para apagar um lançamento já salvo, use Apagar lançamentos na agenda, que verifica as cobranças vinculadas.");return;}
      page.rows.splice(index,1);page.dirty=true;historicalRenderRows();
    }
  };
  body.onkeydown=event=>{
    if(event.isComposing)return;
    if(event.key==="Enter" || event.key==="Tab")historicalCommitChoice(event.target);
    if(event.key!=="Enter" || event.isComposing || event.target.tagName==="BUTTON")return;
    event.preventDefault();
    const fields=[...body.querySelectorAll("[data-field]:not(:disabled)")];
    const position=fields.indexOf(event.target);
    if(position<0)return;
    const next=position+(event.shiftKey?-1:1);
    if(next>=fields.length)historicalAddRows(1);else fields[next]?.focus();
  };
  historicalUpdateAllCheckbox();
}
function historicalKeyboardChoice(items,text,label){
  const exact=historicalChoice(items,text,label);
  const query=normalizedText(String(text).trim());
  if(exact || !query || /^(?:\[#\d+\]|#?\d+)$/.test(query))return exact;
  return items.find(item=>normalizedText(label(item)).includes(query))?.id || "";
}
function historicalCommitChoice(input){
  const field=input.dataset.field;
  if(!["teamId","companyId","workId"].includes(field))return;
  const row=historicalPages.get(historicalDate).rows[Number(input.closest("[data-row]").dataset.row)];
  const items=field==="teamId"?data.workTeams:field==="companyId"?data.companies:data.works.filter(work=>Number(work.companyId)===Number(row.companyId));
  const label=field==="companyId"?x=>companyName(x.id):x=>x.name;
  const id=historicalKeyboardChoice(items,input.value,label);
  if(!id)return;
  input.value=`${label(items.find(item=>Number(item.id)===Number(id)))} [#${id}]`;
  if(Number(row[field])!==Number(id))historicalEdit({target:input});
  input.value=historicalDisplay(row,field,items,label);
}
function historicalUpdateAllCheckbox(){
  const rows=historicalPages.get(historicalDate).rows;
  const checkbox=document.getElementById("historicalFinancialAll");
  checkbox.checked=rows.length>0&&rows.every(row=>row.generateFinancial);
  checkbox.indeterminate=rows.some(row=>row.generateFinancial)&&!checkbox.checked;
  const reviewed=document.getElementById("historicalReviewedAll"),filled=rows.filter(historicalHasContent);
  reviewed.checked=filled.length>0&&filled.every(row=>row.reviewed);
  reviewed.indeterminate=filled.some(row=>row.reviewed)&&!reviewed.checked;
}
function historicalEdit(event){
  const field=event.target.dataset.field;
  if(!field)return;
  const tr=event.target.closest("[data-row]"),index=Number(tr.dataset.row);
  const page=historicalPages.get(historicalDate),row=page.rows[index];
  if(["teamId","companyId","workId"].includes(field)){
    const items=field==="teamId"?data.workTeams:field==="companyId"?data.companies:data.works.filter(work=>Number(work.companyId)===Number(row.companyId));
    const label=field==="companyId"?x=>companyName(x.id):x=>x.name;
    const previous=row[field];
    row[field]=historicalChoice(items,event.target.value,label);
    row[{teamId:"historicalTeamText",companyId:"historicalCompanyText",workId:"historicalWorkText"}[field]]=event.target.value;
    if(field==="companyId" && previous!==row[field]){
      row.workId="";if(previous)row.historicalWorkText="";row.value=0;
      const works=data.works.filter(work=>Number(work.companyId)===Number(row.companyId));
      const workInput=tr.querySelector('[data-field="workId"]');workInput.value=row.historicalWorkText;
      workInput.placeholder=row.companyId?"Selecione ou transcreva":"Empresa libera sugestões";
      tr.querySelector("datalist").innerHTML=historicalOptions(works,x=>x.name);
    }
  }else row[field]=event.target.type==="checkbox"?event.target.checked:event.target.value;
  if(["workId","dailyType"].includes(field)){row.historicalPriceLinked=true;row.value=historicalRegisteredValue(row);}
  if(!["reviewed","generateFinancial"].includes(field))row.reviewed=false;
  tr.querySelector('[data-field="reviewed"]').checked=row.reviewed;
  tr.querySelector("[data-value]").value=money(row.value);
  const issues=historicalIssues(row,historicalDate);
  tr.classList.toggle("historical-pending",issues.length>0);
  tr.classList.toggle("historical-attention",!(Number(row.value)>0));
  tr.querySelector("[data-issues]").textContent=issues.join(" • ");
  page.dirty=true;historicalUpdateAllCheckbox();
}
function historicalFocus(index){document.querySelector(`#historicalRows [data-row="${index}"] [data-field]`)?.focus();}
function historicalAddRows(count){
  const page=historicalPages.get(historicalDate),index=page.rows.length;
  for(let i=0;i<count;i++)page.rows.push(historicalNewRow());
  page.dirty=true;historicalRenderRows();historicalFocus(index);
}
function historicalSetPhoto(file){
  if(file && (!["image/jpeg","image/png","image/webp"].includes(file.type)||file.size>20*1024*1024)){
    alert("Selecione uma foto JPG, PNG ou WebP de até 20 MB.");return;
  }
  const page=historicalPages.get(historicalDate);
  if(page.photo)URL.revokeObjectURL(page.photo);
  page.photo=file?URL.createObjectURL(file):"";page.photoFile=file;page.photoRead=false;page.photoNotes="";historicalRenderPhoto();
}
function historicalRenderPhoto(){
  const image=document.getElementById("historicalPhoto"),url=historicalPages.get(historicalDate).photo;
  if(url)image.src=url;else image.removeAttribute("src");image.hidden=!url;
  const page=historicalPages.get(historicalDate);
  document.getElementById("historicalPhotoRead").disabled=!page.photoFile||page.reading||page.photoRead;
  document.getElementById("historicalPhotoStatus").textContent=page.reading?"Lendo a foto…":page.photoRead?"Foto lida. Revise as linhas antes de salvar.":"";
  document.getElementById("historicalPhotoNotes").textContent=page.photoNotes||"";
}
async function historicalSaveDay(next){
  if(historicalSaving)return;
  const page=historicalPages.get(historicalDate),date=historicalDate;
  const message=document.getElementById("historicalMessage");
  let payloads;
  try{payloads=historicalPrepare(page.rows,date);}catch(error){message.textContent=error.message;return;}
  if(!payloads.length){message.textContent="Preencha ao menos uma linha antes de salvar.";return;}
  historicalSaving=true;
  const controls=[...document.querySelectorAll("#historicalSchedule button,#historicalSchedule input,#historicalSchedule select")];
  const disabled=controls.map(control=>control.disabled);controls.forEach(control=>control.disabled=true);
  try{
    payloads.forEach(payload=>{
      const existing=data.workScheduleEntries.find(item=>item.historicalRowKey===payload.historicalRowKey);
      if(existing)Object.assign(existing,payload);else{
        payload.id=nextId(data.workScheduleEntries);data.workScheduleEntries.push(payload);
      }
      const row=page.rows.find(item=>item.historicalRowKey===payload.historicalRowKey);row.id=existing?.id||payload.id;
    });
    save();
    message.textContent="Salvando no servidor…";
    // Keep the day and row identities on failures, allowing the existing retry without duplicates.
    await syncSystemState();
    if(systemStateDirty)throw new Error("Dados mantidos nesta sessão; sincronização com o servidor pendente. Aguarde e clique em Salvar dia novamente antes de avançar.");
    page.dirty=false;scheduleWeekStart=mondayOf(date);
    message.textContent=`${payloads.length} lançamento(s) salvo(s). ${payloads.filter(row=>row.historicalPending).length} a revisar.`;
    if(next)historicalScheduleForm(addIsoDays(date,1));
  }catch(error){message.textContent=error.message;}
  finally{historicalSaving=false;controls.forEach((control,index)=>control.disabled=disabled[index]);}
}
window.addEventListener("beforeunload",event=>{
  if([...historicalPages.values()].some(page=>page.dirty&&page.rows.some(historicalHasContent))){event.preventDefault();event.returnValue="";}
});
