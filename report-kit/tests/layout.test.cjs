const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const {runInThisContext} = require('node:vm');
const {join} = require('node:path');
const ELK = require('../vendor/elk.bundled.js');
runInThisContext(readFileSync(join(__dirname, '../graph/layout.js'), 'utf8'));

for (const viewport of [{width:1600,height:900}, {width:600,height:900}]) {
  test(`grouped layout preserves every operation without overlap at ${viewport.width}x${viewport.height}`, async () => {
    const nodes = [{id:'entry',group:'entry',width:110,height:110},
      ...Array.from({length:24}, (_,i) => ({id:`op${i}`,group:'dense',width:75+i*3,height:75+i*3})),
      {id:'step1',group:'pipeline',width:130,height:100}, {id:'step2',group:'pipeline',width:130,height:100},
      {id:'loose',width:80,height:80}];
    const edges = [...Array.from({length:24}, (_,i) => ({source:'entry',target:`op${i}`})),
      {source:'op0',target:'step1'}, {source:'step1',target:'step2'},
      {source:'step2',target:'entry'}, {source:'step2',target:'loose'}];
    const layout = await rkLayout(nodes,edges,viewport,new ELK());
    assert.deepEqual(new Set(layout.positions.keys()), new Set(nodes.map(n => n.id)));
    assert.equal((await rkLayout([],[],viewport,new ELK())).positions.size, 0);
    assert.ok(layout.width > 0 && layout.height > 0);
    for (const [i,left] of nodes.entries()) for (const right of nodes.slice(i+1)) {
      const a = layout.positions.get(left.id), b = layout.positions.get(right.id);
      assert.ok(Math.abs(a.x-b.x) >= (left.width+right.width)/2 || Math.abs(a.y-b.y) >= (left.height+right.height)/2,
        `${left.id} overlaps ${right.id}`);
    }
    const dense = nodes.filter(n => n.group === 'dense').map(n => layout.positions.get(n.id));
    assert.ok(new Set(dense.map(p => p.x)).size > 1 && new Set(dense.map(p => p.y)).size > 1);
    if (viewport.width > viewport.height) assert.ok(layout.width/layout.height > 0.8 && layout.width/layout.height < 3.5);
  });
}
