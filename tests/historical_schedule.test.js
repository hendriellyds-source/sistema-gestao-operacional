const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const crypto=require('node:crypto');
const html=fs.readFileSync('app/templates/index.html','utf8');
function extract(name){
  const start=html.indexOf(`function ${name}(`);
  assert(start>=0,name);
  const match=/\n(?:async )?function \w+\(/g;match.lastIndex=start+1;
  const end=match.exec(html)?.index;
  return html.slice(start,end);
}
const names=['normalizedText','scheduleWorkKey','scheduleDuplicateOf','isScheduleDuplicateEntry',
  'findWorkScheduleDuplicate','inferredFullDailyRate','inferredHalfDailyRate','invoiceForScheduleEntry',
  'confirmedWorkEntries','completedWorkEntriesForMonth','monthlyReportData','monthStart','monthEnd',
  'localDateFromIso','isoFromLocalDate','addIsoDays','companyName','teamName','nextId','esc'];
const helpers=names.map(extract).join('\n');
const feature=fs.readFileSync('assets/historical_schedule.js','utf8');
function fixture(){return {workTeams:[{id:1,name:'Emanuel'},{id:2,name:'Neri',active:false}],
  companies:[{id:1,name:'MDM'},{id:2,name:'RTK'}],
  works:[{id:1,companyId:1,name:'Brilhante',dailyRate:1500,halfDailyRate:900},
    {id:2,companyId:2,name:'São Bernardo',dailyRate:1200,halfDailyRate:600}],
  workScheduleEntries:[],invoices:[]};}
function context(){
  const c={data:fixture(),crypto,window:{addEventListener(){}},teamUnavailableOnDate:()=>false,
    plusDays:()=> '2026-09-09',console};
  vm.createContext(c);vm.runInContext(helpers+'\n'+feature,c);return c;
}
if(require.main===module){
  const c=context(),date='2026-08-04';
  const make=()=>c.historicalNewRow({teamId:1,companyId:1,workId:1,value:1500,reviewed:true});
  let row=make();
  assert.equal(row.status,'Confirmado');assert.equal(row.generateFinancial,false);
  assert.equal(c.inferredHalfDailyRate(1),900);
  assert.equal(c.inferredFullDailyRate(1),1500);
  c.data.works[0].nextHalfDayFullCharge=true;
  assert.equal(c.inferredHalfDailyRate(1),900,'Historical imports must not consume the next-service billing rule');
  assert.equal(c.data.works[0].nextHalfDayFullCharge,true);
  const second=c.historicalNewRow({teamId:2,companyId:2,workId:2,value:600,dailyType:'Meia',reviewed:true});
  const draft=c.historicalNewRow({originalText:'Rock Pinheiro / equipe a conferir'});
  const otherDraft=c.historicalNewRow({originalText:'GIA? primeiro plano'});
  const batch=c.historicalPrepare([row,second,draft,otherDraft,c.historicalNewRow()],date);
  assert.equal(batch.length,4);
  batch.forEach((entry,i)=>entry.id=i+1);
  c.data.workScheduleEntries=batch;
  assert.equal(c.confirmedWorkEntries('2026-08').length,0);
  assert.equal(c.completedWorkEntriesForMonth('2026-08').length,2);
  assert.equal(c.monthlyReportData('2026-08',2000,900).total,2100);
  batch[0].generateFinancial=true;
  assert.equal(c.confirmedWorkEntries('2026-08').length,1);
  batch[0].historicalPending=true;
  assert.equal(c.confirmedWorkEntries('2026-08').length,0);
  batch[0].historicalPending=false;
  batch[0].status='Cancelado';assert.equal(c.confirmedWorkEntries('2026-08').length,0);
  batch[0].status='Confirmado';
  assert.throws(()=>c.historicalPrepare([make()],date),/duplicado/);
  assert.equal(c.historicalPrepare([batch[0]],date).length,1,'Own saved row is editable');
  c.data.workScheduleEntries=[];
  assert.throws(()=>c.historicalPrepare([make(),make()],date),/duplicado/);
  assert.throws(()=>c.historicalPrepare([draft,{...draft,historicalRowKey:'another'}],date),/rascunho duplicado/);
  assert.throws(()=>c.historicalPrepare([make()],'2026-02-30'),/data válida/);
  assert.throws(()=>c.historicalPrepare([{...draft,generateFinancial:true}],date),/revise/);
  assert.equal(c.historicalPayload({...row,companyId:2},date).historicalPending,true);
  c.teamUnavailableOnDate=()=>true;
  assert.equal(c.historicalPayload(row,date).historicalPending,true);
  assert.throws(()=>c.historicalPrepare([{...row,generateFinancial:true}],date),/indisponível/);
  c.teamUnavailableOnDate=()=>false;
  assert.equal(c.historicalChoice(c.data.workTeams,'Neri',x=>x.name),2,'Inactive historical teams remain selectable');
  for(const value of ['2','#2','[#2]',' 2 '])assert.equal(c.historicalChoice(c.data.workTeams,value,x=>x.name),2);
  assert.equal(c.historicalChoice(c.data.workTeams,'99',x=>x.name),'','Unknown IDs must stay unresolved');
  assert.equal(c.historicalChoice(c.data.works.filter(x=>x.companyId===1),'2',x=>x.name),'','Work IDs must respect the selected company');
  assert.equal(c.historicalChoice([{id:1,name:'A'},{id:2,name:'A'}],'A',x=>x.name),'');
  c.data.workScheduleEntries=[{...c.historicalPayload(row,date),id:1}];
  c.data.invoices=[{scheduleEntryIds:[1]}];
  assert.throws(()=>c.historicalPrepare([{...c.data.workScheduleEntries[0],value:99}],date),/cobrança/);
  // Legacy entries retain the existing financial behavior.
  c.data.workScheduleEntries=[{id:8,type:'Obra',teamId:1,companyId:1,workId:1,date,value:500,status:'Confirmado'}];
  assert.equal(c.confirmedWorkEntries('2026-08').length,1);
  const linked=context();
  const imported=linked.historicalRowsFromPhoto({rows:[{team:'Emanuel',company:'MDM',work:'Brilhante',dailyType:'',originalText:'MDM Brilhante?',observation:'Conferir',uncertain:true}]})[0];
  assert.equal(imported.workId,1);assert.equal(imported.dailyType,'');assert.equal(imported.value,0);
  assert.equal(imported.reviewed,false);assert.equal(imported.generateFinancial,false);
  assert.equal(linked.data.workScheduleEntries.length,0,'Photo transcription never persists schedules');
  const priceRow={...linked.historicalNewRow({teamId:1,companyId:1,workId:1}),historical:true,id:1};
  linked.data.workScheduleEntries=[priceRow];
  linked.historicalRefreshLinkedPrices();assert.equal(priceRow.value,1500);assert.equal(priceRow.reviewed,false);
  priceRow.reviewed=true;linked.data.works[0].dailyRate=1700;
  linked.historicalRefreshLinkedPrices();assert.equal(priceRow.value,1700);assert.equal(priceRow.reviewed,false);
  linked.data.invoices=[{scheduleEntryIds:[1]}];linked.data.works[0].dailyRate=1900;
  linked.historicalRefreshLinkedPrices();assert.equal(priceRow.value,1700,'Invoices keep their agreed value');
  console.log('Historical schedule: defaults, 04/08/2026, drafts, rates, reports, finance opt-in, duplicate checks, photo drafts, linked prices and invoices OK');
}
module.exports={helpers,feature,fixture};
