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
