// One search box for everything (like Ctrl+K on the PC): your knowledge library, past chats, Schedule and apps.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api.dart';

class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _Hit {
  _Hit(this.group, this.title, this.subtitle, {this.url});

  final String group;
  final String title;
  final String subtitle;
  final String? url;
}

class _SearchScreenState extends State<SearchScreen> {
  final _q = TextEditingController();
  Timer? _debounce;
  List<_Hit> _hits = [];
  String _answer = '';
  String _note = '';
  int _ticket = 0;

  @override
  void dispose() {
    _debounce?.cancel();
    _q.dispose();
    super.dispose();
  }

  void _changed(String _) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), _run);
  }

  Future<void> _run() async {
    final q = _q.text.trim();
    final ticket = ++_ticket;
    if (q.length < 2) {
      setState(() => _hits = []);
      return;
    }
    setState(() => _note = 'Searching...');
    final enc = Uri.encodeQueryComponent(q.length > 300 ? q.substring(0, 300) : q);
    final hits = <_Hit>[];
    var answer = '';
    String note = '';
    Future<void> safe(Future<void> Function() f) async {
      try {
        await f();
      } on ApiError catch (e) {
        note = e.message;
      }
    }

    await Future.wait([
      safe(() async {
        final r = await Api.instance.get('/api/plugins/knowledge/search?q=$enc') as Map<String, dynamic>;
        answer = '${(r['answer'] as Map?)?['text'] ?? ''}';
        for (final x in (r['results'] as List? ?? const []).whereType<Map>()) {
          hits.add(_Hit('Library', '${x['title'] ?? x['name'] ?? 'Passage'}', '${x['text'] ?? x['where'] ?? ''}'));
        }
      }),
      safe(() async {
        final r = await Api.instance.get('/api/history?q=$enc&limit=10') as List<dynamic>;
        for (final x in r.whereType<Map>()) {
          hits.add(_Hit('Chats', '${x['text']}', '${x['at'] ?? ''}'));
        }
      }),
      safe(() async {
        final r = await Api.instance.get('/api/plugins/schedule') as Map<String, dynamic>;
        final low = q.toLowerCase();
        for (final x in (r['events'] as List? ?? const []).whereType<Map>()) {
          if ('${x['title']} ${x['notes'] ?? ''}'.toLowerCase().contains(low)) {
            hits.add(_Hit('Schedule', '${x['title']}', '${x['date']} ${x['time'] ?? ''}'));
          }
        }
      }),
      safe(() async {
        final r = await Api.instance.get('/api/plugins/desktop/tiles') as Map<String, dynamic>;
        final low = q.toLowerCase();
        for (final x in [...(r['tiles'] as List? ?? const []), ...(r['ai'] as List? ?? const [])].whereType<Map>()) {
          if ('${x['name']}'.toLowerCase().contains(low)) {
            hits.add(_Hit('Apps', '${x['name']}', '${x['url']}', url: '${x['url']}'));
          }
        }
      }),
    ]);
    if (!mounted || ticket != _ticket) return;
    setState(() {
      _hits = hits;
      _answer = answer;
      _note = hits.isEmpty ? (note.isEmpty ? 'Nothing found for "$q".' : note) : '';
    });
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    String? last;
    final rows = <Widget>[];
    for (final h in _hits.take(60)) {
      if (h.group != last) {
        rows.add(Padding(padding: const EdgeInsets.fromLTRB(16, 12, 16, 4), child: Text(h.group, style: t.titleSmall)));
        last = h.group;
      }
      rows.add(ListTile(
        title: Text(h.title, maxLines: 2, overflow: TextOverflow.ellipsis),
        subtitle: h.subtitle.isEmpty ? null : Text(h.subtitle, maxLines: 2, overflow: TextOverflow.ellipsis),
        onTap: h.url == null
            ? null
            : () {
                final u = Uri.tryParse(h.url!);
                if (u != null && u.scheme == 'https') launchUrl(u, mode: LaunchMode.externalApplication);
              },
      ));
    }
    return Scaffold(
      appBar: AppBar(
        title: TextField(
          controller: _q,
          autofocus: true,
          onChanged: _changed,
          onSubmitted: (_) => _run(),
          decoration: const InputDecoration(hintText: 'Search everything', border: InputBorder.none),
        ),
      ),
      body: ListView(
        children: [
          if (_answer.isNotEmpty) Padding(padding: const EdgeInsets.all(16), child: Text(_answer)),
          if (_note.isNotEmpty) Padding(padding: const EdgeInsets.all(16), child: Text(_note)),
          ...rows,
        ],
      ),
    );
  }
}
