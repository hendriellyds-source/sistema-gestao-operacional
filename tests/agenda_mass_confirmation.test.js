const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync('app/templates/index.html', 'utf8');
const start = html.indexOf('function confirmAppointmentsForm(preselectedId=null){');
const end = html.indexOf('function deleteAppointment(id){', start);
assert(start >= 0 && end > start, 'Função de confirmação em massa não encontrada.');
let submit, saves = 0;
const data = {
  works: [{id: 3, name: 'Obra teste'}],
  workScheduleEntries: [
    {id: 10, appointmentGroupId: 10, teamId: 1, workId: 3, type: 'Obra', date: '2026-10-19', status: 'Planejado', value: 0, financialPending: true},
    {id: 11, appointmentGroupId: 10, appointmentExtraTeam: true, teamId: 2, workId: 3, type: 'Obra', date: '2026-10-19', status: 'Planejado', value: 100},
    {id: 12, teamId: 3, workId: 3, type: 'Obra', date: '2026-10-20', status: 'Planejado', value: 500},
  ],
};
const context = {
  data, scheduleWeekStart: '2026-10-19',
  addIsoDays: () => '2026-10-25',
  isScheduleDuplicateEntry: () => false,
  fmt: value => value, esc: value => String(value), teamName: id => `Equipe ${id}`,
  employeesOnVacation: () => [],
  modal: () => {},
  document: {getElementById: () => ({addEventListener: (_event, fn) => {submit = fn;}})},
  save: () => {saves++;}, closeModal: () => {}, render: () => {}, showToast: () => {}, alert: message => {throw Error(message)},
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
context.confirmAppointmentsForm();
function card(id, selected, value, billing) {
  return {
    dataset: {confirmGroup: String(id)},
    querySelector: selector => ({
      '.confirm-select': {checked: selected},
      '.confirm-value': {value: String(value)},
      '.confirm-billing': {checked: billing},
    })[selector],
  };
}
submit({preventDefault() {}, target: {querySelectorAll: () => [card(10, true, 1250, true), card(12, false, 500, true)]}});
assert.strictEqual(saves, 1);
assert.strictEqual(data.workScheduleEntries[0].value, 1250);
assert.strictEqual(data.workScheduleEntries[0].financialPending, false);
assert.strictEqual(data.workScheduleEntries[0].status, 'Confirmado');
assert.strictEqual(data.workScheduleEntries[1].status, 'Confirmado', 'Equipe adicional acompanha a confirmação.');
assert.strictEqual(data.workScheduleEntries[2].status, 'Planejado', 'Item não selecionado permanece intacto.');
assert.strictEqual(data.workScheduleEntries[0].generateFinancial, true);
const financeStart = html.indexOf('function confirmedWorkEntries(month=financeMonth){');
const financeEnd = html.indexOf('function localDateFromIso(value){', financeStart);
assert(financeStart >= 0 && financeEnd > financeStart);
const finance = {
  data,
  financeMonth: '2026-10',
  monthStart: () => '2026-10-01',
  monthEnd: () => '2026-10-31',
  isScheduleDuplicateEntry: () => false,
};
vm.createContext(finance);
vm.runInContext(html.slice(financeStart, financeEnd), finance);
assert.strictEqual(finance.confirmedWorkEntries().length, 2, 'Serviço e adicional entram no Financeiro.');
data.workScheduleEntries[0].generateFinancial = false;
data.workScheduleEntries[1].generateFinancial = false;
assert.strictEqual(finance.confirmedWorkEntries().length, 0, 'Opção sem faturamento exclui o grupo.');
console.log('Agenda mass confirmation tests: OK');
