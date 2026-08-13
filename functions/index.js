export async function onRequest(context) {
  const url = new URL(context.request.url);
  // ?v= の値を取得
  const videoId = url.searchParams.get('v');

  // 本来の HTML レスポンスを取得
  const response = await context.env.ASSETS.fetch(context.request);

  // v パラメータがない場合はそのまま返す
  if (!videoId) {
    return response;
  }

  // YouTubeサムネイル画像のURL（hqdefault: 高画質）
  const thumbnailUrl = `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;

  // HTMLRewriter で meta タグを動的に書き換え
  return new HTMLRewriter()
    .on('meta[property="og:image"]', {
      element(element) {
        element.setAttribute('content', thumbnailUrl);
      }
    })
    .on('meta[name="twitter:image"]', {
      element(element) {
        element.setAttribute('content', thumbnailUrl);
      }
    })
    .transform(response);
}