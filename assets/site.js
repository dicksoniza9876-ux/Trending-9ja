(function () {
  // ---- freshness: hide anything older than 7 days (in the visitor's browser) ----
  var MAXD = 7, now = new Date();
  function age(s) { var p = s.split('-'); return (Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) - Date.UTC(+p[0], +p[1] - 1, +p[2])) / 864e5; }
  document.querySelectorAll('.post-card[data-date]').forEach(function (c) { if (age(c.dataset.date) > MAXD) c.style.display = 'none'; });
  document.querySelectorAll('.posts-grid').forEach(function (g) {
    var vis = [].some.call(g.children, function (c) { return c.style.display !== 'none'; });
    var em = g.parentNode.querySelector('.empty'); if (em && !vis) em.hidden = false;
  });
  var art = document.querySelector('.article-layout[data-date]');
  if (art && age(art.dataset.date) > MAXD) document.querySelector('.stale-note').hidden = false;

  // ---- ESN popup (once per session) ----
  var pop = document.getElementById('esnPopup'), KEY = 'esnPopupSeen';
  if (pop) {
    var seen = function () { try { return sessionStorage.getItem(KEY) === '1'; } catch (e) { return false; } };
    var mark = function () { try { sessionStorage.setItem(KEY, '1'); } catch (e) {} };
    var close = function () { pop.classList.remove('open'); mark(); };
    setTimeout(function () { if (!seen() && !pop.classList.contains('open')) pop.classList.add('open'); }, 6000);
    document.getElementById('esnClose').addEventListener('click', close);
    document.getElementById('esnLater').addEventListener('click', close);
    document.getElementById('esnApply').addEventListener('click', mark);
    pop.addEventListener('click', function (e) { if (e.target === pop) close(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
  }

  // ---- cookie notice ----
  var bar = document.getElementById('cookieBar'), K = 'cookieOk', done = false;
  try { done = localStorage.getItem(K) === '1'; } catch (e) {}
  if (!done) bar.style.display = 'flex';
  document.getElementById('cookieOk').addEventListener('click', function () { bar.style.display = 'none'; try { localStorage.setItem(K, '1'); } catch (e) {} });

  // ---- share buttons ----
  var u = encodeURIComponent(location.href), t = encodeURIComponent(document.title);
  document.querySelectorAll('.share a').forEach(function (a) {
    var s = a.dataset.s;
    if (s === 'wa') { a.href = 'https://wa.me/?text=' + t + '%20' + u; a.target = '_blank'; a.rel = 'noopener'; }
    else if (s === 'x') { a.href = 'https://twitter.com/intent/tweet?text=' + t + '&url=' + u; a.target = '_blank'; a.rel = 'noopener'; }
    else if (s === 'fb') { a.href = 'https://www.facebook.com/sharer/sharer.php?u=' + u; a.target = '_blank'; a.rel = 'noopener'; }
    else a.addEventListener('click', function (ev) {
      ev.preventDefault();
      if (navigator.clipboard) navigator.clipboard.writeText(location.href).then(function () { a.textContent = 'Link copied'; }, function () {});
    });
  });

  // ---- AdSense slots (only when configured) ----
  var cfg = window.ADSENSE_CONFIG || {};
  if (!document.documentElement.classList.contains('ads-on')) return;
  document.querySelectorAll('.ad-slot').forEach(function (el) {
    var id = (cfg.slots || {})[el.dataset.slot];
    if (!id) { el.style.display = 'none'; return; }
    var ins = document.createElement('ins');
    ins.className = 'adsbygoogle'; ins.style.display = 'block';
    ins.setAttribute('data-ad-client', cfg.client); ins.setAttribute('data-ad-slot', id);
    ins.setAttribute('data-ad-format', 'auto'); ins.setAttribute('data-full-width-responsive', 'true');
    el.appendChild(ins);
    var pushed = false;
    function push() { if (pushed) return; pushed = true; try { (window.adsbygoogle = window.adsbygoogle || []).push({}); } catch (e) {} }
    if ('IntersectionObserver' in window) {
      var io = new IntersectionObserver(function (en) { en.forEach(function (x) { if (x.isIntersecting && el.offsetWidth > 0) { push(); io.disconnect(); } }); }, { rootMargin: '200px' });
      io.observe(el);
    } else push();
  });
})();
