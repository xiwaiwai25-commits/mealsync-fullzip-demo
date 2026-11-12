const BASE=import.meta.env.VITE_API_URL||'http://127.0.0.1:8000/api';
export const API_ORIGIN=BASE.replace(/\/api\/?$/,'');
export function token(){return localStorage.getItem('ms_token')}
export async function request(path,options={}){const headers={'Content-Type':'application/json',...(options.headers||{})};if(token())headers.Authorization=`Bearer ${token()}`;const res=await fetch(`${BASE}${path}`,{...options,headers});if(!res.ok){let msg='Something went wrong';try{msg=(await res.json()).detail||msg}catch{}throw new Error(msg)}const type=res.headers.get('content-type')||'';return type.includes('application/json')?res.json():res.blob()}
export function money(sen=0){return `RM ${(Number(sen||0)/100).toFixed(2)}`}
export function imageForFood(food){if(food?.image_url)return food.image_url;const photos=['photo-1568901346375-23c9450c58cd','photo-1512058564366-18510be2db19','photo-1569718212165-3a8278d5f624','photo-1546069901-ba9599a7e63c','photo-1532550907401-a500c9a57435','photo-1547592180-85f173990554','photo-1513104890138-7c749659a591','photo-1551024506-0bccd828d307','photo-1544145945-f90425340c7e','photo-1579871494447-9811cf80d66c'];let hash=0;for(const ch of String(food?.id||food?.name||'meal'))hash=(hash*31+ch.charCodeAt(0))>>>0;return `https://images.unsplash.com/${photos[hash%photos.length]}?fm=png&fit=crop&w=900&q=85`}
export function imageForShop(shop){const map={'SHOP001':'photo-1512058564366-18510be2db19','SHOP002':'photo-1568901346375-23c9450c58cd','SHOP003':'photo-1544145945-f90425340c7e','SHOP004':'photo-1579871494447-9811cf80d66c','SHOP005':'photo-1565557623262-b51c2513a641','SHOP006':'photo-1529006557810-274b9b2fc783','SHOP007':'photo-1569718212165-3a8278d5f624','SHOP008':'photo-1547592180-85f173990554'};return `https://images.unsplash.com/${map[shop?.id]||'photo-1546069901-ba9599a7e63c'}?fm=png&fit=crop&w=800&q=85`}

export async function uploadFile(path,file){
 const headers={};if(token())headers.Authorization=`Bearer ${token()}`;
 const form=new FormData();form.append('file',file);
 const res=await fetch(`${BASE}${path}`,{method:'POST',headers,body:form});
 if(!res.ok){let msg='Image upload failed';try{msg=(await res.json()).detail||msg}catch{}throw new Error(msg)}
 return res.json();
}
