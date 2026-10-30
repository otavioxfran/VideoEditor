export async function api(path, method='GET', data) {
  const options={method, headers:{'Content-Type':'application/json'}};
  if(data!==undefined) options.body=JSON.stringify(data);
  const response=await fetch(path,options);
  if(!response.ok){const body=await response.json().catch(()=>({}));const error=new Error(typeof body.detail==='string'?body.detail:`Requisição falhou (${response.status})`);error.status=response.status;throw error;}
  return response.headers.get('content-type')?.includes('json')?response.json():response.text();
}
export function element(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
export function button(text,callback){const node=element('button',text);node.type='button';node.addEventListener('click',()=>Promise.resolve(callback()).catch(e=>document.getElementById('message').textContent=e.message));return node;}
export function time(seconds){return new Date(Math.max(0,seconds)*1000).toISOString().slice(11,19);}
