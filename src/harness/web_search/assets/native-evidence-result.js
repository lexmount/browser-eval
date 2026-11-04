
function nativeEvidenceResult(value,index){
 if(value&&typeof value.body==='string')value=JSON.parse(value.body);
 else if(value&&value.body&&typeof value.body==='object')value=value.body;
 const items=value&&(value.results||value.organic||(value.data&&value.data.webPages&&value.data.webPages.value));
 if(!Array.isArray(items)||!Number.isInteger(index)||index<0||index>=items.length)throw Error('Result not found in native response');
 return items[index];
}
