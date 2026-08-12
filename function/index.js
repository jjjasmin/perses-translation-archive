export async function onRequest(context) {
  const url = new URL(context.request.url);
  const videoId = url.searchParams.get('v');

  // 本来の index.html を取得
  const response = await context.env.ASSETS.fetch(context.request);

  // ?v= 動画ID が指定されていない場合はそのまま通常の index.html を返す
  if (!videoId) {
    return response;
  }

  // YouTubeのサムネイル画像URLを生成 (mqdefault または hqdefault)
  const thumbnailUrl = `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;
  
  // HTMLの書き換え処理 (HTMLRewriterを使用)
  return new HTMLRewriter()
    // og:image をYouTubeサムネイルに置換
    .on('meta[property="og:image"]', {
      element(element) {
        element.setAttribute('content', thumbnailUrl);
      }
    })
    // twitter:image をYouTubeサムネイルに置換
    .on('meta[name="twitter:image"]', {
      element(element) {
        element.setAttribute('content', thumbnailUrl);
      }
    })
    // twitter:card を summary_large_image に固定
    .on('meta[name="twitter:card"]', {
      element(element) {
        element.setAttribute('content', 'summary_large_image');
      }
    })
    .transform(response);
}