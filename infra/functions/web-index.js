// Viewer request, default behaviour (web bucket).
// The web app is a static export with trailing slashes: /stations/dart/ is stored as
// stations/dart/index.html. Paths ending in "/" or without a file extension get index.html.
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.endsWith('/')) {
    request.uri = uri + 'index.html';
  } else {
    var last = uri.substring(uri.lastIndexOf('/') + 1);
    if (last.indexOf('.') === -1) {
      request.uri = uri + '/index.html';
    }
  }
  return request;
}
