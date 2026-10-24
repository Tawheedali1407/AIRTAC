// Pairing: scan the QR code from "Get mobile app" on the PC (or paste its text), sign in with Google if the PC asks,
// then swap the one-time code for this phone's device token.
import 'package:flutter/material.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../api.dart';
import '../pairing_offer.dart';

/// The Google client ID the phone asks its ID token for (a "Web application" client in the same Google Cloud
/// project as the PC's; see docs/MOBILE.md). Built in with --dart-define=GOOGLE_SERVER_CLIENT_ID=...
const googleServerClientId = String.fromEnvironment('GOOGLE_SERVER_CLIENT_ID');

bool _googleReady = false;

class PairScreen extends StatefulWidget {
  const PairScreen({super.key, required this.onPaired});

  final VoidCallback onPaired;

  @override
  State<PairScreen> createState() => _PairScreenState();
}

class _PairScreenState extends State<PairScreen> {
  final _name = TextEditingController(text: 'My phone');
  final _paste = TextEditingController();
  bool _scanning = false;
  bool _busy = false;
  String _message = '';

  @override
  void dispose() {
    _name.dispose();
    _paste.dispose();
    super.dispose();
  }

  Future<void> _use(String raw) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _scanning = false;
      _message = 'Pairing...';
    });
    try {
      final offer = PairingOffer.parse(raw.trim());
      var idToken = '';
      if (offer.needsGoogle) {
        idToken = await _googleIdToken(offer);
      }
      final link = PcLink(hosts: offer.hosts, port: offer.port, fingerprint: offer.fingerprint, pcName: offer.pcName);
      final r = await Api.instance.call('POST', '/api/pair/finish',
          body: {'code': offer.code, 'name': _name.text.trim().isEmpty ? 'My phone' : _name.text.trim(),
              'google_id_token': idToken},
          via: link,
          auth: false,
          timeout: const Duration(seconds: 30)) as Map<String, dynamic>;
      await Api.instance.save(link, r['token'] as String);
      if (!mounted) return;
      setState(() => _message = 'Paired with ${offer.pcName}.');
      widget.onPaired();
    } on FormatException catch (e) {
      setState(() => _message = e.message);
    } on ApiError catch (e) {
      setState(() => _message = e.message);
    } catch (e) {
      setState(() => _message = 'Pairing failed: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<String> _googleIdToken(PairingOffer offer) async {
    if (googleServerClientId.isEmpty) {
      throw ApiError(403, 'Your PC is signed in to Google, but this app was built without a Google client ID. '
          'Rebuild it with GOOGLE_SERVER_CLIENT_ID (docs/MOBILE.md), or sign the PC out of Google to pair without it.');
    }
    final g = GoogleSignIn.instance;
    // google_sign_in may be initialized only once per app run, and the nonce belongs to one pairing code.
    if (_googleReady) {
      throw ApiError(403, 'Close and reopen the app, then scan a new code (Google sign-in can start once per run).');
    }
    _googleReady = true;
    await g.initialize(serverClientId: googleServerClientId, nonce: offer.nonce);
    final account = await g.authenticate();
    final token = account.authentication.idToken;
    if (token == null) throw ApiError(403, "Google didn't give a sign-in token. Try again.");
    return token;
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Pair with your PC')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text('On the PC: open AIRTAC, press "Get mobile app" in the chat header, then Pair a phone.',
                style: t.bodyLarge),
            const SizedBox(height: 12),
            TextField(controller: _name, decoration: const InputDecoration(labelText: 'Name for this phone')),
            const SizedBox(height: 16),
            if (_scanning)
              SizedBox(
                height: 320,
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(12),
                  child: MobileScanner(
                    onDetect: (capture) {
                      for (final b in capture.barcodes) {
                        final v = b.rawValue;
                        if (v != null && v.contains('"airtac"')) {
                          _use(v);
                          return;
                        }
                      }
                    },
                  ),
                ),
              )
            else
              FilledButton.icon(
                onPressed: _busy ? null : () => setState(() => _scanning = true),
                icon: const Icon(Icons.qr_code_scanner),
                label: const Text('Scan the QR code'),
              ),
            const SizedBox(height: 24),
            ExpansionTile(
              title: const Text("Can't scan? Paste the code text"),
              children: [
                TextField(
                  controller: _paste,
                  minLines: 2,
                  maxLines: 5,
                  decoration: const InputDecoration(hintText: '{"v":1,"app":"airtac",...}'),
                ),
                const SizedBox(height: 8),
                OutlinedButton(onPressed: _busy ? null : () => _use(_paste.text), child: const Text('Pair')),
              ],
            ),
            const SizedBox(height: 16),
            if (_message.isNotEmpty) Text(_message, style: t.bodyMedium),
            const SizedBox(height: 16),
            Text('The phone remembers only your PC\'s certificate fingerprint and its own device token (in Android '
                'Keystore). Your API keys and passwords never leave the PC.', style: t.bodySmall),
          ],
        ),
      ),
    );
  }
}
