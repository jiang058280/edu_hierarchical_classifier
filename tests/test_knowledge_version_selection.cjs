// Regression: a global active version owned by another teacher must not hide
// the current teacher's documents. Execute the actual page render function.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '../static/teacher_knowledge_base.html'), 'utf8');
const code = html.slice(html.indexOf('function renderVersions('), html.indexOf('function renderDocuments('));
function selection(selected, active) {
  const context = vm.createContext({
    versions:[{id:3, version:'own-a'}, {id:4, version:'own-b'}],
    selectedVersionId:selected, active, $:() => ({innerHTML:''}),
    T:{escapeHtml:String}, statusBadge:() => '',
  });
  vm.runInContext(code + '\nrenderVersions(active);', context);
  return context.selectedVersionId;
}
assert.equal(selection(null, {id:1}), 3);
assert.equal(selection(null, {id:4}), 4);
assert.equal(selection(3, {id:4}), 3);
assert.equal(selection(999, {id:1}), 3);
assert.equal(selection(null, undefined), 3);
console.log('Knowledge version selection tests passed');
