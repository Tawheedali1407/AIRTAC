// Reminders on the phone for timed Schedule items of the next 14 days. The phone keeps them even when the PC is off;
// each refresh of the Schedule screen replaces them with the PC's current list.
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/data/latest.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;

class Notify {
  Notify._();

  static final Notify instance = Notify._();
  final FlutterLocalNotificationsPlugin _plugin = FlutterLocalNotificationsPlugin();
  bool _ready = false;

  static const _details = NotificationDetails(
    android: AndroidNotificationDetails('schedule', 'Schedule reminders',
        channelDescription: 'Your AIRTAC Schedule items', importance: Importance.high, priority: Priority.high),
  );

  Future<void> init() async {
    if (_ready) return;
    tzdata.initializeTimeZones();
    await _plugin.initialize(settings: const InitializationSettings(android: AndroidInitializationSettings('@mipmap/ic_launcher')));
    await _plugin
        .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission();
    _ready = true;
  }

  /// Replaces every reminder with these items: {id, title, date "YYYY-MM-DD", time "HH:MM"}. Returns how many are set.
  Future<int> replaceAll(List<Map<String, dynamic>> events) async {
    await init();
    await _plugin.cancelAll();
    final now = DateTime.now();
    var n = 0;
    for (final e in events) {
      final when = eventTime(e);
      if (when == null || when.isBefore(now) || when.difference(now).inDays > 14) continue;
      await _plugin.zonedSchedule(
        id: (e['id'] as int? ?? n) % 100000,
        title: '${e['title']}',
        body: 'At ${e['time']} (${e['category'] ?? 'schedule'})',
        scheduledDate: tz.TZDateTime.from(when, tz.UTC),
        notificationDetails: _details,
        androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
      );
      n++;
    }
    return n;
  }
}

/// The local time of a Schedule item, or null when it has no time of day.
DateTime? eventTime(Map<String, dynamic> e) {
  final d = DateTime.tryParse('${e['date'] ?? ''}');
  final t = RegExp(r'^(\d{1,2}):(\d{2})$').firstMatch('${e['time'] ?? ''}');
  if (d == null || t == null) return null;
  return DateTime(d.year, d.month, d.day, int.parse(t.group(1)!), int.parse(t.group(2)!));
}
