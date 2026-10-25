// Viewer request, /artifacts/* (artifacts bucket).
// Strips the /artifacts prefix and serves only the public prefixes: run evidence and traces,
// station assets and published results. Everything else in the bucket (raw data) stays private.
var ALLOWED = ['runs/', 'stations/', 'public/'];

function handler(event) {
  var request = event.request;
  var key = request.uri.replace(/^\/artifacts\//, '');
  var allowed = key.indexOf('..') === -1 && ALLOWED.some(function (prefix) {
    return key.startsWith(prefix) && key.length > prefix.length;
  });
  if (!allowed) {
    return { statusCode: 404, statusDescription: 'Not Found' };
  }
  request.uri = '/' + key;
  return request;
}
