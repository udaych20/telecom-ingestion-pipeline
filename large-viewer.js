const el=id=>document.getElementById(id);
const sourceLabels={chat_history:'Chat history',context_history:'Context history (Agent)',tool_history:'Tool history',feedback:'Feedback'};
let cidPage=0,rowPage=0,selected='',query='',source='',cidVersion=0,rowVersion=0;
async function api(path){const r=await fetch(path);if(!r.ok)throw Error(await r.text());return r.json()}
function guarded(fn){return async()=>{try{error.style.display='none';await fn()}catch(e){showError(e.message)}}}
async function conversations(){
 const version=++cidVersion;
 const data=await api('/api/cids?page='+cidPage+'&q='+encodeURIComponent(query));
 if(version!==cidVersion)return;
 el('cids').replaceChildren();el('cid-title').textContent='Conversations · page '+(cidPage+1);
 for(const row of data.rows){const b=document.createElement('button');b.className='interaction'+(row.cid===selected?' active':'');
 b.innerHTML='<strong>'+esc(row.cid)+'</strong><small>'+row.count+' source records</small>';
 b.onclick=guarded(async()=>{selected=row.cid;rowPage=0;
 for(const item of el('cids').children)item.classList.toggle('active',item===b);await records()});el('cids').append(b)}
 el('prevCid').disabled=cidPage===0;el('nextCid').disabled=!data.more;
 if(!data.rows.length)el('cids').textContent='No matching conversations';
 if(data.rows.length&&!selected){selected=data.rows[0].cid;el('cids').firstChild.classList.add('active');await records()}
}
async function records(){
 if(!selected)return;
 const version=++rowVersion;
 el('info').textContent='Loading records…';
 const data=await api('/api/records?cid='+encodeURIComponent(selected)+'&page='+rowPage+'&source='+encodeURIComponent(source));
 if(version!==rowVersion)return;
 el('selected').textContent=selected;
 el('stats').innerHTML=Object.entries(data.counts).map(([name,count])=>'<span class="pill">'+esc(sourceLabels[name]||name)+': '+count+'</span>').join('');
 el('info').textContent=data.rows.length?'Records '+(rowPage*20+1)+'–'+(rowPage*20+data.rows.length)+' · loaded page only':'No records for this source';
 el('prevRow').disabled=rowPage===0;el('nextRow').disabled=!data.more;
 el('records').replaceChildren();
 for(const name of [...new Set(data.rows.map(r=>r.source))]){
 const heading=document.createElement('h3');heading.textContent=sourceLabels[name]||name;el('records').append(heading);
 for(const row of data.rows.filter(r=>r.source===name)){
 const holder=document.createElement('div');
 if(row.preview){holder.innerHTML=renderRecord(row.preview);
 const card=holder.querySelector('.record');const intent=document.createElement('div');intent.className='meta';intent.style.padding='0 13px 10px';intent.textContent='Intent: '+(row.intent||'not supplied');card.append(intent);
 }else{holder.innerHTML='<article class="record"><div class="record-head"><strong>'+esc(row.record_id)+'</strong></div><div class="summary">'+renderFields([['Agent',row.agent],['Function',row.function_name],['Intent',row.intent]],'')+'<span class="hint">Large record: open in bounded chunks below to avoid exhausting browser memory.</span></div></article>'}
 const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Complete CSV row · chunked view';details.append(summary);
 const output=document.createElement('pre');details.append(output);const nav=document.createElement('div');details.append(nav);
 const back=document.createElement('button'),next=document.createElement('button');back.textContent='Previous chunk';next.textContent='Next chunk';nav.append(back,next);
 let offset=0,loaded=false;
 async function chunk(){const part=await api('/api/text?id='+row.id+'&offset='+offset);output.textContent=part.text;back.disabled=offset===0;next.disabled=!part.more;loaded=true}
 details.ontoggle=guarded(async()=>{if(details.open&&!loaded)await chunk()});
 back.onclick=guarded(async()=>{offset=Math.max(0,offset-32000);await chunk()});next.onclick=guarded(async()=>{offset+=32000;await chunk()});
 holder.querySelector('.record').append(details);el('records').append(holder);
 }}
}
async function overview(){const data=await api('/api/overview');
 const items=[['positive',data.positive,'Positive feedback documents'],['negative',data.negative,'Negative feedback documents'],['',data.documents,'Unique feedback documents'],['',data.documents?((data.positive/data.documents)*100).toFixed(1)+'%':'0.0%','Positive share of feedback documents']];
 el('feedback-overview').innerHTML='<div class="feedback-cards">'+items.map(([kind,n,label])=>'<div class="feedback-card '+kind+'"><strong>'+esc(n??'—')+'</strong><span>'+label+'</span></div>').join('')+'</div><p class="meta">Whole export · same feedback convention as the original viewer</p>';
}
el('find').onclick=guarded(async()=>{query=el('search').value;cidPage=0;selected='';rowPage=0;++rowVersion;el('records').replaceChildren();await conversations()});
el('search').onkeydown=e=>{if(e.key==='Enter')el('find').click()};
el('source').onchange=guarded(async()=>{source=el('source').value;rowPage=0;await records()});
el('prevCid').onclick=guarded(async()=>{cidPage=Math.max(0,cidPage-1);await conversations()});
el('nextCid').onclick=guarded(async()=>{cidPage++;await conversations()});
el('prevRow').onclick=guarded(async()=>{rowPage=Math.max(0,rowPage-1);await records()});
el('nextRow').onclick=guarded(async()=>{rowPage++;await records()});
guarded(async()=>{await Promise.all([conversations(),overview()])})();
