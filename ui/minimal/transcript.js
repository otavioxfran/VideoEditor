import {element,button,time} from './api.js';
export function renderTranscript(container,segments,timeline,query,review,edit){
  container.replaceChildren();
  for(const s of segments){if(query&&!s.text.toLocaleLowerCase().includes(query.toLocaleLowerCase()))continue;
    const removed=timeline.cuts.some(([a,b])=>a<=s.start&&b>=s.end);const row=element('article',undefined,'segment'+(removed?' removed':''));row.dataset.segmentId=s.id;
    row.append(element('small',`${s.id} · ${time(s.start)} → ${time(s.end)}${removed?' · removido':''}`),element('p',s.text));const controls=element('div',undefined,'toolbar');controls.append(button('Ouvir no raw',()=>review(s.start,s.end)),button('Cortar',()=>edit('cut',s.start,s.end)),button('Restaurar',()=>edit('restore',s.start,s.end)));row.append(controls);container.append(row);
  }
}
