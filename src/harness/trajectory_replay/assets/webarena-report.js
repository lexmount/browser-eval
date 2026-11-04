const lang=document.documentElement.lang;
const zh=lang==='zh';
const base=zh?'':'../';
document.documentElement.dataset.theme=localStorage.getItem('report-theme')||'light';
document.querySelector('.theme')?.addEventListener('click',()=>{
  const root=document.documentElement;
  root.dataset.theme=root.dataset.theme==='dark'?'light':'dark';
  localStorage.setItem('report-theme',root.dataset.theme);
});
const fetchJson=async path=>{const response=await fetch(path);if(!response.ok)throw Error(`HTTP ${response.status}`);return response.json()};
const enhanceTables=()=>{
  const excluded='[data-results] table,[data-dataset-table] table,[data-task-matrix] table';
  document.querySelectorAll('.table-scroll>table').forEach((table,tableIndex)=>{
    const headerRows=table.tHead?.rows||[];
    const body=table.tBodies[0];
    if(headerRows.length!==1||!body||body.rows.length<2)return;
    const headers=[...headerRows[0].cells];
    const rows=[...body.rows];
    if(rows.some(row=>row.cells.length!==headers.length))return;
    const original=new Map(rows.map((row,index)=>[row,index]));
    const external=table.matches(excluded);
    let query='';
    let sortColumn=-1;
    let direction='ascending';
    let count=null;
    const value=(cell)=>{
      const explicit=cell.dataset.sort;
      if(explicit!==undefined&&explicit!=='')return {missing:false,numeric:true,value:Number(explicit)};
      const text=cell.textContent.trim();
      if(!text||text==='—')return {missing:true,numeric:false,value:''};
      const compact=text.replaceAll(',','').replace(/\s+/g,' ');
      const ratio=compact.match(/^(-?\d+(?:\.\d+)?)\s*\/\s*(-?\d+(?:\.\d+)?)/);
      if(ratio)return {missing:false,numeric:true,value:Number(ratio[1])/Number(ratio[2]||1)};
      const number=compact.match(/^(-?\d+(?:\.\d+)?)\s*(?:%|ms|s|秒|MiB|MB|KiB|次|条|个|calls?)?(?:\s|$)/i);
      if(number)return {missing:false,numeric:true,value:Number(number[1])};
      return {missing:false,numeric:false,value:compact.toLocaleLowerCase()};
    };
    const apply=()=>{
      const ordered=[...rows].sort((left,right)=>{
        if(sortColumn<0)return original.get(left)-original.get(right);
        const a=value(left.cells[sortColumn]);
        const b=value(right.cells[sortColumn]);
        if(a.missing!==b.missing)return a.missing?1:-1;
        const compared=a.numeric&&b.numeric?a.value-b.value:String(a.value).localeCompare(String(b.value),lang);
        return direction==='ascending'?compared:-compared;
      });
      let shown=0;
      ordered.forEach(row=>{
        body.append(row);
        const matches=!query||row.textContent.toLocaleLowerCase().includes(query);
        if(!external)row.hidden=!matches;
        if(matches&&!row.hidden)shown++;
      });
      if(count)count.textContent=zh?`显示 ${shown} / ${rows.length} 行`:`Showing ${shown} / ${rows.length} rows`;
    };
    headers.forEach((header,column)=>{
      const button=document.createElement('button');
      button.type='button';button.className='table-sort';button.textContent=header.textContent.trim();
      button.setAttribute('aria-label',(zh?'按此列排序：':'Sort by column: ')+button.textContent);
      header.replaceChildren(button);
      button.addEventListener('click',()=>{
        direction=sortColumn===column&&direction==='ascending'?'descending':'ascending';
        sortColumn=column;
        headers.forEach((item,index)=>{
          item.setAttribute('aria-sort',index===column?direction:'none');
          item.querySelector('.table-sort')?.removeAttribute('data-direction');
        });
        button.dataset.direction=direction;apply();
      });
      header.setAttribute('aria-sort','none');
    });
    if(!external){
      const tools=document.createElement('div');tools.className='report-table-tools';tools.dataset.tableControls=String(tableIndex);
      const label=document.createElement('label');label.textContent=zh?'筛选此表':'Filter this table';
      const input=document.createElement('input');input.type='search';input.placeholder=zh?'输入关键词':'Enter keywords';
      input.dataset.tableFilter=String(tableIndex);label.append(input);tools.append(label);
      const reset=document.createElement('button');reset.type='button';reset.textContent=zh?'重置':'Reset';tools.append(reset);
      count=document.createElement('span');count.className='report-table-count';count.setAttribute('role','status');tools.append(count);
      table.closest('.table-scroll').before(tools);
      table.dataset.tableControls=String(tableIndex);
      input.addEventListener('input',()=>{query=input.value.trim().toLocaleLowerCase();apply()});
      reset.addEventListener('click',()=>{query='';sortColumn=-1;direction='ascending';input.value='';headers.forEach(item=>{item.setAttribute('aria-sort','none');item.querySelector('.table-sort')?.removeAttribute('data-direction')});apply();input.focus()});
    }
    table.dataset.enhanced='true';apply();
  });
};
enhanceTables();
const datasetTable=document.querySelector('[data-dataset-table]');
if(datasetTable){
  const rows=[...datasetTable.querySelectorAll('tbody tr')];
  const controls=[...document.querySelectorAll('[data-dataset-filter]')];
  const params=new URLSearchParams(location.search);
  controls.forEach(control=>{const value=params.get(control.dataset.datasetFilter);if(value!==null)control.value=value});
  const applyDataset=()=>{
    const query=(document.querySelector('[data-dataset-filter=q]')?.value||'').trim().toLocaleLowerCase();
    let shown=0;
    rows.forEach(row=>{
      const matches=controls.every(control=>!control.value||control.dataset.datasetFilter==='q'||(control.dataset.datasetFilter==='site'?(row.dataset.sites||'').split(' ').includes(control.value):row.dataset[control.dataset.datasetFilter]===control.value))&&(!query||(row.dataset.search||row.textContent).toLocaleLowerCase().includes(query));
      row.hidden=!matches;if(matches)shown++;
    });
    document.querySelector('[data-dataset-count]').textContent=zh?`显示 ${shown} / ${rows.length} 个任务`:`Showing ${shown} / ${rows.length} tasks`;
    document.querySelector('[data-dataset-empty]').hidden=shown!==0;
    const next=new URLSearchParams();controls.forEach(control=>{if(control.value)next.set(control.dataset.datasetFilter,control.value)});
    history.replaceState(null,'',location.pathname+(next.size?'?'+next:''));
  };
  controls.forEach(control=>control.addEventListener(control.tagName==='INPUT'?'input':'change',applyDataset));
  applyDataset();
}
const matrix=document.querySelector('[data-task-matrix]');
if(matrix){
  const matrixRows=[...matrix.querySelectorAll('tbody tr')];
  const relation=document.querySelector('[data-matrix-relation]');
  relation.value='';
  const site=document.querySelector('[data-matrix-site]');
  const baseline=document.querySelector('[data-matrix-baseline]');
  const boxes=[...document.querySelectorAll('[data-matrix-method]')];
  const count=document.querySelector('[data-matrix-count]');
  const applyMatrix=()=>{
    boxes.forEach(box=>{box.disabled=box.value===baseline.value;box.closest('label')?.classList.toggle('disabled',box.disabled);if(box.disabled)box.checked=false});
    const others=boxes.filter(box=>box.checked&&!box.disabled).map(box=>box.value);
    const selected=[baseline.value,...others];
    let shown=0;
    matrixRows.forEach(row=>{
      const pass=new Set((row.dataset.pass||'').split(' ').filter(Boolean));
      const fail=new Set((row.dataset.fail||'').split(' ').filter(Boolean));
      row.querySelectorAll('[data-participant]').forEach(link=>{link.hidden=!selected.includes(link.dataset.participant)});
      const allMeasured=selected.every(engine=>pass.has(engine)||fail.has(engine));
      const matchRelation=others.length>0&&(!relation.value||(allMeasured&&((relation.value==='different'&&selected.some(engine=>pass.has(engine))&&selected.some(engine=>fail.has(engine)))||(relation.value==='baseline-pass-others-fail'&&pass.has(baseline.value)&&others.every(engine=>fail.has(engine)))||(relation.value==='baseline-fail-others-pass'&&fail.has(baseline.value)&&others.every(engine=>pass.has(engine)))||(relation.value==='all-pass'&&selected.every(engine=>pass.has(engine)))||(relation.value==='all-fail'&&selected.every(engine=>fail.has(engine)))||(relation.value==='exact-pass'&&selected.every(engine=>pass.has(engine))&&pass.size===selected.length))));
      const matchSite=!site.value||row.dataset.site===site.value;
      row.hidden=!(matchRelation&&matchSite);
      if(!row.hidden)shown++;
    });
    count.textContent=others.length?(zh?`找到 ${shown} / ${matrixRows.length} 个任务`:`Showing ${shown} / ${matrixRows.length} tasks`):(zh?'请至少选择一个对比方法':'Select at least one comparison method');
  };
  baseline.addEventListener('change',()=>{boxes.forEach(box=>box.checked=box.value!==baseline.value);applyMatrix()});
  [...boxes,relation,site].forEach(control=>control.addEventListener('change',applyMatrix));
  applyMatrix();
}
const inventory=document.querySelector('[data-results]');
if(inventory){
  const rows=[...inventory.querySelectorAll('tbody tr')];
  const controls=[...document.querySelectorAll('[data-filter]')];
  const dialog=document.querySelector('#diagnosis-dialog');
  const params=new URLSearchParams(location.search);
  let byKey={};
  controls.forEach(control=>{
    const selected=params.get(control.dataset.filter);
    if(selected!==null)control.value=selected;
  });
  function applyFilters(){
    const query=(document.querySelector('[data-filter=q]')?.value||'').toLowerCase();
    const task=params.get('task');
    let shown=0;
    rows.forEach(row=>{
      const result=byKey[row.dataset.key];
      const haystack=(row.textContent+' '+(result?.intent||'')+' '+(result?.program_error||'')+' '+(result?.reason_zh||'')+' '+(result?.reason_en||'')).toLowerCase();
      const matches=controls.every(control=>!control.value||control.dataset.filter==='q'||row.dataset[control.dataset.filter]===control.value)&&(!task||row.dataset.task===task)&&(!query||haystack.includes(query));
      row.hidden=!matches;
      if(matches)shown++;
    });
    const counter=document.querySelector('[data-result-count]');
    if(counter)counter.textContent=zh?`显示 ${shown} / ${rows.length} 条结果`:`Showing ${shown} / ${rows.length} results`;
  }
  controls.forEach(control=>control.addEventListener('input',applyFilters));
  fetchJson(base+'data/results.json').then(data=>{byKey=Object.fromEntries(data.map(item=>[item.key,item]));inventory.dataset.resultsReady='true';applyFilters()}).catch(()=>applyFilters());
  inventory.addEventListener('click',async event=>{
    if(event.target.closest('a'))return;
    const row=event.target.closest('tr[data-key]');
    if(!row||!dialog)return;
    const result=byKey[row.dataset.key];
    if(!result)return;
    const detail=result.detail_url||'result/'+result.key+'.html';
    dialog.querySelector('[data-dialog-title]').textContent=result.browser+' · '+(zh?'任务':'Task')+' #'+result.task_id;
    dialog.querySelector('[data-dialog-stage]').textContent=result.failure_stage==='browser_session_setup'?(zh?'浏览器会话初始化':'Browser session setup'):result.failure_stage==='browser_tool_execution'?(zh?'浏览器动作':'Browser action'):result.failure_stage==='evidence_finalization'?(zh?'执行证据收尾':'Evidence finalization'):result.failure_stage==='official_evaluator_execution'?(zh?'官方评测器执行':'Official evaluator execution'):result.failure_stage==='side_effect_not_observed'?(zh?'网络验收条件未满足':'Network acceptance not satisfied'):result.failure_stage==='official_evaluation'?(zh?'官方验收':'Official evaluation'):(zh?'无':'None');
    dialog.querySelector('[data-dialog-error]').textContent=zh?'正在读取完整证据…':'Loading complete evidence…';
    delete dialog.dataset.loaded;
    dialog.querySelector('[data-dialog-link]').href=detail;
    dialog.querySelector('[data-dialog-step-link]').href=detail+'#trajectory';
    dialog.showModal();
    try{
      const evidence=await fetchJson(base+'data/evidence/'+result.key+'.json');
      const step=evidence.programmatic_failure?.step;
      const trigger=evidence.programmatic_failure?.trigger_step;
      const failedAction=(evidence.trajectory||[]).find(action=>action.index===step);
      dialog.querySelector('[data-dialog-step]').textContent=step?(failedAction?.arguments?.action||'')+' #'+step:trigger?(zh?'触发动作':'Trigger action')+' #'+trigger:evidence.programmatic_failure?.stage==='browser_session_setup'?(zh?'轨迹开始前':'Before trajectory'):(zh?'动作完成后':'After actions completed');
      dialog.querySelector('[data-dialog-step-link]').href=detail+(step?'#step-'+step:trigger?'#step-'+trigger:'#trajectory');
      dialog.querySelector('[data-dialog-category]').textContent=(zh?evidence.review?.label_zh:evidence.review?.label_en)||(zh?'未复核':'Not reviewed');
      const exact=step?(failedAction?.result||evidence.programmatic_failure?.message||''):evidence.programmatic_failure?.stage==='side_effect_not_observed'?(evidence.programmatic_failure.message+'\n\nExpected network event:\n'+JSON.stringify(evidence.programmatic_failure.expected_network,null,2)):evidence.official_evaluation?JSON.stringify(evidence.official_evaluation,null,2):(evidence.programmatic_failure?.message||'');
      dialog.querySelector('[data-dialog-error]').textContent=exact;
      dialog.querySelector('[data-dialog-cause]').textContent=(zh?evidence.review?.reason_zh:evidence.review?.reason_en)||(zh?'尚无复核判断':'No reviewed cause');
      dialog.querySelector('[data-dialog-remediation]').textContent=(zh?'处理方：':'Owner: ')+(evidence.review?.[zh?'remediation_owner_zh':'remediation_owner_en']||evidence.review?.remediation_owner||'—')+' · '+(zh?'状态：':'Status: ')+(evidence.review?.[zh?'remediation_status_zh':'remediation_status_en']||evidence.review?.remediation_status||'—');
      dialog.dataset.loaded='true';
    }catch(error){dialog.querySelector('[data-dialog-error]').textContent=(zh?'证据读取失败：':'Evidence loading failed: ')+error.message;dialog.dataset.loaded='error'}
  });
  dialog?.querySelector('.dialog-head button')?.addEventListener('click',()=>dialog.close());
}
const stepDialog=document.querySelector('#step-dialog');
if(stepDialog){
  let requestId=0;
  let evidencePromise;
  let standardPromise;
  const evidence=()=>evidencePromise??=fetchJson(stepDialog.dataset.evidenceUrl);
  const standard=()=>standardPromise??=fetchJson(stepDialog.dataset.standardUrl);
  document.querySelector('#trajectory')?.addEventListener('click',async event=>{
    const button=event.target.closest('button[data-open-step]');
    if(!button)return;
    const index=Number(button.dataset.openStep);
    const current=++requestId;
    stepDialog.querySelector('[data-step-title]').textContent=(zh?'第 ':'Step ')+index+(zh?' 步':'');
    stepDialog.querySelector('[data-step-status]').textContent=zh?'正在读取逐步证据…':'Loading step evidence…';
    for(const key of ['planned','actual','result','error'])stepDialog.querySelector('[data-step-'+key+']').textContent='';
    stepDialog.querySelector('[data-step-finding]').hidden=true;
    stepDialog.querySelector('[data-step-image-wrap]').hidden=true;
    if(!stepDialog.open)stepDialog.showModal();
    try{
      const [frozen,measured]=await Promise.all([standard(),evidence()]);
      if(current!==requestId||!stepDialog.open)return;
      const planned=frozen.tool_calls[index-1];
      const actual=(measured.trajectory||[]).find(item=>item.index===index);
      if(!planned)throw Error(zh?'标准轨迹缺少该步':'Fixed trajectory is missing this step');
      stepDialog.querySelector('[data-step-planned]').textContent=JSON.stringify(planned,null,2);
      stepDialog.querySelector('[data-step-actual]').textContent=actual?JSON.stringify(actual.arguments||{},null,2):(zh?'未执行':'Not executed');
      stepDialog.querySelector('[data-step-result]').textContent=actual?(actual.result||''):(zh?'该步没有工具调用或返回。':'No tool call or response for this step.');
      if(actual?.action_diagnostics){
        const label=zh?'动作派发时的控件状态（不改变工具返回或任务成绩）':'Control state at dispatch (does not change tool status or task score)';
        stepDialog.querySelector('[data-step-result]').textContent+='\n\n'+label+'\n'+JSON.stringify(actual.action_diagnostics,null,2);
      }
      if(actual?.failure_ui_state||actual?.failure_ui_state_error){
        const observation=actual.failure_ui_state||{observation_error:actual.failure_ui_state_error};
        const label=zh?'失败后观测的页面状态（补充诊断，不替代上方原始错误）':'Page state observed after failure (supplementary diagnosis, not the original error above)';
        stepDialog.querySelector('[data-step-result]').textContent+='\n\n'+label+'\n'+JSON.stringify(observation,null,2);
      }
      stepDialog.querySelector('[data-step-status]').textContent=actual
        ?(zh?'实际状态：':'Actual status: ')+(actual.status||'—')+' · '+(actual.duration_ms==null?'—':(Number(actual.duration_ms)/1000).toFixed(2)+' s')
        :(zh?'实际状态：未执行':'Actual status: not executed');
      const finding=measured.programmatic_failure;
      if(finding&&(finding.step===index||finding.trigger_step===index)){
        stepDialog.querySelector('[data-step-finding]').hidden=false;
        stepDialog.querySelector('[data-step-error]').textContent=finding.message||'';
        if(finding.expected_network)stepDialog.querySelector('[data-step-error]').textContent+='\n\n'+JSON.stringify(finding.expected_network,null,2);
      }
      if(measured.screenshot){
        stepDialog.querySelector('[data-step-image-wrap]').hidden=false;
        stepDialog.querySelector('[data-step-image]').href=(zh?'../':'../../')+measured.screenshot;
      }
    }catch(error){
      if(current!==requestId||!stepDialog.open)return;
      stepDialog.querySelector('[data-step-status]').textContent=(zh?'逐步证据读取失败：':'Could not load step evidence: ')+error.message;
      evidencePromise=undefined;standardPromise=undefined;
    }
  });
  stepDialog.querySelector('[data-close-step]')?.addEventListener('click',()=>stepDialog.close());
  stepDialog.addEventListener('close',()=>{requestId++});
}
const viewer=document.querySelector('[data-evidence-viewer]');
if(viewer){
  const selected=new URLSearchParams(location.search).get('file');
  const safe=/^(?:\.\.\/)?data\/evidence\/[a-z0-9.-]+\.json$/.test(selected||'');
  const tabs=[...document.querySelectorAll('[data-evidence-tab]')];
  const heading=document.querySelector('[data-evidence-heading]');
  const raw=document.querySelector('[data-evidence-raw]');
  const image=document.querySelector('[data-evidence-image]');
  const render=(evidence,section)=>{
    const labels={result:zh?'结果与资源':'Result and resources',site_reset:zh?'站点重置':'Site reset',readiness:zh?'站点就绪':'Site readiness',programmatic_failure:zh?'程序定位':'Programmatic finding',review:zh?'复核原因':'Reviewed cause',trajectory:zh?'执行轨迹与完整日志':'Trajectory and full logs',official_evaluation:zh?'官方验收':'Official evaluation'};
    heading.textContent=labels[section]||section;
    viewer.textContent=JSON.stringify(evidence[section]??null,null,2);
    tabs.forEach(tab=>tab.setAttribute('aria-selected',String(tab.dataset.evidenceTab===section)));
  };
  if(!safe){viewer.textContent=zh?'没有选中证据文件。':'No evidence file selected.'}
  else fetchJson(selected).then(evidence=>{
    raw.href=selected;
    if(evidence.screenshot){image.href=base+evidence.screenshot;image.textContent=zh?'查看最后页面原图':'Open last observed page';image.hidden=false}
    tabs.forEach(tab=>tab.addEventListener('click',()=>render(evidence,tab.dataset.evidenceTab)));
    render(evidence,'result');
  }).catch(error=>viewer.textContent=(zh?'证据读取失败：':'Evidence loading failed: ')+error.message);
}