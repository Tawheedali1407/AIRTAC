// Chat with AIRTAC on the PC. Messages typed while the PC can't be reached wait in a queue and go when it answers.
// Files arrive through Android's share sheet ("Share to Airtac") and are attached to the next message.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';

import '../api.dart';
import '../store.dart';

class Attachment {
  Attachment(this.id, this.name);

  final int id;
  final String name;
}

/// Shared files are uploaded right away and wait here for the next message (the main screen fills this).
class PendingShare {
  static final ValueNotifier<List<Attachment>> files = ValueNotifier<List<Attachment>>([]);
  static final ValueNotifier<String> text = ValueNotifier<String>('');

  /// Uploads one shared file (the PC checks its type and size; 25 MB at most) and returns it.
  static Future<Attachment> upload(String path) async {
    final f = File(path);
    final name = f.uri.pathSegments.isEmpty ? 'shared-file' : f.uri.pathSegments.last;
    final raw = await f.readAsBytes();
    if (raw.length > 25 * 1000 * 1000) throw ApiError(413, '$name is over 25 MB.');
    final r = await Api.instance.post('/api/upload', {'section': 'chat', 'name': name, 'data_base64': base64Encode(raw)},
        timeout: const Duration(minutes: 3)) as Map<String, dynamic>;
    final a = Attachment(r['id'] as int, (r['name'] as String?) ?? name);
    files.value = [...files.value, a];
    return a;
  }
}

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key, this.session = 'phone', this.fixedBrain, this.title = 'Chat'});

  final String session;
  final String? fixedBrain; // e.g. the AIRTAC 1 screen
  final String title;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _text = TextEditingController();
  final _scroll = ScrollController();
  List<ChatLine> _lines = [];
  List<Map<String, String>> _brains = [];
  String _brain = '';
  bool _sending = false;
  Timer? _retry;

  @override
  void initState() {
    super.initState();
    _brain = widget.fixedBrain ?? '';
    _load();
    PendingShare.text.addListener(_takeSharedText);
    _takeSharedText();
    _retry = Timer.periodic(const Duration(seconds: 45), (_) => _flush());
  }

  @override
  void dispose() {
    PendingShare.text.removeListener(_takeSharedText);
    _retry?.cancel();
    _text.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _takeSharedText() {
    final t = PendingShare.text.value;
    if (t.isNotEmpty) {
      _text.text = _text.text.isEmpty ? t : '${_text.text}\n$t';
      PendingShare.text.value = '';
    }
  }

  Future<void> _load() async {
    _lines = await LocalStore.instance.lines(widget.session);
    if (mounted) setState(() {});
    _jumpDown();
    if (widget.fixedBrain == null) _loadBrains();
    _flush();
  }

  Future<void> _loadBrains() async {
    try {
      final r = await Api.instance.get('/api/brain_choices') as Map<String, dynamic>;
      final out = <Map<String, String>>[];
      for (final g in (r['groups'] as List? ?? const [])) {
        if (g is! Map) continue;
        for (final it in (g['items'] as List? ?? const [])) {
          if (it is Map && it['id'] is String) {
            out.add({'id': it['id'] as String, 'label': '${g['label']}: ${it['label'] ?? it['id']}'});
          }
        }
      }
      if (mounted) setState(() => _brains = out);
    } on ApiError {
      // offline: the menu stays "Automatic"
    }
  }

  Future<void> _flush() async {
    final n = await LocalStore.instance.flush((m) async => await Api.instance.post(
        '/api/chat', {'message': m.message, 'session_id': m.session, if (m.brain.isNotEmpty) 'brain': m.brain})
        as Map<String, dynamic>);
    if (n > 0 && mounted) {
      _lines = await LocalStore.instance.lines(widget.session);
      setState(() {});
      _jumpDown();
    }
  }

  void _jumpDown() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
    });
  }

  Future<void> _send() async {
    final msg = _text.text.trim();
    final files = PendingShare.files.value;
    if (msg.isEmpty || _sending) return;
    _text.clear();
    final mine = ChatLine(mine: true, text: files.isEmpty ? msg : '$msg\n[${files.map((f) => f.name).join(', ')}]');
    setState(() {
      _lines.add(mine);
      _sending = true;
    });
    _jumpDown();
    try {
      final r = await Api.instance.post('/api/chat', {
        'message': msg.length > 2000 ? msg.substring(0, 2000) : msg,
        'session_id': widget.session,
        if (_brain.isNotEmpty) 'brain': _brain,
        if (files.isNotEmpty) 'attachments': files.map((f) => f.id).toList(),
      }) as Map<String, dynamic>;
      PendingShare.files.value = [];
      _lines.add(ChatLine(mine: false, text: replyText(r), model: replyModel(r)));
      if (r['pending'] != null) {
        _lines.add(ChatLine(mine: false, text: 'This needs your Confirm on the PC (or in Full app).'));
      }
    } on ApiError catch (e) {
      if (e.offline) {
        mine.queued = true;
        await LocalStore.instance.enqueue(QueuedMessage(msg, widget.session, _brain));
      } else {
        _lines.add(ChatLine(mine: false, text: e.message));
      }
    } finally {
      await LocalStore.instance.saveLines(widget.session, _lines);
      if (mounted) setState(() => _sending = false);
      _jumpDown();
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Column(
      children: [
        if (widget.fixedBrain == null)
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
            child: DropdownButtonFormField<String>(
              initialValue: _brains.any((b) => b['id'] == _brain) ? _brain : '',
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Brain', isDense: true),
              items: [
                const DropdownMenuItem(value: '', child: Text('Automatic (your PC first, then the cloud)')),
                ..._brains.map((b) => DropdownMenuItem(value: b['id'], child: Text(b['label']!, overflow: TextOverflow.ellipsis))),
              ],
              onChanged: (v) => setState(() => _brain = v ?? ''),
            ),
          ),
        Expanded(
          child: _lines.isEmpty
              ? const Center(child: Padding(padding: EdgeInsets.all(24), child: Text('Ask AIRTAC anything.')))
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.all(12),
                  itemCount: _lines.length,
                  itemBuilder: (_, i) {
                    final l = _lines[i];
                    return Align(
                      alignment: l.mine ? Alignment.centerRight : Alignment.centerLeft,
                      child: Container(
                        margin: const EdgeInsets.symmetric(vertical: 4),
                        padding: const EdgeInsets.all(12),
                        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.85),
                        decoration: BoxDecoration(
                          color: l.mine ? scheme.primaryContainer : scheme.surfaceContainerHighest,
                          borderRadius: BorderRadius.circular(14),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            SelectableText(l.text),
                            if (l.queued) Text('Waiting for your PC...', style: Theme.of(context).textTheme.labelSmall),
                            if (l.model.isNotEmpty) Text(l.model, style: Theme.of(context).textTheme.labelSmall),
                          ],
                        ),
                      ),
                    );
                  },
                ),
        ),
        ValueListenableBuilder<List<Attachment>>(
          valueListenable: PendingShare.files,
          builder: (_, files, child) => files.isEmpty
              ? const SizedBox.shrink()
              : Wrap(
                  spacing: 6,
                  children: files
                      .map((f) => InputChip(
                            label: Text(f.name),
                            onDeleted: () => PendingShare.files.value =
                                PendingShare.files.value.where((x) => x.id != f.id).toList(),
                          ))
                      .toList(),
                ),
        ),
        SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 4, 8, 8),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _text,
                    minLines: 1,
                    maxLines: 5,
                    maxLength: 2000,
                    textInputAction: TextInputAction.newline,
                    decoration: const InputDecoration(
                      hintText: 'Message AIRTAC (share a file to attach it)',
                      counterText: '',
                    ),
                  ),
                ),
                IconButton.filled(
                  onPressed: _sending ? null : _send,
                  icon: _sending
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.send),
                  tooltip: 'Send',
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
