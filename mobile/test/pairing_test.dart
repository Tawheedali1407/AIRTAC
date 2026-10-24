// Unit tests that run in CI (flutter test) before the APK is built: the QR payload, the pin, the queue's reply text.
import 'dart:convert';

import 'package:airtac_mobile/src/api.dart';
import 'package:airtac_mobile/src/notify.dart';
import 'package:airtac_mobile/src/pairing_offer.dart';
import 'package:airtac_mobile/src/store.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';

String offer({Map<String, Object?> change = const {}}) {
  final m = <String, Object?>{
    'v': 1,
    'app': 'airtac',
    'hosts': ['192.168.1.20', '100.101.102.103'],
    'port': 8766,
    'fp': 'a' * 64,
    'code': 'abcdefghijkl',
    'exp': DateTime.now().add(const Duration(minutes: 2)).millisecondsSinceEpoch ~/ 1000,
    'google': false,
    'pc': 'ALI-PC',
  }..addAll(change);
  return jsonEncode(m);
}

void main() {
  test('a good QR code is read', () {
    final o = PairingOffer.parse(offer());
    expect(o.hosts, ['192.168.1.20', '100.101.102.103']);
    expect(o.port, 8766);
    expect(o.pcName, 'ALI-PC');
    expect(o.needsGoogle, isFalse);
  });

  test('the nonce matches the PC (sha256 of "airtac-pair:" + code)', () {
    final o = PairingOffer.parse(offer());
    expect(o.nonce, sha256.convert(utf8.encode('airtac-pair:abcdefghijkl')).toString());
  });

  test('other QR codes, expired codes and bad fingerprints are refused', () {
    expect(() => PairingOffer.parse('https://example.com'), throwsFormatException);
    expect(() => PairingOffer.parse(offer(change: {'app': 'other'})), throwsFormatException);
    expect(() => PairingOffer.parse(offer(change: {'fp': 'xyz'})), throwsFormatException);
    expect(() => PairingOffer.parse(offer(change: {'hosts': ['evil.example.com']})), throwsFormatException);
    expect(() => PairingOffer.parse(offer(change: {'exp': 1})), throwsFormatException);
  });

  test('the pin compares the SHA-256 of the certificate bytes', () {
    final der = utf8.encode('pretend certificate');
    final fp = sha256.convert(der).toString();
    expect(pinMatches(der, fp), isTrue);
    expect(pinMatches(der, '0' * 64), isFalse);
  });

  test('reply text joins spoken and screen without repeating', () {
    expect(replyText({'spoken': 'Hi.', 'screen': ''}), 'Hi.');
    expect(replyText({'spoken': 'Hi.', 'screen': 'Hi. More here.'}), 'Hi. More here.');
    expect(replyText({'spoken': 'Short.', 'screen': 'Long text'}), 'Short.\n\nLong text');
    expect(replyModel({'brain': {'model': 'gemini-3-flash'}}), 'gemini-3-flash');
  });

  test('schedule items with a time become reminder times', () {
    expect(eventTime({'date': '2026-11-12', 'time': '09:30'}), DateTime(2026, 11, 12, 9, 30));
    expect(eventTime({'date': '2026-11-12', 'time': ''}), isNull);
  });
}
