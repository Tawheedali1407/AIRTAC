// More: AIRTAC 1, app links, the knowledge library, the full web app, the vault (PC only), and this phone's settings.
import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api.dart';
import '../store.dart';
import 'chat_screen.dart';
import 'search_screen.dart';
import 'web_screen.dart';

class MoreScreen extends StatefulWidget {
  const MoreScreen({super.key, required this.onUnpaired});

  final VoidCallback onUnpaired;

  @override
  State<MoreScreen> createState() => _MoreScreenState();
}

class _MoreScreenState extends State<MoreScreen> {
  void _open(Widget page) => Navigator.of(context).push(MaterialPageRoute(builder: (_) => page));

  @override
  Widget build(BuildContext context) {
    return ListView(
      children: [
        ListTile(
          leading: const Icon(Icons.memory),
          title: const Text('AIRTAC 1'),
          subtitle: const Text('Your own AI brain on the PC'),
          onTap: () => _open(const Airtac1Screen()),
        ),
        ListTile(
          leading: const Icon(Icons.apps),
          title: const Text('Apps'),
          subtitle: const Text('Your app links from the PC'),
          onTap: () => _open(const AppsScreen()),
        ),
        ListTile(
          leading: const Icon(Icons.search),
          title: const Text('Search everything'),
          subtitle: const Text('Library, chats, Schedule and apps'),
          onTap: () => _open(const SearchScreen()),
        ),
        ListTile(
          leading: const Icon(Icons.public),
          title: const Text('Full app'),
          subtitle: const Text('Every other AIRTAC page (Work mode, Research, Settings...)'),
          onTap: () => _open(const WebScreen()),
        ),
        ListTile(
          leading: const Icon(Icons.lock_outline),
          title: const Text('Passwords and API keys'),
          subtitle: const Text('They stay on your PC: open AIRTAC there to see them.'),
          onTap: () => showDialog<void>(
            context: context,
            builder: (dialogContext) => AlertDialog(
              title: const Text('The vault opens on your PC only'),
              content: const Text('Your passwords and API keys never leave the PC, not even to this phone. '
                  'This app can still be locked with your fingerprint (Settings below).'),
              actions: [TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text('OK'))],
            ),
          ),
        ),
        ListTile(
          leading: const Icon(Icons.settings),
          title: const Text('This phone'),
          onTap: () => _open(PhoneSettings(onUnpaired: widget.onUnpaired)),
        ),
      ],
    );
  }
}

class Airtac1Screen extends StatefulWidget {
  const Airtac1Screen({super.key});

  @override
  State<Airtac1Screen> createState() => _Airtac1ScreenState();
}

class _Airtac1ScreenState extends State<Airtac1Screen> {
  String? _brain;
  String _note = 'Checking...';

  @override
  void initState() {
    super.initState();
    Api.instance.get('/api/brain_choices').then((r) {
      final groups = ((r as Map)['groups'] as List? ?? const []).whereType<Map>();
      final g = groups.where((x) => x['id'] == 'airtac1').toList();
      final items = g.isEmpty ? const [] : (g.first['items'] as List? ?? const []);
      if (!mounted) return;
      setState(() {
        if (items.isNotEmpty && items.first is Map) {
          _brain = '${(items.first as Map)['id']}';
          _note = '';
        } else {
          _note = g.isEmpty ? "This PC's AIRTAC doesn't know AIRTAC 1 yet." : '${g.first['note'] ?? 'AIRTAC 1 is not installed yet.'}';
        }
      });
    }, onError: (Object e) {
      if (mounted) setState(() => _note = '$e');
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('AIRTAC 1')),
      body: _brain == null
          ? Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(_note, textAlign: TextAlign.center)))
          : ChatScreen(session: 'phone-airtac1', fixedBrain: _brain, title: 'AIRTAC 1'),
    );
  }
}

class AppsScreen extends StatefulWidget {
  const AppsScreen({super.key});

  @override
  State<AppsScreen> createState() => _AppsScreenState();
}

class _AppsScreenState extends State<AppsScreen> {
  List<Map> _apps = [];
  String _note = 'Loading...';

  @override
  void initState() {
    super.initState();
    Api.instance.get('/api/plugins/desktop/tiles').then((r) {
      final m = r as Map;
      final all = [...(m['ai'] as List? ?? const []), ...(m['tiles'] as List? ?? const [])].whereType<Map>().toList();
      if (!mounted) return;
      setState(() {
        _apps = all;
        _note = all.isEmpty ? 'No apps yet. Add them on the PC (Apps).' : '';
      });
    }, onError: (Object e) {
      if (mounted) setState(() => _note = '$e');
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Apps')),
      body: _apps.isEmpty
          ? Center(child: Text(_note))
          : ListView(
              children: _apps
                  .map((a) => ListTile(
                        leading: CircleAvatar(child: Text(_mark(a))),
                        title: Text('${a['name']}'),
                        subtitle: Text('${a['about'] ?? a['url'] ?? ''}', maxLines: 1, overflow: TextOverflow.ellipsis),
                        onTap: () {
                          final u = Uri.tryParse('${a['url']}');
                          if (u != null && u.scheme == 'https') launchUrl(u, mode: LaunchMode.externalApplication);
                        },
                      ))
                  .toList(),
            ),
    );
  }
}

class PhoneSettings extends StatefulWidget {
  const PhoneSettings({super.key, required this.onUnpaired});

  final VoidCallback onUnpaired;

  @override
  State<PhoneSettings> createState() => _PhoneSettingsState();
}

class _PhoneSettingsState extends State<PhoneSettings> {
  bool _lock = true;
  int _queued = 0;
  final _tailscale = TextEditingController();
  String _note = '';

  @override
  void initState() {
    super.initState();
    LocalStore.instance.getFlag('lock', fallback: true).then((v) {
      if (mounted) setState(() => _lock = v);
    });
    LocalStore.instance.queue().then((q) {
      if (mounted) setState(() => _queued = q.length);
    });
  }

  @override
  void dispose() {
    _tailscale.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final link = Api.instance.link;
    return Scaffold(
      appBar: AppBar(title: const Text('This phone')),
      body: ListView(
        padding: const EdgeInsets.all(8),
        children: [
          ListTile(
            title: Text('Paired with ${link?.pcName ?? '-'}'),
            subtitle: Text('${link?.hosts.join(', ') ?? ''}  port ${link?.port ?? ''}\n'
                'Certificate ${link?.fingerprint.substring(0, 16) ?? ''}...'),
          ),
          SwitchListTile(
            title: const Text('Ask for fingerprint or face to open'),
            value: _lock,
            onChanged: (v) {
              LocalStore.instance.setFlag('lock', v);
              setState(() => _lock = v);
            },
          ),
          ListTile(title: Text('$_queued messages waiting for the PC')),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _tailscale,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: "PC's Tailscale address (100.x.y.z), for away from home"),
                ),
              ),
              TextButton(
                onPressed: () async {
                  final ip = _tailscale.text.trim();
                  if (!RegExp(r'^100\.(\d{1,3}\.){2}\d{1,3}$').hasMatch(ip)) {
                    setState(() => _note = 'That is not a Tailscale address (they start with 100.).');
                    return;
                  }
                  await Api.instance.addHost(ip);
                  if (mounted) setState(() => _note = 'Added. The app tries Wi-Fi first, then Tailscale.');
                },
                child: const Text('Add'),
              ),
            ]),
          ),
          if (_note.isNotEmpty) Padding(padding: const EdgeInsets.all(16), child: Text(_note)),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.link_off),
            title: const Text('Unpair this phone'),
            subtitle: const Text('Forgets the PC here. Also remove it on the PC: Settings > Devices.'),
            onTap: () async {
              await Api.instance.forget();
              widget.onUnpaired();
              if (context.mounted) Navigator.of(context).pop();
            },
          ),
        ],
      ),
    );
  }
}

String _mark(Map a) {
  final m = '${a['mark'] ?? ''}';
  if (m.isNotEmpty) return m;
  final n = '${a['name'] ?? ''}';
  return n.isEmpty ? '?' : n.substring(0, 1);
}
