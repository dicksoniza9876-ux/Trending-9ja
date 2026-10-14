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


  // ---- FEATURED POST (config.js > FEATURED) ----
  (function () {
    var F = window.FEATURED;
    if (!F || !F.live) return;
    var inNews = location.pathname.indexOf('/news/') > -1, base = inNews ? '../' : '';
    var href = base + (inNews ? '' : 'news/') + F.slug + '.html';
    var img = F.image || (F.youtubeId ? 'https://img.youtube.com/vi/' + F.youtubeId + '/hqdefault.jpg' : '');
    var fb = "this.onerror=null;this.src='data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22800%22 height=%22400%22><rect width=%22100%25%22 height=%22100%25%22 fill=%22%23008751%22/><text x=%2250%25%22 y=%2250%25%22 fill=%22white%22 font-family=%22sans-serif%22 font-size=%2236%22 text-anchor=%22middle%22>Trending9ja</text></svg>'";
    function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
    function card(cls) {
      return '<article class="post-card featured-card ' + cls + '"><a class="card-link" href="' + href + '" aria-label="' + esc(F.title) + '"></a>' +
        (img ? '<img src="' + esc(img) + '" alt="' + esc(F.title) + '" loading="lazy" referrerpolicy="no-referrer" onerror="' + fb + '">' : '') +
        '<div class="post-content"><span class="feat-badge"><i class="fas fa-star"></i> ' + esc(F.label) + '</span><span class="category-tag">' + esc(F.category) + '</span>' +
        '<h3>' + esc(F.title) + '</h3><p>' + esc(F.summary) + '</p><span class="read-more">Listen now →</span></div></article>';
    }
    // home: first card in Latest Buzz
    var grid = document.querySelector('.posts-main .posts-grid');
    if (grid && !inNews && document.getElementById('latest')) grid.insertAdjacentHTML('afterbegin', card('feat-home'));
    // story pages: right after the ESN advert in the sidebar
    var esn = document.querySelector('.side-col > .esn-side');
    if (esn && inNews && location.pathname.indexOf(F.slug) === -1) esn.insertAdjacentHTML('afterend', card('feat-side'));
    // featured story page body
    var body = document.getElementById('featuredBody');
    if (body) {
      document.title = F.title + ' | Trending9ja';
      document.getElementById('featTitle').textContent = F.title;
      document.getElementById('featMeta').textContent = 'By Trending9ja Staff • ' + new Date(F.date + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
      var h = '';
      if (img) h += '<img src="' + esc(img) + '" alt="' + esc(F.title) + '" referrerpolicy="no-referrer" onerror="' + fb + '">' + (F.imageCredit ? '<p class="credit">' + esc(F.imageCredit) + '</p>' : '');
      (F.paragraphs || []).forEach(function (p) { h += '<p>' + esc(p) + '</p>'; });
      if ((F.links || []).length) {
        h += '<div class="listen-box"><h3>Listen to ' + esc(F.title) + '</h3>';
        F.links.forEach(function (l) { if (l.url) h += '<a class="listen-btn" href="' + esc(l.url) + '" target="_blank" rel="noopener"><i class="fab ' + esc(l.icon || 'fa-music') + '"></i> ' + esc(l.name) + '</a>'; });
        h += '</div>';
      }
      if (F.creator && F.creator.instagram) h += '<a class="follow-btn" href="' + esc(F.creator.instagram) + '" target="_blank" rel="noopener"><i class="fab fa-instagram"></i> Follow ' + esc(F.creator.name) + ' on Instagram</a>';
      body.innerHTML = h;
    }
  })();

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

  // ---- Newsletter sign-up boxes (config.js > NEWSLETTER_CONFIG) ----
  (function () {
    var N = window.NEWSLETTER_CONFIG || {};
    if (!N.action) return;
    var pl = N.placements || {};
    function box(extra) {
      var d = document.createElement('div');
      d.className = 'nl-box' + (extra ? ' ' + extra : '');
      var h = document.createElement('h3'); h.textContent = N.title || 'Subscribe to our newsletter';
      var p = document.createElement('p'); p.textContent = N.text || '';
      var f = document.createElement('form'); f.method = 'POST'; f.action = N.action;
      var i = document.createElement('input');
      i.type = 'email'; i.name = N.emailField || 'email'; i.required = true;
      i.placeholder = 'Your email address'; i.setAttribute('aria-label', 'Email address');
      var b = document.createElement('button'); b.type = 'submit'; b.textContent = N.button || 'Subscribe';
      var m = document.createElement('div'); m.className = 'nl-msg'; m.setAttribute('role', 'status');
      f.appendChild(i); f.appendChild(b);
      Object.keys(N.hidden || {}).forEach(function (k) { var x = document.createElement('input'); x.type = 'hidden'; x.name = k; x.value = N.hidden[k]; f.appendChild(x); });
      f.addEventListener('submit', function (e) {
        e.preventDefault();
        b.disabled = true; m.textContent = 'Subscribing...';
        fetch(N.action, { method: 'POST', body: new URLSearchParams(new FormData(f)), mode: 'no-cors' })
          .then(function () { f.style.display = 'none'; m.textContent = 'Thank you! Check your inbox to confirm your subscription.'; })
          .catch(function () { b.disabled = false; m.textContent = 'Something went wrong. Please try again.'; });
      });
      d.appendChild(h); d.appendChild(p); d.appendChild(f); d.appendChild(m);
      return d;
    }
    if (pl.feed) [].forEach.call(document.querySelectorAll('.posts-grid'), function (g) {
      var cards = g.querySelectorAll(':scope > .post-card');
      if (cards.length > 3) g.insertBefore(box('nl-feed'), cards[2].nextSibling);
    });
    var post = document.querySelector('.full-post');
    if (post && pl.inPost) {
      var ps = [].filter.call(post.querySelectorAll('p'), function (p) { return !/\b(meta|sources|credit)\b/.test(p.className) && !p.closest('.prev-box,.listen-box,.share,.update-box'); });
      var at = ps[Math.min(1, ps.length - 1)];
      if (at) at.parentNode.insertBefore(box(), at.nextSibling);
    }
    var ft = document.querySelector('footer');
    if (ft && pl.footer) ft.parentNode.insertBefore(box('nl-footer'), ft);
  })();

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
