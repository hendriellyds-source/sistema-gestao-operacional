const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("app/templates/index.html", "utf8");
const start = html.indexOf("function completedWorkEntriesForMonth(month){");
const end = html.indexOf("function vacationBlocksSchedule", start);
assert(start >= 0 && end > start, "As funções reais do relatório mensal não foram encontradas na interface.");
const reportFunctions = html.slice(start, end);

const context = {
  data: {
    works: [
      {id: 1, name: "Obra válida", registrationPending: false},
      {id: 2, name: "Obra pendente", registrationPending: true},
    ],
    workTeams: [
      {id: 10, name: "Equipe A"},
      {id: 20, name: "Equipe B"},
    ],
    workScheduleEntries: [
      {id: 1, type: "Obra", status: "Planejado", date: "2026-08-03", teamId: 10, companyId: 1, workId: 1, value: 1000},
      {id: 2, type: "Obra", status: "Confirmado", date: "2026-08-03", teamId: 10, companyId: 1, workId: 1, value: 500},
      {id: 3, type: "Levantamento", status: "Planejado", date: "2026-08-04", teamId: 20, value: 800},
      {id: 4, type: "Obra", status: "Cancelado", date: "2026-08-03", teamId: 10, companyId: 1, workId: 1, value: 9999},
      {id: 5, type: "Obra", status: "Confirmado", date: "2026-08-03", teamId: 10, companyId: 1, workId: 1, value: 1000, duplicate: true},
      {id: 6, type: "Obra", status: "Confirmado", date: "2026-08-03", teamId: 20, companyId: 1, workId: 2, value: 1000},
      {id: 7, type: "Obra", status: "Confirmado", date: "2026-09-01", teamId: 10, companyId: 1, workId: 1, value: 1000},
    ],
  },
  monthStart: month => `${month}-01`,
  monthEnd: month => `${month}-31`,
  isScheduleDuplicateEntry: entry => Boolean(entry.duplicate),
};
context.teamName = id => context.data.workTeams.find(team => team.id === Number(id))?.name || "Equipe não localizada";
vm.createContext(context);
vm.runInContext(reportFunctions, context);

const report = context.monthlyReportData("2026-08", 2000, 900);
assert.strictEqual(report.entries.length, 3, "O relatório deve usar os mesmos agendamentos válidos da agenda.");
assert.strictEqual(report.total, 2300);
assert.strictEqual(
  JSON.stringify(Array.from(report.ranking, row => [row.team, row.total])),
  JSON.stringify([["Equipe A", 1500], ["Equipe B", 800]])
);
assert.strictEqual(report.days.length, 2);
assert.strictEqual(report.targetDays, 0);
assert.strictEqual(report.lowTeamDays.length, 1);

console.log("Frontend operational tests: OK");
