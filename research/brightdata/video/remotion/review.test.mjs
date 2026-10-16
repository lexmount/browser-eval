import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const owner=path.dirname(fileURLToPath(import.meta.url));
const failures=[];
for (const scenario of ['overview','alias','aggregate','widebox','pageerror','overflow','offcanvas','wrappedoverflow','captionoverflow','missingfocus','missingaggregate','wrongscene','wrongoverview']) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'brightdata-review-test-'));
  const project=path.join(dir,'remotion');
  fs.mkdirSync(path.join(project,'src'),{recursive:true});fs.mkdirSync(path.join(project,'public'));
  fs.symlinkSync(path.join(owner,'node_modules'),path.join(project,'node_modules'),'dir');
  const aggregate=['aggregate','missingaggregate'].includes(scenario);
  const scene=aggregate?'quality':scenario==='alias'?'browser':'unlocker';
  const target=aggregate?'availability':scenario==='alias'?'session':scenario==='overview'?'whole':'input';
  fs.writeFileSync(path.join(project,'src/timeline.json'),JSON.stringify({fps:30,durationInFrames:120,scenes:[{id:scene,start:0,end:4,from:0,durationInFrames:120,speechEnd:2}],events:[{scene,start:.5,end:2,target},{scene,start:2,end:4,target:'whole'}]}));
  fs.writeFileSync(path.join(project,'src/index.tsx'),`
import React,{useEffect} from 'react';
import {registerRoot,Composition,useCurrentFrame} from 'remotion';
function Fixture(){const f=useCurrentFrame();useEffect(()=>{${scenario==='pageerror'?'setTimeout(()=>{throw new Error("fixture pageerror")},0);':''}},[]);
const speaking=f>=15&&f<60;
return <div><div data-scene="${scenario==='wrongscene'?'another-scene':scene}">
<div data-target="${aggregate?'coverage':'input'}" data-active={${scenario==='missingfocus'||scenario==='overview'?'false':'speaking'}?"true":"false"}>Readable product</div>
${scenario==='aggregate'?'<div data-target="success" data-active={speaking?"true":"false"}>Task success</div>':''}
${scenario==='wrongoverview'?'<div data-target="unexpected" data-active={!speaking?"true":"false"}>Wrong focus</div>':''}
${scenario==='overflow'?'<div style={{width:15,height:12,whiteSpace:"nowrap",overflow:"hidden"}}>Visible clipped content</div>':''}
${scenario==='widebox'?'<div style={{width:2500}}>Readable text inside a wide label box</div>':''}
${scenario==='offcanvas'?'<div style={{position:"absolute",top:1090}}>Text outside the film</div>':''}
${scenario==='wrappedoverflow'?'<div style={{position:"absolute",top:1000,width:100,fontSize:30}}>Wrapped text extends below the visible frame</div>':''}
</div>${scenario==='captionoverflow'?'<div data-review-caption style={{width:15,height:12,whiteSpace:"nowrap",overflow:"hidden"}}>An unreadable subtitle outside the scene container</div>':''}{f>=60&&<div>回看要点<div style={{height:5,width:f,background:'blue'}}/></div>}</div>}
registerRoot(()=> <Composition id="BrightData" component={Fixture} width={1920} height={1080} fps={30} durationInFrames={120}/>);
`);
  const result=spawnSync(process.execPath,[path.join(owner,'review.mjs')],{cwd:project,encoding:'utf8',timeout:120000});
  const positive=['overview','alias','aggregate','widebox'].includes(scenario);
  console.log(`${scenario}: exit ${result.status}`);
  if (result.status!==(positive?0:1)) failures.push(`${scenario}: unexpected exit ${result.status}\n${result.stdout}\n${result.stderr}`);
  const report=path.join(dir,'review/current/dom-review.json');
  if(!fs.existsSync(report)) failures.push(`${scenario}: diagnostics not saved`);
  else {
    const data=JSON.parse(fs.readFileSync(report));
    if(!['overflow','offcanvas','wrappedoverflow','captionoverflow'].includes(scenario) && data.failures?.some(f=>f.code==='overflow')) failures.push(`${scenario}: unrelated overflow masks the intended fault`);
    if(!positive && !data.failures?.some(f=> f.code===({pageerror:'pageerror',overflow:'overflow',offcanvas:'overflow',wrappedoverflow:'overflow',captionoverflow:'overflow',missingfocus:'focus',missingaggregate:'focus',wrongscene:'scene',wrongoverview:'focus'}[scenario]))) failures.push(`${scenario}: missing specific diagnostic`);
  }
  fs.rmSync(dir,{recursive:true,force:true});
}
if(failures.length){console.error(failures.join('\n'));process.exitCode=1;}
