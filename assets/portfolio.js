/* Adaptador local da demonstração. Nenhum endpoint de produção é utilizado. */
(() => {
  const nativeFetch = window.fetch.bind(window);
  const key = 'portfolio_session_v1';
  let revision = 1;
  const response = (body, status=200) => Promise.resolve(new Response(JSON.stringify(body), {status, headers:{'Content-Type':'application/json'}}));
  window.fetch = async (input, options={}) => {
    const url = new URL(typeof input==='string' ? input : input.url, location.href);
    const method = String(options.method || input.method || 'GET').toUpperCase();
    if(url.origin !== location.origin) return response({detail:'Consultas externas desativadas no portfólio.'},403);
    const path=url.pathname;
    if(!path.startsWith('/api/')) return nativeFetch(input,options);
    if(path==='/api/system-state') {
      if(method==='PUT') {
        const payload=JSON.parse(options.body||'{}');
        sessionStorage.setItem(key,JSON.stringify(payload.data||{}));
        return response({ok:true,revision:++revision,updated_at:new Date().toISOString()});
      }
      const saved=sessionStorage.getItem(key);
      if(saved) return response({has_state:true,data:JSON.parse(saved),revision});
      return nativeFetch('/api/system-state');
    }
    if(path==='/api/employee-portal/admin/state') return response({employees:[],files:[],messages:[],entries:[]});
    if(path==='/api/employee-portal/admin/sync') return response({ok:true,employees:[],files:[],messages:[],entries:[]});
    if(path==='/api/auth/logout') { sessionStorage.clear(); return response({ok:true}); }
    if(path==='/api/health' || path==='/api/auth/me') return nativeFetch(input,options);
    if(method==='GET' && ['/api/internal-demands','/api/admin/profiles'].includes(path)) return response([]);
    return response({detail:'Recurso desativado no portfólio. Explore os módulos com os dados fictícios disponíveis.'},403);
  };
  document.addEventListener('click', event => {
    const anchor=event.target.closest('a');
    if(anchor && (anchor.origin !== location.origin || anchor.getAttribute('href')==='/app')) {
      event.preventDefault(); alert('Links externos e app de funcionários desativados nesta demonstração.');
    }
  },true);
  function disableSensitiveInputs(){
    document.querySelectorAll('input[type=password],input[type=file]').forEach(input=>{
      input.disabled=true; input.value=''; input.placeholder='Indisponível no portfólio';
      input.title='Use somente dados fictícios. Senhas e arquivos não são utilizados nesta versão.';
    });
  }
  document.addEventListener('DOMContentLoaded',()=>{
    disableSensitiveInputs();
    new MutationObserver(disableSensitiveInputs).observe(document.body,{childList:true,subtree:true});
    // Logout would only reload the password-free demonstration.
    window.logoutSystemV28=()=>location.assign('/workspace');
  });
})();
