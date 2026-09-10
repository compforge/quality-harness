import { strToU8, zipSync } from "fflate";
// Bundled as text so saved reports work offline, without fetching a decoder or Worker script.
import zipSource from "../../node_modules/fflate/umd/index.js" with { type: "text" };

/** The archive retains complete details; the UI inflates only the selected node or span. */
export class TraceArchive {
  private readonly entries: Record<string, Uint8Array> = {};
  add(name: string, value: unknown): string {
    this.entries[name] = strToU8(JSON.stringify(value));
    return name;
  }
  embed(): string {
    return `<template id="trace-archive">${Buffer.from(zipSync(this.entries, { level: 6 })).toString("base64")}</template>`;
  }
}

export const ARCHIVE_SCRIPT = `${zipSource}
${String.raw`
const archiveElement=document.getElementById('trace-archive');
let encodedArchive=archiveElement.content.textContent.trim();
const archiveBytes=new Uint8Array(encodedArchive.length*3/4-(encodedArchive.endsWith('==')?2:encodedArchive.endsWith('=')?1:0));
for(let start=0,offset=0;start<encodedArchive.length;start+=1048576){const part=atob(encodedArchive.slice(start,start+1048576));for(let i=0;i<part.length;i++)archiveBytes[offset++]=part.charCodeAt(i);}
archiveElement.remove();encodedArchive='';
function readTraceEntry(name){const data=fflate.unzipSync(archiveBytes,{filter:entry=>entry.name===name})[name];if(!data)throw new Error('Trace 内容缺失：'+name);return JSON.parse(fflate.strFromU8(data));}
// No decoded-detail cache: changing selection releases its objects and DOM.
function loadNode(n){return n.payload?{...n,...readTraceEntry(n.payload)}:n;}
function loadSpan(sid){return SPANS[sid]?readTraceEntry(SPANS[sid].payload):undefined;}
`}`;
