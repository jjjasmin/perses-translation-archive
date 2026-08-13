export async function onRequest(context) {
  const url = new URL(context.request.url);
  const videoId = url.searchParams.get('v');

  // 本来の HTML を取得
  const response = await context.env.ASSETS.fetch(context.request);

  // ?v= がない場合はそのまま返す
  if (!videoId) {

    return response;
  }

  // YouTubeサムネイル画像のURL
  const thumbnailUrl = `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;

  // HTMLRewriterでmetaタグを書き換えて返す
  return new HTMLRewriter()
    .on('meta[property="og:image"]', (element) => {
      element.setAttribute('content', thumbnailUrl);
    })
    .on('meta[name="twitter:image"]', (element) => {
      element.setAttribute('content', thumbnailUrl);
    })
    .transform(response);
}