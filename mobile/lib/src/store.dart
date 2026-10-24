// What the phone keeps for itself: the chat as shown, and the offline queue (messages typed while the PC was away,
// sent in order when it answers again). Plain JSON in shared preferences; nothing secret goes here.
import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';

class ChatLine {
  ChatLine({required this.mine, required this.text, this.queued = false, this.model = '', DateTime? at})
      : at = at ?? DateTime.now();

  final bool mine;
  final String text;
  bool queued;
  final String model;
  final DateTime at;

  Map<String, Object> toJson() =>
      {'mine': mine, 'text': text, 'queued': queued, 'model': model, 'at': at.toIso8601String()};

  static ChatLine fromJson(Map<String, dynamic> j) => ChatLine(
        mine: j['mine'] == true,
        text: (j['text'] as String?) ?? '',
        queued: j['queued'] == true,
        model: (j['model'] as String?) ?? '',
        at: DateTime.tryParse((j['at'] as String?) ?? ''),
      );
}

class QueuedMessage {
  QueuedMessage(this.message, this.session, this.brain);

  final String message;
  final String session;
  final String brain;

  Map<String, String> toJson() => {'message': message, 'session': session, 'brain': brain};

  static QueuedMessage fromJson(Map<String, dynamic> j) =>
      QueuedMessage((j['message'] as String?) ?? '', (j['session'] as String?) ?? 'phone', (j['brain'] as String?) ?? '');
}

/// Keeps at most this many chat lines per conversation on the phone (the PC keeps the full history).
const maxLines = 200;

class LocalStore {
  LocalStore._();

  static final LocalStore instance = LocalStore._();

  Future<List<ChatLine>> lines(String session) async {
    final p = await SharedPreferences.getInstance();
    final raw = p.getString('chat:$session');
    if (raw == null) return [];
    return (jsonDecode(raw) as List).map((e) => ChatLine.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<void> saveLines(String session, List<ChatLine> lines) async {
    final p = await SharedPreferences.getInstance();
    final keep = lines.length > maxLines ? lines.sublist(lines.length - maxLines) : lines;
    await p.setString('chat:$session', jsonEncode(keep.map((l) => l.toJson()).toList()));
  }

  Future<List<QueuedMessage>> queue() async {
    final p = await SharedPreferences.getInstance();
    final raw = p.getString('queue');
    if (raw == null) return [];
    return (jsonDecode(raw) as List).map((e) => QueuedMessage.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<void> _saveQueue(List<QueuedMessage> q) async {
    final p = await SharedPreferences.getInstance();
    await p.setString('queue', jsonEncode(q.map((m) => m.toJson()).toList()));
  }

  Future<void> enqueue(QueuedMessage m) async => _saveQueue([...await queue(), m]);

  /// Sends what was queued, oldest first; stops at the first failure so the order is kept. Returns how many went.
  Future<int> flush(Future<Map<String, dynamic>> Function(QueuedMessage) send) async {
    var q = await queue();
    var sent = 0;
    while (q.isNotEmpty) {
      try {
        final reply = await send(q.first);
        final lines = await this.lines(q.first.session);
        for (final l in lines) {
          if (l.mine && l.queued && l.text == q.first.message) {
            l.queued = false;
            break;
          }
        }
        lines.add(ChatLine(mine: false, text: replyText(reply), model: replyModel(reply)));
        await saveLines(q.first.session, lines);
        q = q.sublist(1);
        await _saveQueue(q);
        sent++;
      } on ApiError catch (e) {
        if (e.offline) break; // still away: try again later
        q = q.sublist(1); // refused by the PC (e.g. too long): drop it, keep the rest
        await _saveQueue(q);
      }
    }
    return sent;
  }

  Future<bool> getFlag(String key, {bool fallback = false}) async =>
      (await SharedPreferences.getInstance()).getBool(key) ?? fallback;

  Future<void> setFlag(String key, bool value) async => (await SharedPreferences.getInstance()).setBool(key, value);
}

String replyText(Map<String, dynamic> r) {
  final spoken = (r['spoken'] as String?) ?? '';
  final screen = (r['screen'] as String?) ?? '';
  if (screen.isEmpty) return spoken;
  if (spoken.isEmpty || screen.contains(spoken)) return screen;
  return '$spoken\n\n$screen';
}

String replyModel(Map<String, dynamic> r) {
  final b = r['brain'];
  if (b is Map && b['model'] is String) return b['model'] as String;
  return '';
}
