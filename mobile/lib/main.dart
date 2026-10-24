// Airtac Mobile: AIRTAC Advance on Ali's phone. Native screens for chat, today, learning and the schedule; the full
// web app for the rest. Everything talks to the paired PC (lib/src/api.dart); nothing goes to any other server.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:local_auth/local_auth.dart';
import 'package:receive_sharing_intent/receive_sharing_intent.dart';

import 'src/api.dart';
import 'src/screens/chat_screen.dart';
import 'src/screens/learn_screen.dart';
import 'src/screens/more_screen.dart';
import 'src/screens/pair_screen.dart';
import 'src/screens/schedule_screen.dart';
import 'src/screens/search_screen.dart';
import 'src/screens/today_screen.dart';
import 'src/store.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Api.instance.load();
  runApp(const AirtacApp());
}

class AirtacApp extends StatelessWidget {
  const AirtacApp({super.key});

  @override
  Widget build(BuildContext context) {
    const seed = Color(0xFFD7263D); // AIRTAC's red
    return MaterialApp(
      title: 'Airtac',
      theme: ThemeData(colorSchemeSeed: seed, useMaterial3: true),
      darkTheme: ThemeData(colorSchemeSeed: seed, brightness: Brightness.dark, useMaterial3: true),
      home: const Root(),
    );
  }
}

class Root extends StatefulWidget {
  const Root({super.key});

  @override
  State<Root> createState() => _RootState();
}

class _RootState extends State<Root> with WidgetsBindingObserver {
  bool _unlocked = false;
  bool _asking = false;
  String _lockNote = '';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _unlock();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused) {
      LocalStore.instance.getFlag('lock', fallback: true).then((on) {
        if (on && mounted) setState(() => _unlocked = false);
      });
    } else if (state == AppLifecycleState.resumed && !_unlocked) {
      _unlock();
    }
  }

  Future<void> _unlock() async {
    if (_asking) return;
    final want = await LocalStore.instance.getFlag('lock', fallback: true);
    if (!want || !Api.instance.paired) {
      if (mounted) setState(() => _unlocked = true);
      return;
    }
    _asking = true;
    try {
      final auth = LocalAuthentication();
      if (!await auth.isDeviceSupported()) {
        if (mounted) setState(() => _unlocked = true); // no screen lock on the phone: nothing to ask with
        return;
      }
      final ok = await auth.authenticate(localizedReason: 'Open Airtac', persistAcrossBackgrounding: true);
      if (mounted) {
        setState(() {
          _unlocked = ok;
          _lockNote = ok ? '' : 'Locked. Tap to try again.';
        });
      }
    } catch (e) {
      if (mounted) setState(() => _lockNote = 'Couldn\'t check your fingerprint: $e');
    } finally {
      _asking = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!Api.instance.paired) {
      return PairScreen(onPaired: () => setState(() => _unlocked = true));
    }
    if (!_unlocked) {
      return Scaffold(
        body: Center(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            IconButton.filled(iconSize: 48, onPressed: _unlock, icon: const Icon(Icons.fingerprint)),
            const SizedBox(height: 12),
            Text(_lockNote.isEmpty ? 'Airtac is locked' : _lockNote),
          ]),
        ),
      );
    }
    return Home(onUnpaired: () => setState(() {}));
  }
}

class Home extends StatefulWidget {
  const Home({super.key, required this.onUnpaired});

  final VoidCallback onUnpaired;

  @override
  State<Home> createState() => _HomeState();
}

class _HomeState extends State<Home> {
  int _tab = 0;
  StreamSubscription<List<SharedMediaFile>>? _shares;
  String _shareNote = '';

  static const _titles = ['Chat', 'Today', 'Learn', 'Schedule', 'More'];

  @override
  void initState() {
    super.initState();
    _shares = ReceiveSharingIntent.instance.getMediaStream().listen(_shared, onError: (Object _) {});
    ReceiveSharingIntent.instance.getInitialMedia().then((files) {
      _shared(files);
      ReceiveSharingIntent.instance.reset();
    });
  }

  @override
  void dispose() {
    _shares?.cancel();
    super.dispose();
  }

  /// "Share to Airtac": text goes into the chat box; files are uploaded to the PC and attached to the next message.
  Future<void> _shared(List<SharedMediaFile> files) async {
    if (files.isEmpty) return;
    setState(() {
      _tab = 0;
      _shareNote = 'Adding what you shared...';
    });
    final failed = <String>[];
    for (final f in files) {
      if (f.type == SharedMediaType.text || f.type == SharedMediaType.url) {
        PendingShare.text.value = f.path;
        continue;
      }
      try {
        await PendingShare.upload(f.path);
      } on ApiError catch (e) {
        failed.add(e.message);
      } catch (e) {
        failed.add('$e');
      }
    }
    if (mounted) setState(() => _shareNote = failed.isEmpty ? '' : failed.join('\n'));
  }

  @override
  Widget build(BuildContext context) {
    final pages = <Widget>[
      const ChatScreen(),
      const TodayScreen(),
      const LearnScreen(),
      const ScheduleScreen(),
      MoreScreen(onUnpaired: widget.onUnpaired),
    ];
    return Scaffold(
      appBar: AppBar(
        title: Text(_titles[_tab]),
        actions: [
          IconButton(
            tooltip: 'Search everything',
            icon: const Icon(Icons.search),
            onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const SearchScreen())),
          ),
        ],
      ),
      body: Column(
        children: [
          if (_shareNote.isNotEmpty)
            MaterialBanner(content: Text(_shareNote), actions: [
              TextButton(onPressed: () => setState(() => _shareNote = ''), child: const Text('OK')),
            ]),
          Expanded(child: IndexedStack(index: _tab, children: pages)),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.chat_bubble_outline), label: 'Chat'),
          NavigationDestination(icon: Icon(Icons.today_outlined), label: 'Today'),
          NavigationDestination(icon: Icon(Icons.school_outlined), label: 'Learn'),
          NavigationDestination(icon: Icon(Icons.event_outlined), label: 'Schedule'),
          NavigationDestination(icon: Icon(Icons.more_horiz), label: 'More'),
        ],
      ),
    );
  }
}
