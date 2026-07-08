import 'package:flutter_test/flutter_test.dart';
import 'package:idea_scraper_ui/main.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('app builds and shows project picker', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final prefs = await SharedPreferences.getInstance();
    await tester.pumpWidget(App(prefs: prefs));
    await tester.pump();
    expect(find.text('VectorPaper'), findsOneWidget);
  });
}
