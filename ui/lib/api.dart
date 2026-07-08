import 'dart:convert';

import 'package:http/http.dart' as http;

const apiBase = String.fromEnvironment('API_BASE', defaultValue: 'http://localhost:8000');

class ApiException implements Exception {
  ApiException(this.status, this.body);
  final int status;
  final String body;
  @override
  String toString() => 'API $status: $body';
}

/// Thin JSON client — every backend call goes through here.
class Api {
  static const _json = {'content-type': 'application/json'};

  static Uri _u(String path, [Map<String, String>? q]) =>
      Uri.parse('$apiBase$path').replace(queryParameters: q);

  static dynamic _decode(http.Response r) {
    if (r.statusCode >= 400) throw ApiException(r.statusCode, r.body);
    return jsonDecode(utf8.decode(r.bodyBytes));
  }

  static Future<dynamic> get(String path, [Map<String, String>? q]) async =>
      _decode(await http.get(_u(path, q)));

  static Future<dynamic> post(String path, [Object? body]) async =>
      _decode(await http.post(_u(path), headers: _json, body: jsonEncode(body ?? {})));

  static Future<dynamic> patch(String path, Object body) async =>
      _decode(await http.patch(_u(path), headers: _json, body: jsonEncode(body)));

  static Future<dynamic> delete(String path) async => _decode(await http.delete(_u(path)));
}
