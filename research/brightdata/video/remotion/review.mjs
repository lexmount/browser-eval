import { bundle } from '@remotion/bundler';
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {createHash} from 'node:crypto';
import {validateSample} from './review-policy.mjs';
const timelineBytes=fs.readFileSync('src/timeline.json');
const timeline=JSON.parse(timelineBytes);
const timelineSha256=createHash('sha256').update(timelineBytes).digest('hex');
const output=path.resolve('../review/current');
fs.mkdirSync(path.join(output,'focus'),{recursive:true});
const errors=[],failures=[],records=[],openings=[],pauses=[];
let server,browser,page;
const atFrame=(time)=>Math.round(time*timeline.fps);
function targetAt(scene,frame){return timeline.events.filter(e=>e.scene===scene&&e.start<=frame/timeline.fps).at(-1)?.target||'whole';}
async function sample(frame,scene,target,kind){
  await page.evaluate(f=>window.remotion_setFrame(f,'BrightData',1),frame);
  await page.waitForFunction(()=>window.remotion_renderReady===true);
  await page.evaluate(()=>document.fonts.ready);
  const state=await page.evaluate(()=>{
    const scenes=[...document.querySelectorAll('[data-scene]')];
    const clipped=[...document.querySelectorAll('[data-scene] div')].filter(e=>{
      const r=e.getBoundingClientRect();
      return r.width>0&&r.height>0&&e.childElementCount<3&&e.textContent.trim()&&
        (e.scrollHeight>e.clientHeight+2||e.scrollWidth>e.clientWidth+2);
    }).map(e=>({text:e.textContent,sh:e.scrollHeight,ch:e.clientHeight,sw:e.scrollWidth,cw:e.clientWidth}));
    return {scenes:scenes.map(e=>e.getAttribute('data-scene')),
      active:[...document.querySelectorAll('[data-scene] [data-active="true"]')].map(e=>e.getAttribute('data-target')),
      clipped,text:scenes[0]?.textContent,recapVisible:document.body.innerText.includes('回看要点')};
  });
  const result={time:frame/timeline.fps,frame,kind,target,scene:state.scenes[0],...state};
  failures.push(...validateSample({scene,target},state).map(f=>({...f,kind,time:result.time,scene,target})));
  return result;
}
try {
  const root=await bundle({entryPoint:path.resolve('src/index.tsx'),publicDir:path.resolve('public')});
  server=http.createServer((req,res)=>{
    let p=path.join(root,decodeURIComponent(req.url.split('?')[0]));
    if(fs.existsSync(p)&&fs.statSync(p).isDirectory())p=path.join(p,'index.html');
    try {res.setHeader('Content-Type',p.endsWith('.js')?'text/javascript':p.endsWith('.html')?'text/html':p.endsWith('.png')?'image/png':'application/octet-stream');res.end(fs.readFileSync(p));}
    catch {res.statusCode=404;res.end();}
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const executablePath=process.env.CHROME_BIN||['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','/usr/bin/google-chrome','/usr/bin/chromium'].find(p=>fs.existsSync(p));
  if(!executablePath)throw Error('Chrome not found; set CHROME_BIN');
  browser=await puppeteer.launch({executablePath,headless:true});
  page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.setViewport({width:1920,height:1080});
  await page.evaluateOnNewDocument(()=>{
    window.remotion_isMainTab=true;window.remotion_puppeteerTimeout=30000;window.remotion_inputProps='{}';
    window.remotion_initialFrame=0;window.remotion_attempt=1;window.remotion_audioEnabled=false;window.remotion_videoEnabled=true;window.process={env:{NODE_ENV:'production'}};
  });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(()=>typeof window.remotion_setBundleMode==='function');
  await page.evaluate(({n,fps})=>window.remotion_setBundleMode({type:'composition',compositionName:'BrightData',serializedResolvedPropsWithSchema:'{}',compositionDurationInFrames:n,compositionFps:fps,compositionHeight:1080,compositionWidth:1920}),{n:timeline.durationInFrames,fps:timeline.fps});
  await page.waitForFunction(()=>window.remotion_renderReady===true);
  for(const [i,e] of timeline.events.entries()) {
    const scene=timeline.scenes.find(s=>s.id===e.scene);
    if(!scene)throw Error(`Unknown event scene: ${e.scene}`);
    const next=timeline.events.slice(i+1).find(n=>n.scene===e.scene&&n.target!==e.target);
    const end=Math.min(scene.from+scene.durationInFrames,next?Math.ceil(next.start*timeline.fps):Infinity);
    const frame=Math.min(atFrame(e.start+.7),end-1);
    if(frame<Math.ceil(e.start*timeline.fps))throw Error(`Focus has no visible frame: ${e.scene}/${e.target}`);
    const result=await sample(frame,e.scene,e.target,'event');records.push({...result,eventIndex:i});
    if(i===0||timeline.events[i-1].scene!==e.scene||timeline.events[i-1].target!==e.target)
      await page.screenshot({path:path.join(output,'focus',`${String(i).padStart(3,'0')}-${e.scene}-${e.target}.png`)});
  }
  for(const s of timeline.scenes){
    const first=timeline.events.find(e=>e.scene===s.id);
    if(!first)throw Error(`Scene has no focus events: ${s.id}`);
    const frame=Math.max(s.from,Math.ceil(first.start*timeline.fps)-1);
    openings.push(await sample(frame,s.id,targetAt(s.id,frame),'opening'));
    await page.screenshot({path:path.join(output,'focus',`opening-${s.id}.png`)});
    const samples=[];
    for(const fraction of [.15,.8]){
      const frame=atFrame(s.speechEnd+(s.end-s.speechEnd)*fraction);
      const result=await sample(frame,s.id,targetAt(s.id,frame),'pause');
      const bytes=await page.screenshot({path:path.join(output,'focus',`pause-${s.id}-${fraction}.png`)});
      samples.push({...result,bytes:bytes.toString('base64')});
    }
    const framesDiffer=samples[0].bytes!==samples[1].bytes;
    if(!framesDiffer||samples.some(s=>!s.recapVisible))failures.push({code:'pause',scene:s.id,reason:'Frozen or missing visual recap'});
    pauses.push({scene:s.id,framesDiffer,recapVisible:samples.every(s=>s.recapVisible),times:samples.map(s=>s.time),samples:samples.map(({bytes,...state})=>state)});
  }
}catch(error){failures.push({code:'runtime',message:error.stack||String(error)});}
finally {
  failures.push(...errors.map(message=>({code:'pageerror',message})));
  try {
    fs.writeFileSync(path.join(output,'opening-review.json'),JSON.stringify(openings,null,2));
    fs.writeFileSync(path.join(output,'pause-review.json'),JSON.stringify(pauses,null,2));
    fs.writeFileSync(path.join(output,'dom-review.json'),JSON.stringify({timelineSha256,errors,failures,records},null,2));
  } catch(error) {
    failures.push({code:'diagnostics',message:String(error)});
  } finally {
    await Promise.allSettled([browser?.close(),server&&new Promise(resolve=>server.close(resolve))]);
  }
}
console.log(`Review ${records.length} events; ${openings.length} openings; ${pauses.length} pauses; ${failures.length} failures`);
if(failures.length){console.error(JSON.stringify(failures,null,2));process.exitCode=1;}
