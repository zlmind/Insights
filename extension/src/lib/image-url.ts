// The card's image_url is recorded with whatever host the browser used when
// the image was uploaded (localhost vs 127.0.0.1). The service worker holds
// host permission for the paired serverUrl origin only, so a loopback image
// host is rewritten to the serverUrl host before fetching — otherwise the
// SW's fetch fails and the card's real picture is silently replaced by the
// bundled default cover.

export function sameServerImageUrl(imageUrl: string, serverUrl: string): string {
  try {
    const img = new URL(imageUrl);
    const srv = new URL(serverUrl);
    if (isLoopback(img.hostname) && isLoopback(srv.hostname)) {
      img.hostname = srv.hostname;
    }
    return img.toString();
  } catch {
    return imageUrl;
  }
}

function isLoopback(host: string): boolean {
  return host === "localhost" || host === "127.0.0.1";
}
