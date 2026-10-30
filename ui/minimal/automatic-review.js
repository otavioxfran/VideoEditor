import {element,button} from './api.js';

export function reviewForm(container,report,submit,listen,audition,watchJoin){
  container.replaceChildren();
  const form=element('form'), selectors=new Map();
  form.append(element('p','Avalie os cortes aplicados depois de assistir ao MP4 completo com áudio. Classes novas exigem cinco vídeos aprovados e exemplos reais de cortes corretos.'));
  for(const s of report.applied_cuts||[]){
    const label=element('label',`${s.reason} Antes: ${s.before||'—'} Depois: ${s.after||'—'}`), select=element('select');
    select.required=true;select.setAttribute('aria-label','Avaliação '+s.id);
    for(const [value,text] of [['','Escolha'],['correct','Corte correto'],['keep','Precisa manter'],['uncertain','Ainda tenho dúvida']]){const option=element('option',text);option.value=value;select.append(option);}
    label.append(select);form.append(label,button('Ouvir trecho',()=>listen(s.start,s.end)),button('Ouvir emenda sem salvar',()=>audition(s.id)));if(watchJoin&&Number.isFinite(s.join_output_seconds))form.append(button('Ver emenda no MP4',()=>watchJoin(s.join_output_seconds)));selectors.set(s.id,select);
  }
  const fields={};
  for(const [name,text,min,max] of [['naturalness','Naturalidade (1 a 5)',1,5],['severe_errors','Perdas de sentido ou palavras truncadas',0,null],['missed_errors','Erros que sobraram',0,null],['correction_minutes','Minutos gastos corrigindo',0,null]]){
    const label=element('label',text),input=element('input');input.type='number';input.min=min;if(max)input.max=max;input.step=name==='correction_minutes'?'0.1':'1';input.required=true;label.append(input);form.append(label);fields[name]=input;
  }
  const watched=element('input');watched.type='checkbox';watched.required=true;
  const watchedLabel=element('label','Assisti ao MP4 exportado completo com áudio ');watchedLabel.prepend(watched);form.append(watchedLabel);
  const approved=element('input');approved.type='checkbox';
  const approvedLabel=element('label','Aprovo esta edição ');approvedLabel.prepend(approved);form.append(approvedLabel);
  const send=element('button','Registrar minha avaliação');send.type='submit';form.append(send);
  form.onsubmit=async event=>{event.preventDefault();send.disabled=true;try{await submit({labels:Object.fromEntries([...selectors].map(([id,select])=>[id,select.value])),watched_complete:watched.checked,approved:approved.checked,...Object.fromEntries(Object.entries(fields).map(([name,input])=>[name,Number(input.value)]))});}finally{send.disabled=false;}};
  container.append(form);
}
