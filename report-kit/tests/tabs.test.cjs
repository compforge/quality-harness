const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');

test('report tabs support click and keyboard without a chart dependency', () => {
  const panels=[{hidden:false},{hidden:true}], focused=[];
  const tabs=panels.map((_,i)=>({
    attrs:{'aria-controls':String(i),'aria-selected':String(i===0)},tabIndex:i===0?0:-1,handlers:{},
    getAttribute(key){return this.attrs[key];}, setAttribute(key,value){this.attrs[key]=value;},
    addEventListener(type,handler){this.handlers[type]=handler;},focus(){focused.push(i);},
  }));
  const document={
    querySelectorAll(selector){return selector==='.report-tabs'?[{querySelectorAll(){return tabs;}}]:[];},
    getElementById(id){return panels[Number(id)];},
  };
  runInNewContext(readFileSync(join(__dirname,'../tabs/view.js'),'utf8'),{document});
  tabs[1].handlers.click();
  assert.deepEqual(panels.map(p=>p.hidden),[true,false]);
  assert.equal(tabs[1].attrs['aria-selected'],'true');
  assert.equal(tabs[0].tabIndex,-1);
  tabs[1].handlers.keydown({key:'ArrowRight',preventDefault(){}});
  assert.deepEqual(panels.map(p=>p.hidden),[false,true]);
  assert.deepEqual(focused,[0]);
  tabs[0].handlers.keydown({key:'End',preventDefault(){}});
  assert.deepEqual(panels.map(p=>p.hidden),[true,false]);
});
