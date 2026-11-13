async function loadSavedPage(){
 let target=document.querySelector('[data-delivery-target]');
 if(!target){target=document.createElement('pre');target.dataset.deliveryTarget='';document.querySelector('main').appendChild(target)}
 const selected=decodeURIComponent(location.hash.slice(1));
 const separator=selected.indexOf('#');
 const path=separator<0?selected:selected.slice(0,separator);
 const anchor=separator<0?'':selected.slice(separator+1);
 try{
  if(!path)throw Error('No result selected');
  const bytes=restoreBytes(await stored(path));
  const text=new TextDecoder('utf-8').decode(bytes);
  if(path.endsWith('.html')){
   const doc=new DOMParser().parseFromString(text,'text/html');
   document.documentElement.lang=doc.documentElement.lang;
   document.title=doc.title;
   document.querySelector('nav').replaceWith(doc.querySelector('nav'));
   document.querySelector('main').replaceWith(doc.querySelector('main'));
   document.querySelector('dialog').replaceWith(doc.querySelector('dialog'));
   rewriteLinks(document);
   const script=document.createElement('script');
   script.textContent='{'+doc.querySelector('script').textContent+'}';
   document.body.appendChild(script);
   if(anchor)requestAnimationFrame(()=>document.getElementById(anchor)?.scrollIntoView());
  }else{
   let formatted=text;try{formatted=JSON.stringify(JSON.parse(text),null,2)}catch{}
   target.textContent=formatted;
   let download=document.querySelector('[data-evidence-download]');
   if(!download){download=document.createElement('a');download.dataset.evidenceDownload='';target.before(download)}
   if(download.href.startsWith('blob:'))URL.revokeObjectURL(download.href);
   download.href=URL.createObjectURL(new Blob([bytes],{type:'application/octet-stream'}));
   download.download=path.split('/').pop();download.textContent=document.documentElement.lang.startsWith('zh')?'__DOWNLOAD_LABEL_ZH__':'Download original response';
  }
 }catch(e){target.textContent='Unable to load saved evidence: '+e.message}
}
window.addEventListener('hashchange',loadSavedPage);loadSavedPage();
