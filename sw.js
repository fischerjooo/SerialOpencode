/* The app shell is cached locally. Chat stays on Bluetooth. */
const CACHE_NAME = 'serialopencode-pwa-v1';
const ASSETS = ['./index.html','./manifest.webmanifest','./icons/icon-192.png','./icons/icon.svg','./icons/icon-maskable.svg'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener('fetch', event => {
  if(event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if(url.origin !== self.location.origin || !url.pathname.startsWith(new URL('./', self.location.href).pathname)) return;
  if(event.request.mode === 'navigate'){
    event.respondWith(fetch(event.request).then(response => {
      if(response.ok){const copy=response.clone();caches.open(CACHE_NAME).then(cache=>cache.put(event.request,copy)).catch(()=>{});}
      return response;
    }).catch(()=>caches.match('./index.html')));
  }else{
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
  }
});
