/* ===== GOOGLE ADSENSE SETUP =====
  1) After Google approves you, replace ca-pub-XXXXXXXXXXXXXXXX with your publisher ID (also edit ads.txt).
  2) Paste each ad unit's numeric slot ID into slots (feed = lists, article = inside stories).
  Ads stay off (no empty boxes) until a real ID is set. */
var ADSENSE_CONFIG = {
  client: 'ca-pub-XXXXXXXXXXXXXXXX',
  slots: { feed: '', article: '' }
};
(function () {
  var c = ADSENSE_CONFIG.client;
  if (!c || /X{6}/.test(c)) return;
  document.documentElement.classList.add('ads-on');
  var m = document.createElement('meta'); m.name = 'google-adsense-account'; m.content = c; document.head.appendChild(m);
  var sc = document.createElement('script'); sc.async = true; sc.crossOrigin = 'anonymous';
  sc.src = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=' + encodeURIComponent(c);
  document.head.appendChild(sc);
})();

/* ===== PRIVATE VISITOR STATISTICS (Google Analytics 4) =====
   Paste your Measurement ID (looks like G-ABC123XYZ) between the quotes. Leave empty to switch tracking off.
   Only you can see the numbers, by logging in at analytics.google.com. */
var ANALYTICS_ID = 'G-5JYDBZ2MQZ';
(function () {
  if (!ANALYTICS_ID || !/^G-[A-Z0-9]+$/.test(ANALYTICS_ID)) return;
  var s = document.createElement('script'); s.async = true;
  s.src = 'https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(ANALYTICS_ID);
  document.head.appendChild(s);
  window.dataLayer = window.dataLayer || [];
  window.gtag = function () { dataLayer.push(arguments); };
  gtag('js', new Date()); gtag('config', ANALYTICS_ID);
})();
