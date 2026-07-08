import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'common.dart';
import 'library_page.dart';
import 'search_page.dart';
import 'settings_page.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(App(prefs: await SharedPreferences.getInstance()));
}

/// App-wide state: theme + active project, persisted between launches.
class AppState extends ChangeNotifier {
  AppState(this._prefs) {
    themeMode = ThemeMode.values.asNameMap()[_prefs.getString('theme')] ?? ThemeMode.system;
    final saved = _prefs.getString('project');
    if (saved != null) project = jsonDecode(saved) as Map<String, dynamic>;
  }

  final SharedPreferences _prefs;
  ThemeMode themeMode = ThemeMode.system;
  Map<String, dynamic>? project;

  void setTheme(ThemeMode m) {
    themeMode = m;
    _prefs.setString('theme', m.name);
    notifyListeners();
  }

  void setProject(Map<String, dynamic>? p) {
    project = p;
    p == null ? _prefs.remove('project') : _prefs.setString('project', jsonEncode(p));
    notifyListeners();
  }
}

const _sharp = RoundedRectangleBorder(borderRadius: BorderRadius.zero);

ThemeData _theme(Brightness b) {
  final scheme = ColorScheme.fromSeed(seedColor: const Color(0xFF2A3F54), brightness: b);
  return ThemeData(
    colorScheme: scheme,
    useMaterial3: true,
    scaffoldBackgroundColor: scheme.surfaceContainerLow,
    appBarTheme: AppBarTheme(
      backgroundColor: scheme.surfaceContainerLow,
      elevation: 0,
      surfaceTintColor: Colors.transparent,
      shape: Border(bottom: BorderSide(color: scheme.outlineVariant.withValues(alpha: 0.6))),
    ),
    cardTheme: CardThemeData(
      elevation: 0,
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
      color: scheme.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.zero,
        side: BorderSide(color: scheme.outlineVariant.withValues(alpha: 0.5)),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      isDense: true,
      filled: true,
      fillColor: scheme.surface,
      border: const OutlineInputBorder(borderRadius: BorderRadius.zero),
    ),
    filledButtonTheme: FilledButtonThemeData(style: FilledButton.styleFrom(shape: _sharp)),
    outlinedButtonTheme: OutlinedButtonThemeData(style: OutlinedButton.styleFrom(shape: _sharp)),
    textButtonTheme: TextButtonThemeData(style: TextButton.styleFrom(shape: _sharp)),
    elevatedButtonTheme: ElevatedButtonThemeData(style: ElevatedButton.styleFrom(shape: _sharp)),
    iconButtonTheme: IconButtonThemeData(style: IconButton.styleFrom(shape: _sharp)),
    segmentedButtonTheme:
        SegmentedButtonThemeData(style: SegmentedButton.styleFrom(shape: _sharp)),
    chipTheme: ChipThemeData(shape: _sharp, side: BorderSide(color: scheme.outlineVariant)),
    dialogTheme: const DialogThemeData(shape: _sharp),
    snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating, shape: _sharp),
    navigationRailTheme: const NavigationRailThemeData(indicatorShape: _sharp),
  );
}

class App extends StatelessWidget {
  App({super.key, required SharedPreferences prefs}) : state = AppState(prefs);
  final AppState state;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: state,
        builder: (_, _) => MaterialApp(
          title: 'VectorPaper',
          debugShowCheckedModeBanner: false,
          theme: _theme(Brightness.light),
          darkTheme: _theme(Brightness.dark),
          themeMode: state.themeMode,
          home: state.project == null
              ? ProjectsPage(state: state)
              : WorkspacePage(key: ValueKey(state.project!['id']), state: state),
        ),
      );
}

// ---------------- Project picker ----------------

class ProjectsPage extends StatefulWidget {
  const ProjectsPage({super.key, required this.state});
  final AppState state;
  @override
  State<ProjectsPage> createState() => _ProjectsPageState();
}

class _ProjectsPageState extends State<ProjectsPage> {
  List<dynamic>? _projects;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final r = await Api.get('/projects');
      if (mounted) setState(() => _projects = r['projects']);
    } catch (e) {
      if (mounted) setState(() => _error = 'Could not reach the API: $e');
    }
  }

  Future<void> _create() async {
    final ctrl = TextEditingController();
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('New project'),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          decoration: const InputDecoration(labelText: 'Project name'),
          onSubmitted: (v) => Navigator.pop(ctx, v),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, ctrl.text), child: const Text('Create')),
        ],
      ),
    );
    if (name == null || name.trim().isEmpty) return;
    final p = await Api.post('/projects', {'name': name.trim()});
    widget.state.setProject(p as Map<String, dynamic>);
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 560),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.data_object, size: 56, color: scheme.primary),
              const SizedBox(height: 12),
              Text('VectorPaper', style: Theme.of(context).textTheme.headlineMedium),
              Text('Academic research assistant — pick a project to work in',
                  style: TextStyle(color: scheme.onSurfaceVariant)),
              const SizedBox(height: 24),
              if (_error != null) ...[
                Text(_error!, style: TextStyle(color: scheme.error)),
                const SizedBox(height: 8),
                OutlinedButton.icon(
                    onPressed: _load, icon: const Icon(Icons.refresh), label: const Text('Retry')),
              ] else if (_projects == null)
                const CircularProgressIndicator()
              else
                Flexible(
                  child: ListView(
                    shrinkWrap: true,
                    children: [
                      for (final p in _projects!)
                        Card(
                          child: ListTile(
                            leading: const Icon(Icons.folder_outlined),
                            title: Text(p['name']),
                            subtitle: Text('${p['paper_count']} papers'),
                            trailing: IconButton(
                              icon: const Icon(Icons.delete_outline),
                              tooltip: 'Delete project',
                              onPressed: () async {
                                if (!await confirm(context, 'Delete project',
                                    '"${p['name']}" and all its papers/data will be deleted.')) {
                                  return;
                                }
                                await Api.delete('/projects/${p['id']}');
                                _load();
                              },
                            ),
                            onTap: () => widget.state.setProject(p as Map<String, dynamic>),
                          ),
                        ),
                    ],
                  ),
                ),
              const SizedBox(height: 16),
              if (_error == null)
                FilledButton.icon(
                    onPressed: _create, icon: const Icon(Icons.add), label: const Text('New project')),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------- Workspace shell: rail + pages ----------------

class WorkspacePage extends StatefulWidget {
  const WorkspacePage({super.key, required this.state});
  final AppState state;
  @override
  State<WorkspacePage> createState() => _WorkspacePageState();
}

class _WorkspacePageState extends State<WorkspacePage> {
  int _tab = 0;
  final _libraryKey = GlobalKey<LibraryPageState>();

  @override
  Widget build(BuildContext context) {
    final state = widget.state;
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Scaffold(
      appBar: AppBar(
        title: Row(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.data_object, size: 22),
          const SizedBox(width: 8),
          Text(state.project!['name'], style: const TextStyle(fontWeight: FontWeight.w600)),
        ]),
        actions: [
          IconButton(
            tooltip: dark ? 'Light theme' : 'Dark theme',
            icon: Icon(dark ? Icons.light_mode_outlined : Icons.dark_mode_outlined),
            onPressed: () => state.setTheme(dark ? ThemeMode.light : ThemeMode.dark),
          ),
          IconButton(
            tooltip: 'Switch project',
            icon: const Icon(Icons.swap_horiz),
            onPressed: () => state.setProject(null),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: LayoutBuilder(builder: (context, constraints) {
        final mobile = constraints.maxWidth < 600;
        final pages = IndexedStack(index: _tab, children: [
          SearchPage(
              state: state,
              onIngested: () {
                setState(() => _tab = 1);
                _libraryKey.currentState?.refresh();
              }),
          LibraryPage(key: _libraryKey, state: state),
          SettingsPage(state: state),
        ]);
        if (mobile) return pages; // rail becomes the bottom bar below, in bottomNavigationBar
        return Row(children: [
          NavigationRail(
            selectedIndex: _tab,
            onDestinationSelected: _selectTab,
            labelType: NavigationRailLabelType.all,
            destinations: const [
              NavigationRailDestination(
                  icon: Icon(Icons.travel_explore_outlined), label: Text('Discover')),
              NavigationRailDestination(
                  icon: Icon(Icons.library_books_outlined), label: Text('Library')),
              NavigationRailDestination(icon: Icon(Icons.tune_outlined), label: Text('Settings')),
            ],
          ),
          const VerticalDivider(width: 1),
          Expanded(child: pages),
        ]);
      }),
      bottomNavigationBar: MediaQuery.sizeOf(context).width < 600
          ? NavigationBar(
              selectedIndex: _tab,
              onDestinationSelected: _selectTab,
              destinations: const [
                NavigationDestination(
                    icon: Icon(Icons.travel_explore_outlined), label: 'Discover'),
                NavigationDestination(icon: Icon(Icons.library_books_outlined), label: 'Library'),
                NavigationDestination(icon: Icon(Icons.tune_outlined), label: 'Settings'),
              ],
            )
          : null,
    );
  }

  void _selectTab(int i) {
    setState(() => _tab = i);
    if (i == 1) _libraryKey.currentState?.refresh();
  }
}
