// The one API client. Every call goes to the paired PC over HTTPS, and trusts ONLY the certificate whose SHA-256 came
// in the pairing QR code: the HttpClient has no trusted roots at all, so even a certificate signed by a public
// authority is refused unless it matches the pin.
import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class ApiError implements Exception {
  ApiError(this.status, this.message);

  /// HTTP status, or 0 = can't reach the PC, -1 = certificate doesn't match the pin, -2 = not paired.
  final int status;
  final String message;

  bool get offline => status == 0;

  @override
  String toString() => message;
}

class PcLink {
  PcLink({required this.hosts, required this.port, required this.fingerprint, required this.pcName});

  final List<String> hosts;
  final int port;
  final String fingerprint;
  final String pcName;

  Map<String, Object> toJson() => {'hosts': hosts, 'port': port, 'fp': fingerprint, 'pc': pcName};

  static PcLink fromJson(Map<String, dynamic> j) => PcLink(
        hosts: (j['hosts'] as List).cast<String>(),
        port: j['port'] as int,
        fingerprint: j['fp'] as String,
        pcName: (j['pc'] as String?) ?? 'your PC',
      );
}

bool pinMatches(List<int> der, String fingerprint) => sha256.convert(der).toString() == fingerprint;

class Api {
  Api._();

  static final Api instance = Api._();
  static const _store = FlutterSecureStorage();

  PcLink? link;
  String? _token;
  String? _goodHost; // the address that answered last time (Wi-Fi or Tailscale)

  bool get paired => link != null && _token != null;
  String? get host => _goodHost ?? link?.hosts.first;

  /// For the web view's first request only (it swaps the Bearer for cookies on /api/pair/web).
  String? get bearer => _token;

  Future<void> load() async {
    final raw = await _store.read(key: 'pc_link');
    final token = await _store.read(key: 'device_token');
    if (raw != null && token != null) {
      link = PcLink.fromJson(jsonDecode(raw) as Map<String, dynamic>);
      _token = token;
    }
  }

  Future<void> save(PcLink l, String token) async {
    await _store.write(key: 'pc_link', value: jsonEncode(l.toJson()));
    await _store.write(key: 'device_token', value: token);
    link = l;
    _token = token;
    _goodHost = null;
  }

  Future<void> forget() async {
    await _store.delete(key: 'pc_link');
    await _store.delete(key: 'device_token');
    link = null;
    _token = null;
  }

  /// Adds a Tailscale address (100.x.y.z) the PC also answers on, for when you're away from home.
  Future<void> addHost(String ip) async {
    final l = link;
    if (l == null || l.hosts.contains(ip)) return;
    await save(PcLink(hosts: [...l.hosts, ip], port: l.port, fingerprint: l.fingerprint, pcName: l.pcName), _token!);
  }

  HttpClient _client(String fingerprint) {
    // No trusted roots: every certificate goes to the callback, which accepts only the pinned one.
    final c = HttpClient(context: SecurityContext(withTrustedRoots: false))
      ..connectionTimeout = const Duration(seconds: 5)
      ..idleTimeout = const Duration(seconds: 15);
    c.badCertificateCallback = (X509Certificate cert, String h, int p) => pinMatches(cert.der, fingerprint);
    return c;
  }

  Future<dynamic> get(String path, {Duration timeout = const Duration(seconds: 30)}) =>
      call('GET', path, timeout: timeout);

  Future<dynamic> post(String path, Object body, {Duration timeout = const Duration(seconds: 120)}) =>
      call('POST', path, body: body, timeout: timeout);

  /// One call, trying the address that worked last first, then the others. Returns decoded JSON.
  Future<dynamic> call(String method, String path,
      {Object? body, Duration timeout = const Duration(seconds: 30), PcLink? via, bool auth = true}) async {
    final bytes = await _send(method, path, body: body, timeout: timeout, via: via, auth: auth);
    if (bytes.isEmpty) return null;
    return jsonDecode(utf8.decode(bytes));
  }

  Future<Uint8List> bytes(String path, {Duration timeout = const Duration(seconds: 60)}) =>
      _send('GET', path, timeout: timeout);

  Future<Uint8List> _send(String method, String path,
      {Object? body, Duration timeout = const Duration(seconds: 30), PcLink? via, bool auth = true}) async {
    final l = via ?? link;
    if (l == null) throw ApiError(-2, 'Not paired yet. Pair this phone with your PC first.');
    final first = via == null ? _goodHost : null;
    final hosts = [if (first != null) first, ...l.hosts.where((h) => h != first)];
    for (final h in hosts) {
      final client = _client(l.fingerprint);
      try {
        final req = await client.openUrl(method, Uri.parse('https://$h:${l.port}$path')).timeout(timeout);
        req.headers.set(HttpHeaders.acceptHeader, 'application/json');
        if (auth && _token != null) req.headers.set(HttpHeaders.authorizationHeader, 'Bearer $_token');
        if (body != null) {
          req.headers.contentType = ContentType.json;
          req.add(utf8.encode(jsonEncode(body)));
        }
        final res = await req.close().timeout(timeout);
        final collected = <int>[];
        await for (final chunk in res.timeout(timeout)) {
          collected.addAll(chunk);
        }
        final data = Uint8List.fromList(collected);
        if (via == null) _goodHost = h;
        if (res.statusCode >= 400) {
          String msg = 'Your PC answered with an error (${res.statusCode}).';
          try {
            final j = jsonDecode(utf8.decode(data));
            if (j is Map && j['detail'] is String) msg = j['detail'] as String;
          } catch (_) {}
          if (res.statusCode == 401 && auth) msg = 'This phone was removed on the PC. Pair it again.';
          throw ApiError(res.statusCode, msg);
        }
        return data;
      } on ApiError {
        rethrow;
      } on HandshakeException {
        throw ApiError(-1, "The PC's certificate doesn't match the one you paired with. "
            'If you reinstalled AIRTAC, pair again; otherwise this network may not be safe.');
      } on TlsException {
        throw ApiError(-1, "The PC's certificate doesn't match the one you paired with.");
      } on SocketException {
        continue; // try the next address
      } on TimeoutException {
        continue;
      } on HttpException {
        continue;
      } finally {
        client.close(force: true);
      }
    }
    throw ApiError(0, "Can't reach ${l.pcName}. Check that it's on, AIRTAC is running, phone access on Wi-Fi is on, "
        'and you are on the same Wi-Fi (or Tailscale is on).');
  }
}
