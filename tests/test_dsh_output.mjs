import assert from 'node:assert/strict';
import { test } from 'node:test';
import { projectDeclaredFetch } from '../src/harness/web_fetch/workers/dsh_output.mjs';
const result = body => ({isError:false,value:{url:'https://example.com/',statusCode:200,body:{kind:'html',content:body},truncated:false},content:[{type:'text',text:'Different model presentation'}],meta:{statusCode:200,truncated:true}});
test('the declared native body is passed verbatim, not replaced by presentation',()=>{
 const raw='<html><body><a href="https://example.com/item">Native item</a></body></html>';
 const p=projectDeclaredFetch(result(raw));
 assert.equal(p.content,raw);assert.equal(p.ok,true);assert.equal(p.truncated,false);
 assert.equal(p.rendered_tool_content,'Different model presentation');
});
test('successful empty native body stays empty even when presentation is nonempty',()=>{
 const p=projectDeclaredFetch(result(''));assert.equal(p.content,'');assert.equal(p.ok,true);
});
test('missing declared body fails rather than rescuing from presentation',()=>{
 const out=result('');delete out.value.body;
 const p=projectDeclaredFetch(out);assert.equal(p.ok,false);assert.equal(p.error_type,'invalid_response');assert.equal(p.content,'');
});
test('HTTP status failure and tool failure remain failures',()=>{
 const out=result('Origin error body');out.value.statusCode=403;
 const p=projectDeclaredFetch(out);assert.equal(p.ok,false);assert.equal(p.error_type,'http_status');assert.equal(p.content,'Origin error body');
 const e=projectDeclaredFetch({isError:true,error:{info:{code:'WEB_BLOCKED_URL'}},content:[{type:'text',text:'Blocked'}]});
 assert.equal(e.ok,false);assert.equal(e.error_type,'WEB_BLOCKED_URL');assert.equal(e.content,'Blocked');
});
