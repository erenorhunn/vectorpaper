import 'package:flutter/material.dart';

import 'api.dart';
import 'common.dart';
import 'detail_page.dart';
import 'main.dart';

class LibraryPage extends StatefulWidget {
  const LibraryPage({super.key, required this.state});
  final AppState state;
  @override
  State<LibraryPage> createState() => LibraryPageState();
}

class LibraryPageState extends State<LibraryPage> {
  final _search = TextEditingController();
  List<dynamic> _papers = [];
  String? _error;
  bool _loading = true;

  String get _pid => widget.state.project!['id'];

  @override
  void initState() {
    super.initState();
    refresh();
  }

  Future<void> refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final q = _search.text.trim();
      final r = await Api.get('/papers', {'project_id': _pid, if (q.isNotEmpty) 'q': q});
      if (mounted) setState(() => _papers = r['papers']);
    } catch (e) {
      if (mounted) setState(() => _error = 'Could not load: $e');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _feedback(Map p, String signal) async {
    await Api.post('/papers/${p['id']}/feedback', {'signal': signal});
    if (mounted) {
      snack(context, signal == 'like' ? 'Liked — improves future search results' : 'Disliked');
    }
  }

  Future<void> _delete(Map p) async {
    if (!await confirm(context, 'Delete paper',
        '"${p['title']}" will be deleted, including its PDF, vectors, and summaries.')) {
      return;
    }
    await Api.delete('/papers/${p['id']}');
    refresh();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
        child: SizedBox(
          height: 32,
          child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Expanded(
              child: TextField(
                controller: _search,
                style: const TextStyle(fontSize: 13),
                decoration: InputDecoration(
                  isDense: true,
                  contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  prefixIcon: const Icon(Icons.manage_search, size: 18),
                  prefixIconConstraints: const BoxConstraints(minWidth: 32, minHeight: 32),
                  hintText: 'Semantic search across your library',
                  suffixIcon: _search.text.isEmpty
                      ? null
                      : IconButton(
                          icon: const Icon(Icons.clear, size: 18),
                          padding: EdgeInsets.zero,
                          constraints: const BoxConstraints.tightFor(width: 32, height: 32),
                          onPressed: () {
                            _search.clear();
                            refresh();
                          }),
                ),
                onSubmitted: (_) => refresh(),
              ),
            ),
            const SizedBox(width: 8),
            IconButton.filledTonal(
              onPressed: refresh,
              icon: const Icon(Icons.refresh),
              iconSize: 16,
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints.tightFor(width: 32, height: 32),
              style: const ButtonStyle(tapTargetSize: MaterialTapTargetSize.shrinkWrap),
              tooltip: 'Refresh',
            ),
          ]),
        ),
      ),
      Expanded(
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? Center(child: Text(_error!, style: TextStyle(color: scheme.error)))
                : _papers.isEmpty
                    ? Center(
                        child: Column(mainAxisSize: MainAxisSize.min, children: [
                        Icon(Icons.library_books_outlined, size: 48, color: scheme.outline),
                        const SizedBox(height: 8),
                        Text('No papers yet — search from the Discover tab.',
                            style: TextStyle(color: scheme.onSurfaceVariant)),
                      ]))
                    : ListView(
                        padding: const EdgeInsets.only(bottom: 12),
                        children: [for (final p in _papers) _paperCard(p)]),
      ),
    ]);
  }

  Widget _paperCard(Map<String, dynamic> p) => Card(
        child: ListTile(
          contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
          title: Text(p['title'] ?? '', style: const TextStyle(fontWeight: FontWeight.w600)),
          subtitle: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const SizedBox(height: 2),
            Text(paperMeta(p)),
            if (p['match'] != null) ...[
              const SizedBox(height: 4),
              Text('“…${p['match']}…”',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontStyle: FontStyle.italic)),
            ],
          ]),
          leading: statusChip(context, p['status']),
          trailing: Row(mainAxisSize: MainAxisSize.min, children: [
            IconButton(
                icon: const Icon(Icons.thumb_up_outlined, size: 20),
                tooltip: 'Like',
                onPressed: () => _feedback(p, 'like')),
            IconButton(
                icon: const Icon(Icons.thumb_down_outlined, size: 20),
                tooltip: 'Dislike',
                onPressed: () => _feedback(p, 'dislike')),
            IconButton(
                icon: const Icon(Icons.delete_outline, size: 20),
                tooltip: 'Delete',
                onPressed: () => _delete(p)),
          ]),
          onTap: () => Navigator.push(
              context, MaterialPageRoute(builder: (_) => DetailPage(paperId: p['id']))),
        ),
      );
}
