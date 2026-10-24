// Learn: this week of the roadmap, progress, and the lectures made on the PC (summary, script, quiz) played with
// Android's text-to-speech.
import 'package:flutter/material.dart';

import '../api.dart';
import '../speech.dart';

class LearnScreen extends StatefulWidget {
  const LearnScreen({super.key});

  @override
  State<LearnScreen> createState() => _LearnScreenState();
}

class _LearnScreenState extends State<LearnScreen> {
  Map<String, dynamic>? _plan;
  List<Map<String, dynamic>> _lectures = [];
  String _error = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final p = await Api.instance.get('/api/plugins/learn') as Map<String, dynamic>;
      final l = await Api.instance.get('/api/plugins/lectures') as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _plan = p;
        _lectures = (l['items'] as List? ?? const []).whereType<Map<String, dynamic>>().toList();
        _error = '';
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    if (_plan == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: _error.isEmpty ? const CircularProgressIndicator() : Text(_error, textAlign: TextAlign.center),
        ),
      );
    }
    final plan = (_plan!['plan'] as Map?) ?? const {};
    final done = (_plan!['done'] as List? ?? const []).length;
    final weeks = ((_plan!['starts'] as Map?) ?? const {}).length;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('${plan['name'] ?? 'Learning plan'}', style: t.titleLarge),
          const SizedBox(height: 8),
          if (weeks > 0) ...[
            LinearProgressIndicator(value: weeks == 0 ? 0 : (done / weeks).clamp(0, 1).toDouble()),
            Padding(padding: const EdgeInsets.only(top: 4), child: Text('$done of $weeks weeks done', style: t.bodySmall)),
          ],
          const SizedBox(height: 12),
          Text('This week', style: t.titleMedium),
          Text('${_plan!['this_week'] ?? 'Nothing planned this week.'}'),
          const Divider(height: 32),
          Text('Lectures', style: t.titleLarge),
          if (_lectures.isEmpty)
            const Padding(
              padding: EdgeInsets.all(8),
              child: Text('No lectures yet. Make one on the PC (Learn > Lectures) from a PDF or your notes.'),
            ),
          ..._lectures.map((x) => ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.school_outlined),
                title: Text('${x['title'] ?? 'Lecture'}'),
                subtitle: Text('${x['file'] ?? ''}'),
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => LectureScreen(id: '${x['id']}', title: '${x['title'] ?? 'Lecture'}'))),
              )),
        ],
      ),
    );
  }
}

class LectureScreen extends StatefulWidget {
  const LectureScreen({super.key, required this.id, required this.title});

  final String id;
  final String title;

  @override
  State<LectureScreen> createState() => _LectureScreenState();
}

class _LectureScreenState extends State<LectureScreen> {
  Map<String, dynamic>? _lec;
  String _error = '';

  @override
  void initState() {
    super.initState();
    Api.instance.get('/api/plugins/lectures/${Uri.encodeComponent(widget.id)}').then((v) {
      if (mounted) setState(() => _lec = v as Map<String, dynamic>);
    }, onError: (Object e) {
      if (mounted) setState(() => _error = '$e');
    });
  }

  @override
  void dispose() {
    Speech.instance.stop();
    super.dispose();
  }

  Future<void> _play() async {
    if (Speech.instance.speaking) {
      await Speech.instance.stop();
      setState(() {});
      return;
    }
    final script = (_lec?['script'] as List? ?? const []).whereType<Map>().map((s) => '${s['text'] ?? ''}').toList();
    setState(() {});
    await Speech.instance.sayAll(script.isEmpty ? ['${_lec?['summary'] ?? ''}'] : script);
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final lec = _lec;
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      floatingActionButton: lec == null
          ? null
          : FloatingActionButton.extended(
              onPressed: _play,
              icon: Icon(Speech.instance.speaking ? Icons.stop : Icons.play_arrow),
              label: Text(Speech.instance.speaking ? 'Stop' : 'Play lecture'),
            ),
      body: lec == null
          ? Center(child: _error.isEmpty ? const CircularProgressIndicator() : Text(_error))
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
              children: [
                Text('Summary', style: t.titleMedium),
                Text('${lec['summary'] ?? ''}'),
                const SizedBox(height: 16),
                if ((lec['concepts'] as List?)?.isNotEmpty ?? false) ...[
                  Text('Key ideas', style: t.titleMedium),
                  ...(lec['concepts'] as List).map((c) => Text('• ${c is Map ? (c['name'] ?? c['term'] ?? c) : c}')),
                  const SizedBox(height: 16),
                ],
                if ((lec['quiz'] as List?)?.isNotEmpty ?? false) ...[
                  Text('Quiz', style: t.titleMedium),
                  ...(lec['quiz'] as List).whereType<Map>().map((q) => ExpansionTile(
                        tilePadding: EdgeInsets.zero,
                        title: Text('${q['q']}'),
                        children: [
                          ListTile(title: Text('${q['answer']}'), subtitle: Text('${q['where'] ?? ''}')),
                        ],
                      )),
                ],
              ],
            ),
    );
  }
}
