const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const {runInThisContext} = require('node:vm');
const {join} = require('node:path');
runInThisContext(readFileSync(join(__dirname, '../graph/filter.js'), 'utf8'));
const ids = items => new Set(items.map(item => item.id));

test('filters preserve reachability, entry identity and original metrics across restore', () => {
  const frame = {
    entry_ids: ['entry'],
    nodes: [{id:'entry', group:'front', diameter:112, label:'QPS 2'}, {id:'sibling', group:'front'},
      {id:'middle', group:'worker', importance:'low'},
      {id:'leaf', group:'model', diameter:160, label:'QPS 20', importance:'high'},
      {id:'alternate', group:'backup'}, {id:'island', group:'model'}],
    edges: [{id:'a', source:'entry', target:'middle'}, {id:'b', source:'middle', target:'leaf'},
      {id:'c', source:'entry', target:'alternate'}, {id:'d', source:'alternate', target:'leaf'},
      {id:'cycle', source:'leaf', target:'alternate'}, {id:'same-service', source:'entry', target:'sibling'}],
  };
  const original = structuredClone(frame), all = ['front','worker','model','backup'];
  const filter = (groups, importance) => rkFilterGraph(frame, new Set(groups), importance && new Set(importance));
  const full = filter(all), cut = filter(['front','model']), alternate = filter(['model','backup']), none = filter([]);
  assert.deepEqual(ids(full.nodes), new Set(['entry','sibling','middle','leaf','alternate']));
  assert.deepEqual(ids(cut.nodes), new Set(['entry','sibling']));
  assert.deepEqual(ids(alternate.nodes), new Set(['entry','alternate','leaf']));
  assert.deepEqual(ids(alternate.edges), new Set(['c','d','cycle']));
  assert.deepEqual(ids(none.nodes), new Set(['entry'])); assert.deepEqual(none.edges, []);
  assert.deepEqual(filter(all), full);
  assert.deepEqual(alternate.nodes.find(n => n.id === 'leaf'), frame.nodes[3]);
  assert.deepEqual(ids(filter(all,['high']).nodes), new Set(['entry']));
  assert.deepEqual(ids(filter(all,['high','medium']).nodes), new Set(['entry','sibling','alternate','leaf']));
  assert.deepEqual(ids(filter(all,['high','low']).nodes), new Set(['entry','middle','leaf']));
  assert.deepEqual(ids(filter(['front','model'],['high','medium']).nodes), new Set(['entry','sibling']));
  assert.deepEqual(frame, original);
});

for (const [entry, selected, expected] of [
  [2,['= 0'],['entry','zero']], [2,['N/A'],['entry','missing']],
  [2,['< 入口'],['entry','small']], [2,['≥ 入口'],['entry','equal','large']],
  [2,['< 入口','≥ 入口'],['entry','small','equal','large','leaf']],
  [2,['= 0','N/A','< 入口','≥ 入口'],['entry','zero','missing','small','equal','large','leaf']],
  [0,['< 入口'],['entry']], [0,['≥ 入口'],['entry','small','equal','large','leaf']],
  [null,[],['entry','small','equal','large','leaf']],
]) test(`QPS reference ${entry}, selection ${selected}`, () => {
  const frame = {entry_ids:['entry'],
    nodes: Object.entries({entry, zero:0, missing:null, small:1, equal:2, large:5, leaf:8})
      .map(([id,comparison_value]) => ({id, comparison_value, group:'service', importance:'high'})),
    edges: ['zero','missing','small','equal','large'].map(target => ({source:'entry',target}))
      .concat([{source:'small',target:'leaf'}]),
  };
  const original = structuredClone(frame);
  const result = rkFilterGraph(frame,new Set(['service']),new Set(['high']),new Set(selected));
  assert.deepEqual(ids(result.nodes), new Set(expected));
  assert.equal(rkEntryValue(frame), entry);
  assert.equal(rkEntryValue({...frame, entry_ids:['entry','zero']}), null);
  assert.deepEqual(frame, original);
});
