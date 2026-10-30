import {api,element,button,time} from './api.js';

export function setupProductivity(container,base,getCurrent,onError){
  const heading=element('h3','Medir revisão e correção'),hint=element('p','Inicie antes de assistir e revisar. Pause nas interrupções: o cronômetro continua contando em outras abas e após recarregar. O tempo inclui revisão e correções; processamento automático não entra nessa conta.');
  const manual=element('input');manual.type='number';manual.min='0.1';manual.step='0.1';manual.placeholder='Opcional';manual.setAttribute('aria-label','Tempo manual para esta mesma gravação (minutos)');
  const manualLabel=element('label','Tempo que você gastaria limpando esta mesma gravação manualmente (minutos)');manualLabel.append(manual);
  const basis=element('select');basis.setAttribute('aria-label','Origem do tempo manual');
  for(const [value,text] of [['estimated','Minha estimativa'],['measured','Medi em uma edição manual desta gravação']]){const item=element('option',text);item.value=value;basis.append(item);}
  const summary=element('p'),controls=element('div',undefined,'toolbar'),output=element('div');
  container.append(heading,hint,manualLabel,basis,summary,controls,output);
  let state=null,pending=false,serverTime=0,loadedAt=0;
  async function command(action){
    if(pending)return;const current=getCurrent();if(!current)return;
    pending=true;draw();try{
      const data={action,expected_revision:current.edit.revision};
      if(action==='start'){if(manual.value!=='')data.manual_minutes=Number(manual.value);data.manual_basis=basis.value;}
      else{data.session_id=state.session.id;data.expected_session_revision=state.session.revision;}
      receive(await api(base+'/productivity','POST',data));
    }catch(error){onError(error.message);await refresh().catch(()=>{});}finally{pending=false;draw();}
  }
  function receive(value){state=value;serverTime=value.session?.elapsed_seconds||0;loadedAt=performance.now();draw();}
  function draw(){
    if(!state)return;const s=state.session;controls.replaceChildren();output.replaceChildren();
    manual.disabled=basis.disabled=!!s&&s.state!=='finished';
    if(!s||s.state==='finished')controls.append(button(s?'Iniciar outra medição':'Iniciar medição',()=>command('start')));
    else{controls.append(button(s.state==='running'?'Pausar cronômetro':'Continuar cronômetro',()=>command(s.state==='running'?'pause':'resume')),button('Concluir medição',()=>command('finish')));}
    for(const b of controls.querySelectorAll('button'))b.disabled=pending||!getCurrent();
    tick();
    if(!s){output.append(element('p','Economia ainda não medida. O cronômetro não aprova cortes nem naturalidade.'));return;}
    const c=s.comparison;
    output.append(element('p',`${c.intact_cuts} cortes iniciais continuam removidos · ${c.partially_restored} parcialmente restaurados · ${c.fully_restored} restaurados integralmente. Manter um corte não é uma aprovação de qualidade.`));
    if(s.state==='finished'){
      if(s.minutes_saved===null)output.append(element('p','Economia ainda não medida: faltou um tempo manual para comparação.'));
      else output.append(element('p',`${s.manual_basis==='estimated'?'Comparação com sua estimativa':'Comparação com seu tempo manual informado'}: ${Math.abs(s.minutes_saved).toFixed(1)} min ${s.minutes_saved>=0?'a menos':'a mais'} de trabalho, incluindo revisão e correções. A qualidade precisa ser avaliada separadamente.`));
    }
    if(s.policy_changed)output.append(element('p','O motor mudou depois do início. Esta medição pertence à versão anterior.'));
    const watch=element('a','Assistir à versão inicial desta medição');watch.href=base+`/exports/${s.render_id}/video.mp4`;watch.target='_blank';watch.rel='noopener';output.append(watch);
    output.append(element('p','Após restaurar ou ajustar cortes, exporte novamente para assistir à edição atual. A aprovação humana continua no formulário de avaliação.'));
  }
  function tick(){if(!state)return;const s=state.session;const seconds=serverTime+(s?.state==='running'?(performance.now()-loadedAt)/1000:0);summary.textContent=s?`${{running:'Contando',paused:'Pausado',finished:'Concluído'}[s.state]} · ${time(seconds)} de revisão e correção`:'Nenhuma medição iniciada.';}
  async function refresh(){if(!getCurrent())return;receive(await api(base+'/productivity'));}
  setInterval(tick,1000);
  return {refresh};
}
