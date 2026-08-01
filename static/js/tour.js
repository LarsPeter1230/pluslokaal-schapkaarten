/* PLUSLokaal rondleiding (onboarding). Stapsgewijze uitleg met spotlight + gedimd scherm,
   werkt over meerdere pagina's heen via sessionStorage. Stappen komen uit window.PLT_STEPS. */
(function(){
  var body=document.body;
  var EP=body.getAttribute('data-ep')||'';
  var TOUR=window.PLT_STEPS||[];
  var DONE_URL=window.PLT_DONE_URL||'/tour/done';
  var K_ACTIVE='plt_active', K_I='plt_i';
  function ss(k){try{return sessionStorage.getItem(k);}catch(e){return null;}}
  function sset(k,v){try{sessionStorage.setItem(k,v);}catch(e){}}
  function sdel(k){try{sessionStorage.removeItem(k);}catch(e){}}

  var overlay,ring,tip,tries=0,lifted=null,liftedPrev='';
  function dom(){
    if(overlay)return;
    overlay=document.createElement('div');overlay.className='plt-overlay';overlay.style.display='none';
    ring=document.createElement('div');ring.className='plt-ring';ring.style.display='none';
    tip=document.createElement('div');tip.className='plt-tip';tip.style.display='none';
    document.body.appendChild(overlay);document.body.appendChild(ring);document.body.appendChild(tip);
  }
  function lift(el){
    unlift();lifted=el;liftedPrev=el.getAttribute('style')||'';
    if(getComputedStyle(el).position==='static')el.style.position='relative';
    el.style.zIndex='100002';el.classList.add('plt-target');
  }
  function unlift(){if(lifted){lifted.setAttribute('style',liftedPrev);lifted.classList.remove('plt-target');lifted=null;}}

  function curI(){var i=parseInt(ss(K_I)||'0',10);return isNaN(i)?0:i;}
  function setI(i){sset(K_I,String(i));}
  function findForPage(from){for(var i=from;i<TOUR.length;i++){if(TOUR[i].ep===EP)return i;}return -1;}

  function place(r,p){
    tip.style.display='block';
    var tw=tip.offsetWidth,th=tip.offsetHeight,m=14,vw=innerWidth,vh=innerHeight,x,y;p=p||'bottom';
    if(p==='right'){x=r.right+m;y=r.top;}
    else if(p==='left'){x=r.left-tw-m;y=r.top;}
    else if(p==='top'){x=r.left;y=r.top-th-m;}
    else if(p==='center'){x=(vw-tw)/2;y=(vh-th)/2;}
    else{x=r.left;y=r.bottom+m;}
    x=Math.max(m,Math.min(x,vw-tw-m));y=Math.max(m,Math.min(y,vh-th-m));
    tip.style.left=x+'px';tip.style.top=y+'px';
  }
  function moveRing(r){ring.style.display='block';ring.style.left=(r.left-6)+'px';ring.style.top=(r.top-6)+'px';ring.style.width=(r.width+12)+'px';ring.style.height=(r.height+12)+'px';}

  function render(){
    dom();
    if(ss(K_ACTIVE)!=='1')return;
    var i=curI();
    if(!TOUR[i]||TOUR[i].ep!==EP){
      var j=findForPage(i); if(j<0)j=findForPage(0);
      if(j<0){teardown();return;}
      i=j;setI(i);
    }
    var step=TOUR[i];
    var el=step.sel?document.querySelector(step.sel):null;
    if(step.sel&&!el){ if(tries++<20){setTimeout(render,300);return;} }
    tries=0;
    overlay.style.display='block';
    buildTip(step,i);
    if(el){
      try{el.scrollIntoView({block:'center',behavior:'smooth'});}catch(e){}
      var r=el.getBoundingClientRect();
      lift(el);moveRing(r);place(r,step.place);
    }else{
      unlift();ring.style.display='none';
      place({left:0,top:0,right:innerWidth,bottom:innerHeight},'center');
    }
  }
  function buildTip(step,i){
    var total=TOUR.length;
    var lbl=step.nav?'Ga verder':(i>=total-1?'Afronden':'Volgende');
    tip.innerHTML=
      '<div class="plt-tip__bar"><span class="plt-tip__step">Stap '+(i+1)+' / '+total+'</span>'+
      '<button class="plt-tip__x" data-plt="close" aria-label="Rondleiding sluiten">&times;</button></div>'+
      (step.title?'<div class="plt-tip__title">'+step.title+'</div>':'')+
      '<div class="plt-tip__body">'+step.body+'</div>'+
      '<div class="plt-tip__btns">'+
        (i>0?'<button class="plt-btn plt-btn--ghost" data-plt="prev">Vorige</button>':'<span></span>')+
        '<button class="plt-btn plt-btn--primary" data-plt="next">'+lbl+'</button>'+
      '</div>';
  }
  function teardown(){unlift();if(overlay)overlay.style.display='none';if(ring)ring.style.display='none';if(tip)tip.style.display='none';}

  function start(){sset(K_ACTIVE,'1');setI(0);render();}
  function next(){
    var i=curI(),step=TOUR[i];
    if(step&&step.nav&&step.sel){var el=document.querySelector(step.sel);if(el){setI(i+1);el.click();return;}}
    var ni=i+1; if(ni>=TOUR.length){finish();return;}
    setI(ni);render();
  }
  function prev(){var i=curI();if(i<=0)return;setI(i-1);render();}
  function finish(){teardown();var e=document.getElementById('pltEnd');if(e)e.style.display='flex';}
  function stop(){ sdel(K_ACTIVE);sdel(K_I);teardown(); }

  document.addEventListener('click',function(e){
    var t=e.target.closest('[data-plt]');if(!t)return;
    var a=t.getAttribute('data-plt');
    if(a==='start'){hide('pltWelcome');start();}
    else if(a==='welcome-close'){hide('pltWelcome');}
    else if(a==='next'){next();}
    else if(a==='prev'){prev();}
    else if(a==='close'){stop();}
    else if(a==='end-close'){hide('pltEnd');stop();}
  });
  function hide(id){var w=document.getElementById(id);if(w)w.style.display='none';}
  function show(id){var w=document.getElementById(id);if(w)w.style.display='flex';}

  addEventListener('resize',function(){if(ss(K_ACTIVE)==='1')render();});
  addEventListener('scroll',function(){
    if(ss(K_ACTIVE)!=='1')return;
    var step=TOUR[curI()];var el=step&&step.sel?document.querySelector(step.sel):null;
    if(el){var r=el.getBoundingClientRect();moveRing(r);place(r,step.place);}
  },true);

  dom();
  if(ss(K_ACTIVE)==='1')render();
  else if(window.PLT_WELCOME===true)show('pltWelcome');   // server bepaalt dit: elke login opnieuw
})();
