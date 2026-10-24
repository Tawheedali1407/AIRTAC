// The full AIRTAC page from the PC, for everything without a native screen. The web view trusts ONLY the paired
// certificate (same pin as the API client); its first request carries the device token once, and the PC answers
// with HttpOnly cookies, so the token never appears in a URL.
import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import '../api.dart';

class WebScreen extends StatefulWidget {
  const WebScreen({super.key, this.page = ''});

  final String page; // e.g. "#settings"

  @override
  State<WebScreen> createState() => _WebScreenState();
}

class _WebScreenState extends State<WebScreen> {
  String _error = '';
  InAppWebViewController? _web;
  bool _desktop = false;

  /// The page's own layout switch (web/view.js): phone layout, or the PC layout to scroll sideways and pinch-zoom.
  Future<void> _toggleView() async {
    final next = !_desktop;
    await _web?.evaluateJavascript(source: "window.airtacSetView && window.airtacSetView('${next ? 'desktop' : 'fit'}');");
    if (mounted) setState(() => _desktop = next);
  }

  @override
  Widget build(BuildContext context) {
    final api = Api.instance;
    final link = api.link;
    if (link == null || api.bearer == null) return const Center(child: Text('Pair with your PC first.'));
    final start = 'https://${api.host}:${link.port}/api/pair/web';
    return Scaffold(
      appBar: AppBar(title: const Text('AIRTAC (full app)'), actions: [
        TextButton.icon(
          onPressed: _toggleView,
          icon: Icon(_desktop ? Icons.smartphone : Icons.desktop_windows_outlined),
          label: Text(_desktop ? 'Phone view' : 'Desktop view'),
        ),
      ]),
      body: Column(
        children: [
          if (_error.isNotEmpty) MaterialBanner(content: Text(_error), actions: [
            TextButton(onPressed: () => setState(() => _error = ''), child: const Text('OK')),
          ]),
          Expanded(
            child: InAppWebView(
              initialUrlRequest: URLRequest(url: WebUri(start), headers: {'Authorization': 'Bearer ${api.bearer}'}),
              initialSettings: InAppWebViewSettings(
                javaScriptEnabled: true,
                useShouldOverrideUrlLoading: true,
                thirdPartyCookiesEnabled: false,
                allowFileAccess: false,
                allowContentAccess: false,
                mediaPlaybackRequiresUserGesture: true,
                supportMultipleWindows: false,
                useWideViewPort: true, // honour the page's width, so Desktop view lays out at 1280 px
                loadWithOverviewMode: true,
                supportZoom: true,
                builtInZoomControls: true,
                displayZoomControls: false,
              ),
              onWebViewCreated: (controller) => _web = controller,
              onReceivedServerTrustAuthRequest: (controller, challenge) async {
                final der = challenge.protectionSpace.sslCertificate?.x509Certificate?.encoded;
                final ok = der != null && pinMatches(der, link.fingerprint);
                if (!ok && mounted) {
                  setState(() => _error = "The PC's certificate doesn't match the one you paired with, so the page was blocked.");
                }
                return ServerTrustAuthResponse(
                    action: ok ? ServerTrustAuthResponseAction.PROCEED : ServerTrustAuthResponseAction.CANCEL);
              },
              shouldOverrideUrlLoading: (controller, action) async {
                // Stay on the PC; any other site opens outside the app.
                final u = action.request.url;
                if (u == null || (u.host == api.host && u.port == link.port)) return NavigationActionPolicy.ALLOW;
                return NavigationActionPolicy.CANCEL;
              },
              onLoadStop: (controller, url) async {
                final mode = await controller.evaluateJavascript(
                    source: "document.documentElement.getAttribute('data-page-view')");
                if (mounted) setState(() => _desktop = mode == 'desktop');
                if (widget.page.isNotEmpty && url != null && url.path == '/' && url.fragment.isEmpty) {
                  await controller.evaluateJavascript(source: 'location.hash = ${_jsString(widget.page)};');
                }
              },
            ),
          ),
        ],
      ),
    );
  }
}

String _jsString(String s) => "'${s.replaceAll(RegExp(r"[^A-Za-z0-9#_-]"), '')}'";
