// What the PC's pairing QR code holds (see airtac/pairing.py start()). Pure Dart, so it's unit-tested.
import 'dart:convert';

import 'package:crypto/crypto.dart';

class PairingOffer {
  PairingOffer({
    required this.hosts,
    required this.port,
    required this.fingerprint,
    required this.code,
    required this.expires,
    required this.needsGoogle,
    required this.pcName,
  });

  final List<String> hosts;
  final int port;
  final String fingerprint; // SHA-256 of the PC certificate (DER), lowercase hex
  final String code;
  final DateTime expires;
  final bool needsGoogle;
  final String pcName;

  /// The Google sign-in nonce for this pairing (the PC checks the same value).
  String get nonce => sha256.convert(utf8.encode('airtac-pair:$code')).toString();

  bool get expired => DateTime.now().isAfter(expires);

  static final _hex64 = RegExp(r'^[0-9a-f]{64}$');
  static final _ipv4 = RegExp(r'^(\d{1,3}\.){3}\d{1,3}$');

  /// Reads a scanned QR code. Throws FormatException with a sentence for the person when it isn't AIRTAC's.
  static PairingOffer parse(String raw) {
    Object? data;
    try {
      data = jsonDecode(raw);
    } on FormatException {
      throw const FormatException("That isn't an AIRTAC pairing code.");
    }
    if (data is! Map || data['app'] != 'airtac' || data['v'] != 1) {
      throw const FormatException("That isn't an AIRTAC pairing code.");
    }
    final hosts = (data['hosts'] is List ? data['hosts'] as List : const [])
        .whereType<String>()
        .where(_ipv4.hasMatch)
        .toList();
    final port = data['port'];
    final fp = data['fp'];
    final code = data['code'];
    final exp = data['exp'];
    if (hosts.isEmpty) {
      throw const FormatException('The PC has no Wi-Fi address. Connect it to Wi-Fi and press Pair again.');
    }
    if (port is! int || port < 1 || port > 65535 || fp is! String || !_hex64.hasMatch(fp) ||
        code is! String || code.length < 8 || exp is! int) {
      throw const FormatException('The pairing code is incomplete. Press Pair on the PC again.');
    }
    final offer = PairingOffer(
      hosts: hosts,
      port: port,
      fingerprint: fp,
      code: code,
      expires: DateTime.fromMillisecondsSinceEpoch(exp * 1000),
      needsGoogle: data['google'] == true,
      pcName: data['pc'] is String ? data['pc'] as String : 'your PC',
    );
    if (offer.expired) {
      throw const FormatException('That code has expired. Press Pair on the PC again.');
    }
    return offer;
  }
}
