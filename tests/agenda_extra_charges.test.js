const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("app/templates/index.html", "utf8");
const start = html.indexOf("function inferredFullDailyRate(workId){");
const end = html.indexOf("function scheduleWorkKey(entry){", start);
assert(start >= 0 && end > start, "As funções de cálculo de adicionais da Agenda não foram encontradas.");

const context = {
  data: {
    works: [{id: 1, dailyRate: 0}],
    workScheduleEntries: [
      {id: 1, type: "Obra", workId: 1, value: 1200},
      {id: 2, type: "Obra", workId: 1, value: 300, appointmentExtraTeam: true},
    ],
  },
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);

assert.strictEqual(context.parseAgendaCharge("1/4", 1), 300);
assert.strictEqual(context.parseAgendaCharge("1,5/4", 1), 450);
assert.strictEqual(context.parseAgendaCharge("275,50", 1), 275.5);
assert.strictEqual(context.inferredFullDailyRate(1), 1200);
assert(Number.isNaN(context.parseAgendaCharge("1/4", 2)));

console.log("Agenda extra charge tests: OK");
