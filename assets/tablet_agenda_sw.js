const CACHE='Sistema Portfólio-tablet-agenda-v14';
const SHELL=['/agenda-tablet','/assets/tablet_agenda.css?v=1.2.1','/assets/tablet_agenda.js?v=1.3.4','/assets/tablet_agenda.webmanifest','/assets/institutional/Sistema Portfólio-globe.png'];
self.addEventListener('install',event=>{
  event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL)).then(()=>self.skipWaiting()));
});
self.addEventListener('activate',event=>{
  event.waitUntil(Promise.all([self.clients.claim(),caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key))))]));
});
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/'))return;
  if(url.pathname==='/agenda-tablet'){
    event.respondWith(fetch(event.request).then(response=>{
      if(response.ok && !response.redirected && new URL(response.url).pathname==='/agenda-tablet')
        caches.open(CACHE).then(cache=>cache.put('/agenda-tablet',response.clone()));
      return response;
    }).catch(()=>caches.match('/agenda-tablet')));
    return;
  }
  if(SHELL.some(path=>new URL(path,self.location.origin).pathname===url.pathname))event.respondWith(fetch(event.request).then(response=>{
    if(response.ok)caches.open(CACHE).then(cache=>cache.put(event.request,response.clone()));
    return response;
  }).catch(()=>caches.match(event.request)));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  event.waitUntil(clients.openWindow(event.notification.data?.url||'/agenda-tablet'));
});
