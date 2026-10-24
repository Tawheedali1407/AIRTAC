// Schedule: what's coming up (from the PC), add an item, and reminders on the phone.
import 'package:flutter/material.dart';

import '../api.dart';
import '../notify.dart';

// The same kinds as the PC's Schedule (plugins/schedule CATEGORIES).
const categories = ['personal', 'study', 'exam', 'contest', 'internship', 'project', 'reminder', 'birthday', 'holiday'];

class ScheduleScreen extends StatefulWidget {
  const ScheduleScreen({super.key});

  @override
  State<ScheduleScreen> createState() => _ScheduleScreenState();
}

class _ScheduleScreenState extends State<ScheduleScreen> {
  List<Map<String, dynamic>>? _events;
  String _note = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await Api.instance.get('/api/plugins/schedule') as Map<String, dynamic>;
      final today = DateTime.now().toIso8601String().substring(0, 10);
      final events = (r['events'] as List? ?? const [])
          .whereType<Map<String, dynamic>>()
          .where((e) => e['done'] != 1 && e['done'] != true && '${e['date']}'.compareTo(today) >= 0)
          .toList()
        ..sort((a, b) => '${a['date']} ${a['time']}'.compareTo('${b['date']} ${b['time']}'));
      var reminders = 0;
      try {
        reminders = await Notify.instance.replaceAll(events);
      } catch (_) {
        // notifications refused: the list still works
      }
      if (!mounted) return;
      setState(() {
        _events = events;
        _note = reminders > 0 ? '$reminders reminders set on this phone for the next 14 days.' : '';
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _note = e.message);
    }
  }

  Future<void> _add() async {
    final added = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => const _AddSheet(),
    );
    if (added == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final events = _events;
    return Scaffold(
      floatingActionButton: FloatingActionButton(onPressed: _add, tooltip: 'Add', child: const Icon(Icons.add)),
      body: events == null
          ? Center(child: Padding(padding: const EdgeInsets.all(24), child: _note.isEmpty ? const CircularProgressIndicator() : Text(_note)))
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(8, 8, 8, 88),
                children: [
                  if (_note.isNotEmpty) Padding(padding: const EdgeInsets.all(8), child: Text(_note, style: t.bodySmall)),
                  if (events.isEmpty) const Padding(padding: EdgeInsets.all(16), child: Text('Nothing coming up.')),
                  ...events.take(100).map((e) => ListTile(
                        leading: Text('${e['date']}'.substring(5), style: t.labelLarge),
                        title: Text('${e['title']}'),
                        subtitle: Text([
                          if ('${e['time'] ?? ''}'.isNotEmpty) '${e['time']}',
                          '${e['category'] ?? ''}',
                          if ('${e['repeat'] ?? ''}'.isNotEmpty) 'every ${e['repeat'] == 'yearly' ? 'year' : e['repeat'] == 'monthly' ? 'month' : 'week'}',
                        ].join(' · ')),
                      )),
                ],
              ),
            ),
    );
  }
}

class _AddSheet extends StatefulWidget {
  const _AddSheet();

  @override
  State<_AddSheet> createState() => _AddSheetState();
}

class _AddSheetState extends State<_AddSheet> {
  final _title = TextEditingController();
  DateTime _date = DateTime.now();
  TimeOfDay? _time;
  String _category = 'personal';
  String _repeat = '';
  String _error = '';
  bool _busy = false;

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  String _two(int n) => n.toString().padLeft(2, '0');

  Future<void> _save() async {
    if (_title.text.trim().isEmpty) return;
    setState(() => _busy = true);
    try {
      await Api.instance.post('/api/plugins/schedule', {
        'title': _title.text.trim(),
        'date': '${_date.year}-${_two(_date.month)}-${_two(_date.day)}',
        'time': _time == null ? '' : '${_two(_time!.hour)}:${_two(_time!.minute)}',
        'category': _category,
        'notes': 'Added from the phone',
        'repeat': _repeat,
      });
      if (mounted) Navigator.of(context).pop(true);
    } on ApiError catch (e) {
      setState(() {
        _error = e.message;
        _busy = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(16, 16, 16, 16 + MediaQuery.of(context).viewInsets.bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextField(controller: _title, autofocus: true, decoration: const InputDecoration(labelText: 'What')),
          const SizedBox(height: 8),
          Wrap(spacing: 8, children: [
            OutlinedButton(
              onPressed: () async {
                final d = await showDatePicker(
                    context: context, initialDate: _date, firstDate: DateTime(2020), lastDate: DateTime(2035));
                if (d != null && mounted) setState(() => _date = d);
              },
              child: Text('${_date.year}-${_two(_date.month)}-${_two(_date.day)}'),
            ),
            OutlinedButton(
              onPressed: () async {
                final t = await showTimePicker(context: context, initialTime: _time ?? TimeOfDay.now());
                if (mounted) setState(() => _time = t);
              },
              child: Text(_time == null ? 'No time' : _time!.format(context)),
            ),
          ]),
          DropdownButtonFormField<String>(
            initialValue: _category,
            decoration: const InputDecoration(labelText: 'Kind'),
            items: categories.map((c) => DropdownMenuItem(value: c, child: Text(c))).toList(),
            onChanged: (v) => setState(() => _category = v ?? 'personal'),
          ),
          DropdownButtonFormField<String>(
            initialValue: _repeat,
            decoration: const InputDecoration(labelText: 'Repeats'),
            items: const [
              DropdownMenuItem(value: '', child: Text('Once')),
              DropdownMenuItem(value: 'yearly', child: Text('Every year (birthdays)')),
              DropdownMenuItem(value: 'monthly', child: Text('Every month')),
              DropdownMenuItem(value: 'weekly', child: Text('Every week')),
            ],
            onChanged: (v) => setState(() => _repeat = v ?? ''),
          ),
          if (_error.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 8), child: Text(_error)),
          const SizedBox(height: 12),
          FilledButton(onPressed: _busy ? null : _save, child: const Text('Add to Schedule')),
        ],
      ),
    );
  }
}
