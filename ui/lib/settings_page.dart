import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import 'api.dart';
import 'common.dart';
import 'main.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key, required this.state});
  final AppState state;
  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  List<dynamic>? _providers;
  List<dynamic> _papers = [];

  String get _pid => widget.state.project!['id'];
  String get _provider =>
      (widget.state.project!['settings'] ?? {})['provider'] as String? ?? 'ollama';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final prov = await Api.get('/providers');
      final papers = await Api.get('/papers', {'project_id': _pid, 'limit': '200'});
      if (mounted) {
        setState(() {
          _providers = prov['providers'];
          _papers = papers['papers'];
        });
      }
    } catch (e) {
      if (mounted) snack(context, 'Could not load settings: $e');
    }
  }

  Future<void> _setProvider(String id) async {
    final p = await Api.patch('/projects/$_pid', {
      'settings': {'provider': id}
    });
    widget.state.setProject(p as Map<String, dynamic>);
    setState(() {});
  }

  Future<void> _export(Map paper) async {
    final safe = (paper['title'] as String).replaceAll(RegExp(r'[^\w\s-]'), '').trim();
    final loc = await getSaveLocation(
        suggestedName: '${safe.substring(0, safe.length.clamp(0, 60))}.pdf',
        acceptedTypeGroups: const [XTypeGroup(label: 'PDF', extensions: ['pdf'])]);
    if (loc == null) return;
    final r = await http.get(Uri.parse('$apiBase/papers/${paper['id']}/pdf'));
    if (r.statusCode != 200) {
      if (mounted) snack(context, 'Could not download PDF (HTTP ${r.statusCode})');
      return;
    }
    await File(loc.path).writeAsBytes(r.bodyBytes);
    if (mounted) snack(context, 'Saved: ${loc.path}');
  }

  Future<void> _deletePaper(Map paper) async {
    if (!await confirm(context, 'Delete paper',
        '"${paper['title']}" will be deleted, including its PDF, vectors, and summaries.')) {
      return;
    }
    await Api.delete('/papers/${paper['id']}');
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final style = Theme.of(context).textTheme;
    return ListView(padding: const EdgeInsets.all(16), children: [
      Text('Appearance', style: style.titleMedium),
      const SizedBox(height: 8),
      SegmentedButton<ThemeMode>(
        segments: const [
          ButtonSegment(
              value: ThemeMode.system, icon: Icon(Icons.brightness_auto), label: Text('System')),
          ButtonSegment(
              value: ThemeMode.light, icon: Icon(Icons.light_mode), label: Text('Light')),
          ButtonSegment(value: ThemeMode.dark, icon: Icon(Icons.dark_mode), label: Text('Dark')),
        ],
        selected: {widget.state.themeMode},
        onSelectionChanged: (s) => widget.state.setTheme(s.first),
      ),
      const SizedBox(height: 24),
      Text('AI provider', style: style.titleMedium),
      Text('Used for summaries, analysis, and search help in this project.',
          style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 13)),
      const SizedBox(height: 4),
      if (_providers == null)
        const Padding(padding: EdgeInsets.all(16), child: LinearProgressIndicator())
      else
        RadioGroup<String>(
          groupValue: _provider,
          onChanged: (v) => _setProvider(v!),
          child: Column(children: [
            for (final pr in _providers!)
              RadioListTile<String>(
                value: pr['id'],
                enabled: pr['available'] == true,
                title: Text(pr['label']),
                subtitle: Text(pr['available'] == true
                    ? 'model: ${pr['model']}'
                    : 'API key not set — add it to .env (see .env.example)'),
              ),
          ]),
        ),
      const SizedBox(height: 24),
      Row(children: [
        Text('Downloaded content', style: style.titleMedium),
        const Spacer(),
        if (_papers.isNotEmpty)
          TextButton.icon(
            onPressed: () async {
              if (!await confirm(context, 'Delete all',
                  'All data for ${_papers.length} papers in this project will be deleted.')) {
                return;
              }
              await Api.delete('/projects/$_pid/papers');
              _load();
            },
            icon: const Icon(Icons.delete_sweep_outlined, size: 18),
            label: const Text('Delete all'),
          ),
      ]),
      const SizedBox(height: 4),
      if (_papers.isEmpty)
        Text('No downloaded content in this project.', style: TextStyle(color: scheme.onSurfaceVariant))
      else
        Card(
          margin: EdgeInsets.zero,
          child: Column(children: [
            for (final p in _papers)
              ListTile(
                dense: true,
                title: Text(p['title'], maxLines: 1, overflow: TextOverflow.ellipsis),
                subtitle: Text(paperMeta(p)),
                leading: statusChip(context, p['status']),
                trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                  if (p['has_pdf'] == true)
                    IconButton(
                        icon: const Icon(Icons.save_alt, size: 20),
                        tooltip: 'Export PDF',
                        onPressed: () => _export(p)),
                  IconButton(
                      icon: const Icon(Icons.delete_outline, size: 20),
                      tooltip: 'Delete',
                      onPressed: () => _deletePaper(p)),
                ]),
              ),
          ]),
        ),
      const SizedBox(height: 24),
      Text('Danger zone', style: style.titleMedium?.copyWith(color: scheme.error)),
      const SizedBox(height: 8),
      Align(
        alignment: Alignment.centerLeft,
        child: OutlinedButton.icon(
          style: OutlinedButton.styleFrom(foregroundColor: scheme.error),
          onPressed: () async {
            if (!await confirm(context, 'Delete project',
                'The project "${widget.state.project!['name']}" and all its data will be permanently deleted.')) {
              return;
            }
            await Api.delete('/projects/$_pid');
            widget.state.setProject(null);
          },
          icon: const Icon(Icons.delete_forever_outlined),
          label: const Text('Permanently delete project'),
        ),
      ),
      const SizedBox(height: 24),
    ]);
  }
}
