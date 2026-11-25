import { expect, test } from "bun:test";
import { runArgv } from "../src/kubernetes/executor";

test("Node runtime preserves TCP half-close and the complete reverse response", async () => {
  // Bun's net.Server half-close differs from Node; exercise the supported distribution runtime.
  const transport = new URL('../dist/transport/index.js', import.meta.url).href;
  const executor = new URL('../dist/kubernetes/executor.js', import.meta.url).href;
  const result = await runArgv(['node', '--input-type=module', '-e', `
    import assert from 'node:assert/strict';
    import {createServer,createConnection} from 'node:net';
    import {once} from 'node:events';
    import {PodRelayTransport} from ${JSON.stringify(transport)};
    import {runArgv} from ${JSON.stringify(executor)};
    const server=createServer({allowHalfOpen:true}, socket=>{
      const chunks=[];socket.on('data',x=>chunks.push(x));
      socket.on('end',()=>socket.end(Buffer.concat(chunks)));
    });
    server.listen(0,'127.0.0.1');await once(server,'listening');
    const relay=new PodRelayTransport({namespace:'test',startupTimeoutMs:3000,connectTimeoutMs:1000,
      maxConnections:2,maxTargets:2,maxCandidatePods:1},{executor:{
        run:async()=>({ok:true,stdout:JSON.stringify({items:[{metadata:{name:'a'},status:{phase:'Running',
          containerStatuses:[{name:'a',ready:true}]}}]})}),
        exec:async(t,c,o)=>runArgv(c,o)
      },startForward:async a=>({ok:true,value:{target:a.target,localPort:a.remotePort,command:[],stop(){}}})});
    let socket;
    try {
      const address=await relay.connect({host:'127.0.0.1',port:server.address().port});
      socket=createConnection(address);
      socket.setTimeout(3000,()=>socket.destroy(new Error('test timeout')));
      const chunks=[];socket.on('data',x=>chunks.push(x));
      const done=once(socket,'end');await once(socket,'connect');
      const payload=Buffer.alloc(1048576,167);socket.end(payload);await done;
      assert.ok(Buffer.concat(chunks).equals(payload));
    } finally {socket?.destroy();await relay.dispose();server.close();}
  `], { timeoutMs: 5000 });
  expect(result.stderr).toBe('');
  expect(result.ok).toBe(true);
});
