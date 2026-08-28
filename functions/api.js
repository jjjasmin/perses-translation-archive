export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const sheetName = url.searchParams.get('sheet');

  if (!sheetName) {
    return new Response(JSON.stringify({ error: 'Sheet name is required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' }
    });
  }

  // Cloudflare のシークレット（または Wrangler の .env）から API_KEY を取得
  const apiKey = env.GOOGLE_SHEETS_API_KEY;
  const spreadsheetId = '11JTSe6twASwnKU1rDbcead8Rm0raUtGGa33WeqS7nDk';
  const targetUrl = `https://sheets.googleapis.com/v4/spreadsheets/${spreadsheetId}/values/${encodeURIComponent(sheetName)}?key=${apiKey}`;

  try {
    const response = await fetch(targetUrl);
    const data = await response.json();

    return new Response(JSON.stringify(data), {
      headers: {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*'
      }
    });
  } catch (error) {
    return new Response(JSON.stringify({ error: 'Failed to fetch data' }), { status: 500 });
  }
}