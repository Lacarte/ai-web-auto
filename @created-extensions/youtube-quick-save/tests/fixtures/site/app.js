// Fake YouTube single-page app: renders Home, a channel page and the watch page by URL, navigates with
// pushState + 'yt-navigate-finish' (like YouTube), and shows an inline preview that covers a hovered
// thumbnail after 600 ms (YouTube: ~2 s), carrying a link to the same video.
(() => {
  const VIDEOS = {
    home: ['homeVideo01', 'homeVideo02', 'homeVideo03', 'homeVideo04', 'homeVideo05', 'homeVideo06'],
    homeShorts: ['homeShort01', 'homeShort02', 'homeShort03'],
    channel: ['chanVideo01', 'chanVideo02', 'chanVideo03', 'chanVideo04'],
    channelShorts: ['chanShort01', 'chanShort02', 'chanShort03'],
    side: ['sideVideo01', 'sideVideo02', 'sideVideo03', 'sideVideo04', 'sideVideo05'],
  };

  const art = (id, w, h) => {
    const hue = [...id].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
    return 'data:image/svg+xml,' + encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><rect width="100%" height="100%" fill="hsl(${hue},55%,35%)"/>` +
      `<text x="50%" y="55%" fill="white" font-family="Arial" font-size="${Math.round(h / 8)}" text-anchor="middle">${id}</text></svg>`);
  };

  const tile = id => `
    <ytd-rich-item-renderer><yt-lockup-view-model data-testid="tile-${id}"><div class="lockup">
      <a class="thumb" href="/watch?v=${id}"><yt-thumbnail-view-model><img alt="" width="320" height="180" src="${art(id, 320, 180)}"></yt-thumbnail-view-model></a>
      <div class="meta"><h3><a href="/watch?v=${id}" title="Title of ${id}">Title of ${id}</a></h3><span>Some channel</span></div>
    </div></yt-lockup-view-model></ytd-rich-item-renderer>`;

  const short = id => `
    <ytm-shorts-lockup-view-model data-testid="tile-${id}">
      <a class="thumb short" href="/shorts/${id}"><img alt="" width="180" height="320" src="${art(id, 180, 320)}"></a>
      <h3><a href="/shorts/${id}">Short ${id}</a></h3>
    </ytm-shorts-lockup-view-model>`;

  const compact = id => `
    <yt-lockup-view-model class="compact" data-testid="tile-${id}">
      <a class="thumb" href="/watch?v=${id}"><yt-thumbnail-view-model><img alt="" width="168" height="94" src="${art(id, 168, 94)}"></yt-thumbnail-view-model></a>
      <div class="meta"><h3><a href="/watch?v=${id}" title="Title of ${id}">Title of ${id}</a></h3></div>
    </yt-lockup-view-model>`;

  function render() {
    const page = document.getElementById('page');
    const path = location.pathname;
    if (path === '/watch') {
      const v = new URLSearchParams(location.search).get('v') || 'mainVideo01';
      page.innerHTML = `
        <ytd-watch-flexy video-id="${v}">
          <div id="primary">
            <div id="movie_player"><video muted></video><div class="label">Playing ${v}</div></div>
            <h1>Watching ${v}</h1>
            <textarea id="comment" placeholder="Add a comment"></textarea>
          </div>
          <div id="secondary">${VIDEOS.side.map(compact).join('')}</div>
        </ytd-watch-flexy>`;
      document.title = `Watching ${v} - YouTube`;
    } else if (path.startsWith('/@')) {
      page.innerHTML = `<h2>Channel</h2><div class="grid">${VIDEOS.channel.map(tile).join('')}</div>
        <h2>Shorts</h2><div class="shorts">${VIDEOS.channelShorts.map(short).join('')}</div>`;
      document.title = 'Channel - YouTube';
    } else {
      page.innerHTML = `<div class="grid">${VIDEOS.home.map(tile).join('')}</div>
        <h2>Shorts</h2><div class="shorts">${VIDEOS.homeShorts.map(short).join('')}</div>`;
      document.title = 'YouTube';
    }
    hidePreview();
    document.dispatchEvent(new CustomEvent('yt-navigate-finish'));
  }

  // Single-page navigation, like YouTube.
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[href^="/"]');
    if (!a) return;
    e.preventDefault();
    history.pushState({}, '', a.getAttribute('href'));
    render();
  });
  window.addEventListener('popstate', render);

  // Inline preview over a hovered thumbnail.
  let timer = 0;
  function hidePreview() {
    clearTimeout(timer);
    const pv = document.querySelector('ytd-video-preview');
    if (pv) pv.hidden = true;
  }
  function showPreview(a) {
    let pv = document.querySelector('ytd-video-preview');
    if (!pv) {
      pv = document.createElement('ytd-video-preview');
      pv.innerHTML = '<div id="inline-preview-player"><video muted></video></div><a class="preview-link" href=""></a>';
      document.body.appendChild(pv);
      pv.addEventListener('mouseleave', hidePreview);
    }
    const r = a.getBoundingClientRect();
    Object.assign(pv.style, { left: `${r.left + scrollX}px`, top: `${r.top + scrollY}px`, width: `${r.width}px`, height: `${r.height}px` });
    pv.querySelector('a').setAttribute('href', a.getAttribute('href'));
    pv.hidden = false;
  }
  document.addEventListener('mouseover', e => {
    const a = e.target.closest && e.target.closest('a.thumb[href^="/watch"]');
    if (!a || e.target.closest('ytd-video-preview')) return;
    clearTimeout(timer);
    timer = setTimeout(() => showPreview(a), 600);
  });
  document.addEventListener('mouseout', e => {
    const a = e.target.closest && e.target.closest('a.thumb');
    if (a && !(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('ytd-video-preview'))) clearTimeout(timer);
  });

  render();
})();
