const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("assets/tablet_agenda.js", "utf8");
const start = source.indexOf("function fold(value) {");
const end = source.indexOf("function renderTextRows()", start);
assert(start >= 0 && end > start, "As funções de interpretação da Agenda Tablet não foram encontradas.");

const context = {
  catalog: {
    teams: [{id: 1, name: "Neri"}, {id: 4, name: "Daniel"}],
    companies: [{id: 2, name: "MDM"}],
    works: [{id: 3, companyId: 2, name: "Talantaia"}],
    aliases: [],
  },
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

const review = {text_original: "Neri/Daniel/MDM/Talantaia/1/4", extra_teams: []};
context.resolveText(review);
assert.strictEqual(review.team_id, 1);
assert.strictEqual(review.company_id, 2);
assert.strictEqual(review.work_id, 3);
assert.strictEqual(review.daily_type, "Quarto");
assert.strictEqual(review.unregistered_work_text, "");
assert.deepStrictEqual(JSON.parse(JSON.stringify(review.extra_teams)), [{team_id: 4, amount: ""}]);

const fixedPrice = {text_original: "Neri/Talantaia/2000", extra_teams: []};
context.resolveText(fixedPrice);
assert.strictEqual(fixedPrice.work_id, 3);
assert.strictEqual(fixedPrice.unregistered_work_text, "");
assert.strictEqual(fixedPrice.manual_price_mode, "total");
assert.strictEqual(fixedPrice.manual_price_amount, 2000);

const surcharge = {text_original: "Neri/Talantaia/+400", extra_teams: []};
context.resolveText(surcharge);
assert.strictEqual(surcharge.work_id, 3);
assert.strictEqual(surcharge.manual_price_mode, "surcharge");
assert.strictEqual(surcharge.manual_price_amount, 400);

const newWork = {text_original: "Neri/Rua Nova/2000", extra_teams: []};
context.resolveText(newWork);
assert.strictEqual(newWork.unregistered_work_text, "Rua Nova");
assert.strictEqual(newWork.manual_price_amount, 2000);

const spacedPrice = {text_original: "Neri/MDM/Rua Nova rtk 2600", extra_teams: []};
context.resolveText(spacedPrice);
assert.strictEqual(spacedPrice.unregistered_work_text, "Rua Nova rtk");
assert.strictEqual(spacedPrice.manual_price_mode, "total");
assert.strictEqual(spacedPrice.manual_price_amount, 2600);

const halfSurcharge = {text_original: "Neri/Talantaia/meia +400", extra_teams: []};
context.resolveText(halfSurcharge);
assert.strictEqual(halfSurcharge.work_id, 3);
assert.strictEqual(halfSurcharge.daily_type, "Meia");
assert.strictEqual(halfSurcharge.manual_price_mode, "surcharge");
assert.strictEqual(halfSurcharge.manual_price_amount, 400);

console.log("Tablet agenda parser tests: OK");
