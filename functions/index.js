export async function onRequest(context) {
  const url = new URL(context.request.url);
  const videoId = url.searchParams.get('v');

  const response = await context.env.ASSETS.fetch(context.request);

  if (!videoId) {
    return response;
  }

  const thumbnailUrl = `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;

  return new HTMLRewriter()
    // og:image を書き換え
    .on('meta', {
      element(element) {
        const property = element.getAttribute('property');
        const name = element.getAttribute('name');

        if (property === 'og:image' || name === 'twitter:image') {
          element.setAttribute('content', thumbnailUrl);
        }
      }
    })
    .transform(response);
}