// Today: the day's plan and the morning briefing, as the PC made them (plugins/daily). Listen reads them aloud.
import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api.dart';
import '../speech.dart';

class TodayScreen extends StatefulWidget {
  const TodayScreen({super.key});

  @override
  State<TodayScreen> createState() => _TodayScreenState();
}

class _TodayScreenState extends State<TodayScreen> {
  Map<String, dynamic>? _data;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await Api.instance.get('/api/plugins/daily') as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _data = d;
        _error = '';
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  List<Map<String, dynamic>> _items(String key) =>
      ((_data?['briefing'] as Map?)?[key] as List? ?? const []).whereType<Map<String, dynamic>>().toList();

  Future<void> _listen() async {
    if (Speech.instance.speaking) {
      await Speech.instance.stop();
      setState(() {});
      return;
    }
    final blocks = ((_data?['plan'] as Map?)?['blocks'] as List? ?? const []).whereType<Map>();
    final parts = <String>[
      if (blocks.isNotEmpty) 'Your day.',
      ...blocks.map((b) => '${b['start']}: ${b['title']}.'),
      if (_items('ai').isNotEmpty) 'In AI today.',
      ..._items('ai').take(5).map((x) => '${x['summary'] ?? x['title']}'),
    ];
    setState(() {});
    await Speech.instance.sayAll(parts);
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    if (_data == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: _error.isEmpty ? const CircularProgressIndicator() : Text(_error, textAlign: TextAlign.center),
        ),
      );
    }
    final plan = (_data!['plan'] as Map?) ?? const {};
    final blocks = (plan['blocks'] as List? ?? const []).whereType<Map>().toList();
    final briefing = (_data!['briefing'] as Map?) ?? const {};
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Row(
            children: [
              Expanded(child: Text('Your day', style: t.titleLarge)),
              FilledButton.tonalIcon(
                onPressed: _listen,
                icon: Icon(Speech.instance.speaking ? Icons.stop : Icons.volume_up),
                label: Text(Speech.instance.speaking ? 'Stop' : 'Listen'),
              ),
            ],
          ),
          if (plan['light'] == true) const Text('Light mode today (exam week or a busy day).'),
          if (blocks.isEmpty) const Padding(padding: EdgeInsets.all(8), child: Text('No plan for today yet.')),
          ...blocks.map((b) => ListTile(
                dense: true,
                leading: Text('${b['start']}\n${b['end']}', style: t.labelSmall),
                title: Text('${b['title']}'),
                subtitle: (b['note'] ?? '').toString().isEmpty ? null : Text('${b['note']}'),
              )),
          const Divider(height: 32),
          Text('Briefing', style: t.titleLarge),
          if ((briefing['note'] ?? '').toString().isNotEmpty)
            Padding(padding: const EdgeInsets.only(top: 4), child: Text('${briefing['note']}', style: t.bodySmall)),
          ..._items('ai').map((x) => _story(x, t)),
          if (_items('other').isNotEmpty) Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text('Other industries', style: t.titleMedium),
          ),
          ..._items('other').map((x) => _story(x, t)),
          if (_items('ai').isEmpty && _items('other').isEmpty)
            const Padding(padding: EdgeInsets.all(8), child: Text('No briefing yet today. It\'s made on the PC each morning.')),
        ],
      ),
    );
  }

  Widget _story(Map<String, dynamic> x, TextTheme t) => ListTile(
        contentPadding: EdgeInsets.zero,
        title: Text('${x['summary'] ?? x['title']}'),
        subtitle: Text([x['why'], x['source']].where((s) => s != null && '$s'.isNotEmpty).join(' · ')),
        onTap: () {
          final u = Uri.tryParse('${x['url'] ?? ''}');
          if (u != null && u.scheme == 'https') launchUrl(u, mode: LaunchMode.externalApplication);
        },
      );
}
