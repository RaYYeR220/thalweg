// Viewer request, /api/* (HTTP API origin). The API's routes are bare (/health, /runs, ...).
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.startsWith('/api/')) {
    request.uri = uri.substring(4);
  } else if (uri === '/api') {
    request.uri = '/';
  }
  return request;
}
