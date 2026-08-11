/* labdocs report theme toggle — lifted verbatim from
   codebase_template/2026-08-11_codebase_template_reference_v3.html <script>.
   Inlined into explain_codebase.html at render time; the CSS carries a
   prefers-color-scheme fallback so a no-JS open is still readable. */
(function(){
  var r=document.documentElement, b=document.getElementById('tg');
  function set(t){ r.setAttribute('data-theme',t); b.textContent = t==='light' ? '◐ Dark theme' : '◑ Light theme'; }
  b.addEventListener('click', function(){ set(r.getAttribute('data-theme')==='light'?'dark':'light'); });
  if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) set('dark');
})();
