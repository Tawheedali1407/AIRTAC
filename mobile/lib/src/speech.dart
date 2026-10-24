// Android's own text-to-speech (no audio leaves the phone). Used by Today and the lecture player.
import 'package:flutter_tts/flutter_tts.dart';

class Speech {
  Speech._();

  static final Speech instance = Speech._();
  final FlutterTts _tts = FlutterTts();
  bool _ready = false;
  bool speaking = false;

  Future<void> _init() async {
    if (_ready) return;
    await _tts.setLanguage('en-IN');
    await _tts.setSpeechRate(0.5);
    await _tts.awaitSpeakCompletion(true);
    _ready = true;
  }

  /// Speaks the parts one after another; stop() ends it.
  Future<void> sayAll(List<String> parts) async {
    await _init();
    speaking = true;
    for (final p in parts) {
      if (!speaking) break;
      if (p.trim().isEmpty) continue;
      await _tts.speak(p);
    }
    speaking = false;
  }

  Future<void> stop() async {
    speaking = false;
    await _tts.stop();
  }
}
