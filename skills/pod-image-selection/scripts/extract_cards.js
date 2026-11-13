() => {
  // Read-only DOM extraction. Run with the browser tool's documented evaluate API.
  const origin = document.location.origin;
  if (origin !== 'https://huaban.com') throw new Error('请在花瓣正常页面执行');
  const seen = new Set();
  const pins = [];
  for (const anchor of document.querySelectorAll('a[href*="/pins/"]')) {
    const image = anchor.querySelector('img');
    const id = anchor.getAttribute('href')?.match(/^\/pins\/(\d+)(?:[/?#]|$)/)?.[1];
    if (!image || !id || seen.has(id) || !anchor.getClientRects().length) continue;
    const imageUrl = image.currentSrc || image.getAttribute('src');
    if (!imageUrl || !image.complete || !image.naturalWidth) continue;
    seen.add(id);
    pins.push({
      pin_id: id,
      pin_url: `${origin}/pins/${id}`,
      title: image.getAttribute('alt') || image.getAttribute('title') || '',
      image_url: imageUrl,
      width: null,
      height: null,
      thumbnail_width: image.naturalWidth,
      thumbnail_height: image.naturalHeight,
      dimension_source: 'dom_thumbnail',
      decision: '待看图',
      source_paths: [{ via: 'browser_dom', page_url: document.location.href }]
    });
  }
  return { collected_at: new Date().toISOString(), pins };
}
