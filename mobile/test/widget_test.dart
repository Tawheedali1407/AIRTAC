// Kept so `flutter create .` (run in CI to make the android/ folder) doesn't add its counter-app sample test.
import 'package:airtac_mobile/src/screens/pair_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('the pairing screen explains where the code comes from', (tester) async {
    await tester.pumpWidget(MaterialApp(home: PairScreen(onPaired: () {})));
    expect(find.textContaining('Get mobile app'), findsOneWidget);
    expect(find.text('Scan the QR code'), findsOneWidget);
  });
}
