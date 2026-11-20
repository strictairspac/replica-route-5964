import {api} from './client.js';
export const bookingRunsApi={list:()=>api('/booking_runs'),create:data=>api('/booking_runs','POST',data)};
export async function downloadBooking(id){const res=await fetch('/api/booking_runs/'+id+'/export',{headers:{Authorization:'Bearer '+localStorage.getItem('replicaroute-token')}});if(!res.ok)throw Error('导出失败，请重新登录后重试');const url=URL.createObjectURL(await res.blob());const a=document.createElement('a');a.href=url;a.download='booking-'+id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
