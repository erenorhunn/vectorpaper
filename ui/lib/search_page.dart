import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'common.dart';
import 'main.dart';

/// Two-stage discovery: queries → candidate list (nothing downloaded) → user selects →
/// ingest job (download + parse + embed) with progress.
class SearchPage extends StatefulWidget {
  const SearchPage({super.key, required this.state, required this.onIngested});
  final AppState state;
  final VoidCallback onIngested;
  @override
  State<SearchPage> createState() => _SearchPageState();
}

class _SearchPageState extends State<SearchPage> {
  final _query = TextEditingController();
  final List<String> _queries = [];
  List<dynamic> _candidates = [];
  final Set<String> _selected = {};
  int _page = 0;
  bool _searching = false, _searched = false;
  int? _yearMin;
  int? _minCitations;
  String? _jobProgress;
  Timer? _poll;

  String get _pid => widget.state.project!['id'];
  bool get _filtersActive => _yearMin != null || _minCitations != null;

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  void _addQuery(String q) {
    q = q.trim();
    if (q.isNotEmpty && !_queries.contains(q)) setState(() => _queries.add(q));
    _query.clear();
  }

  Future<void> _openFilters() async {
    final result = await showDialog<(int?, int?)>(
      context: context,
      builder: (_) => _FilterDialog(yearMin: _yearMin, minCitations: _minCitations),
    );
    if (result != null) {
      setState(() {
        _yearMin = result.$1;
        _minCitations = result.$2;
      });
    }
  }

  Future<void> _openSuggestions() async {
    final picked = await showDialog<List<String>>(context: context, builder: (_) => _SuggestDialog(pid: _pid));
    if (picked != null) {
      for (final q in picked) {
        _addQuery(q);
      }
    }
  }

  Future<void> _search({bool more = false}) async {
    _addQuery(_query.text); // pending text counts too
    if (_queries.isEmpty) return;
    setState(() {
      _searching = true;
      if (!more) {
        _page = 0;
        _candidates = [];
        _selected.clear();
      }
    });
    try {
      final r = await Api.post('/projects/$_pid/discover', {
        'queries': _queries,
        'page': _page,
        if (_yearMin != null) 'year_min': _yearMin,
        if (_minCitations != null) 'min_citations': _minCitations,
      });
      final known = _candidates.map((p) => p['id']).toSet();
      final fresh = (r['papers'] as List).where((p) => !known.contains(p['id']));
      setState(() {
        _candidates = [..._candidates, ...fresh];
        _page += 1;
        _searched = true;
      });
    } catch (e) {
      if (mounted) snack(context, 'Search failed: $e');
    } finally {
      if (mounted) setState(() => _searching = false);
    }
  }

  Future<void> _ingest() async {
    final r = await Api.post('/projects/$_pid/ingest', {'paper_ids': _selected.toList()});
    setState(() => _jobProgress = 'starting…');
    _poll = Timer.periodic(const Duration(seconds: 4), (t) async {
      try {
        final job = await Api.get('/jobs/${r['job_id']}');
        if (!mounted) return t.cancel();
        setState(() => _jobProgress = job['progress'] ?? job['status']);
        if (job['status'] == 'done' || job['status'] == 'failed') {
          t.cancel();
          setState(() {
            _jobProgress = null;
            _selected.clear();
          });
          if (job['status'] == 'done') {
            snack(context, 'Download and processing complete');
            widget.onIngested();
          } else {
            snack(context, 'Processing failed: ${job['progress']}');
          }
        }
      } catch (_) {} // transient poll errors — keep trying
    });
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          // --- multi-query input ---
          if (_queries.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Wrap(spacing: 6, runSpacing: 6, children: [
                for (final q in _queries)
                  InputChip(label: Text(q), onDeleted: () => setState(() => _queries.remove(q))),
              ]),
            ),
          SizedBox(
            height: 32,
            child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Expanded(
                child: TextField(
                  controller: _query,
                  style: const TextStyle(fontSize: 13),
                  decoration: const InputDecoration(
                      isDense: true,
                      contentPadding: EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                      prefixIcon: Icon(Icons.search, size: 18),
                      prefixIconConstraints: BoxConstraints(minWidth: 32, minHeight: 32),
                      hintText: 'Type a search query and press Enter — add as many as you like'),
                  onSubmitted: _addQuery,
                ),
              ),
              const SizedBox(width: 8),
              IconButton.outlined(
                onPressed: _openFilters,
                tooltip: 'Filters',
                iconSize: 16,
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints.tightFor(width: 32, height: 32),
                style: const ButtonStyle(tapTargetSize: MaterialTapTargetSize.shrinkWrap),
                icon: Icon(_filtersActive ? Icons.filter_alt : Icons.filter_alt_outlined,
                    color: _filtersActive ? scheme.primary : null),
              ),
              const SizedBox(width: 8),
              IconButton.outlined(
                onPressed: _openSuggestions,
                tooltip: 'AI query suggestions',
                iconSize: 16,
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints.tightFor(width: 32, height: 32),
                style: const ButtonStyle(tapTargetSize: MaterialTapTargetSize.shrinkWrap),
                icon: const Icon(Icons.lightbulb_outline),
              ),
              const SizedBox(width: 8),
              FilledButton.icon(
                onPressed: _searching || _jobProgress != null ? null : () => _search(),
                style: FilledButton.styleFrom(
                  visualDensity: VisualDensity.compact,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  textStyle: const TextStyle(fontSize: 13),
                  minimumSize: const Size(0, 32),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
                icon: _searching
                    ? const SizedBox(
                        width: 13, height: 13, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.travel_explore, size: 16),
                label: const Text('Search'),
              ),
            ]),
          ),
        ]),
      ),
      const SizedBox(height: 8),
      // --- candidates ---
      Expanded(
        child: !_searched
            ? Center(
                child: Text(
                    'Results come from arXiv and Semantic Scholar;\n'
                    'only what you select gets downloaded.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: scheme.onSurfaceVariant)))
            : _candidates.isEmpty
                ? const Center(child: Text('No results — try different queries or filters.'))
                : ListView(children: [
                    for (final p in _candidates) _candidateCard(p),
                    Padding(
                      padding: const EdgeInsets.all(12),
                      child: Center(
                        child: OutlinedButton.icon(
                          onPressed: _searching ? null : () => _search(more: true),
                          icon: const Icon(Icons.expand_more),
                          label: const Text('Load more results'),
                        ),
                      ),
                    ),
                  ]),
      ),
      // --- ingest bar ---
      if (_jobProgress != null)
        Container(
          padding: const EdgeInsets.all(12),
          color: scheme.surfaceContainerHigh,
          child: Row(children: [
            const SizedBox(
                width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
            const SizedBox(width: 12),
            Expanded(child: Text('Processing: $_jobProgress')),
          ]),
        )
      else if (_selected.isNotEmpty)
        Container(
          padding: const EdgeInsets.all(12),
          color: scheme.surfaceContainerHigh,
          child: Row(children: [
            Expanded(child: Text('${_selected.length} papers selected')),
            FilledButton.icon(
              onPressed: _ingest,
              icon: const Icon(Icons.download, size: 18),
              label: const Text('Download & process'),
            ),
          ]),
        ),
    ]);
  }

  Widget _candidateCard(Map<String, dynamic> p) {
    final inLibrary = p['status'] != 'discovered';
    final selectable = !inLibrary && p['downloadable'] == true;
    final authors = (p['authors'] as List? ?? []).join(', ');
    return Card(
      child: ExpansionTile(
        leading: Checkbox(
          value: _selected.contains(p['id']),
          onChanged: selectable
              ? (v) => setState(() => v == true ? _selected.add(p['id']) : _selected.remove(p['id']))
              : null,
        ),
        title: Text(p['title'] ?? '', style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(paperMeta(p)),
        trailing: Row(mainAxisSize: MainAxisSize.min, children: [
          if (inLibrary) ...[statusChip(context, p['status']), const SizedBox(width: 8)],
          const Icon(Icons.expand_more),
        ]),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if ((p['abstract'] ?? '').isNotEmpty) ...[
            Text('Abstract', style: Theme.of(context).textTheme.labelLarge),
            const SizedBox(height: 4),
            SelectableText(p['abstract']),
            const SizedBox(height: 12),
          ],
          if (authors.isNotEmpty) _detailRow('Authors', authors),
          if (p['venue'] != null) _detailRow('Venue', p['venue']),
          if (p['doi'] != null) _detailRow('DOI', p['doi']),
          if (p['arxiv_id'] != null) _detailRow('arXiv ID', p['arxiv_id']),
        ],
      ),
    );
  }

  Widget _detailRow(String label, String value) => Padding(
        padding: const EdgeInsets.only(bottom: 4),
        child: RichText(
          text: TextSpan(style: DefaultTextStyle.of(context).style, children: [
            TextSpan(text: '$label: ', style: const TextStyle(fontWeight: FontWeight.w600)),
            TextSpan(text: value),
          ]),
        ),
      );
}

// ---------------- Filter dialog ----------------

class _FilterDialog extends StatefulWidget {
  const _FilterDialog({required this.yearMin, required this.minCitations});
  final int? yearMin;
  final int? minCitations;
  @override
  State<_FilterDialog> createState() => _FilterDialogState();
}

class _FilterDialogState extends State<_FilterDialog> {
  late final _year = TextEditingController(text: widget.yearMin?.toString() ?? '');
  late final _citations = TextEditingController(text: widget.minCitations?.toString() ?? '');

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('Filters'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(
            controller: _year,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'Minimum year', hintText: 'e.g. 2020'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _citations,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'Minimum citations', hintText: 'e.g. 10'),
          ),
        ]),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, (null, null)),
            child: const Text('Clear'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(
                context, (int.tryParse(_year.text), int.tryParse(_citations.text))),
            child: const Text('Apply'),
          ),
        ],
      );
}

// ---------------- AI suggestion dialog: type a topic, pick queries to add ----------------

class _SuggestDialog extends StatefulWidget {
  const _SuggestDialog({required this.pid});
  final String pid;
  @override
  State<_SuggestDialog> createState() => _SuggestDialogState();
}

class _SuggestDialogState extends State<_SuggestDialog> {
  final _topic = TextEditingController();
  List<String> _suggestions = [];
  final Set<String> _picked = {};
  bool _loading = false;
  String? _error;

  Future<void> _fetch() async {
    if (_topic.text.trim().isEmpty) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final r = await Api.post('/projects/${widget.pid}/search-help', {'topic': _topic.text});
      final qs = List<String>.from(r['queries']);
      setState(() {
        _suggestions = qs;
        _picked
          ..clear()
          ..addAll(qs);
      });
    } catch (e) {
      setState(() => _error = 'Could not get suggestions: $e');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('AI query suggestions'),
        content: SizedBox(
          width: 480,
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(
                child: TextField(
                  controller: _topic,
                  autofocus: true,
                  decoration: const InputDecoration(hintText: 'Describe your research topic'),
                  onSubmitted: (_) => _fetch(),
                ),
              ),
              const SizedBox(width: 8),
              FilledButton.tonalIcon(
                onPressed: _loading ? null : _fetch,
                icon: _loading
                    ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.auto_awesome, size: 18),
                label: const Text('Suggest'),
              ),
            ]),
            const SizedBox(height: 12),
            if (_error != null) Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            if (_suggestions.isNotEmpty)
              Flexible(
                child: ListView(
                  shrinkWrap: true,
                  children: [
                    for (final s in _suggestions)
                      CheckboxListTile(
                        dense: true,
                        value: _picked.contains(s),
                        onChanged: (v) =>
                            setState(() => v == true ? _picked.add(s) : _picked.remove(s)),
                        title: Text(s),
                        controlAffinity: ListTileControlAffinity.leading,
                      ),
                  ],
                ),
              ),
          ]),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
            onPressed: _picked.isEmpty ? null : () => Navigator.pop(context, _picked.toList()),
            child: Text('Add ${_picked.length} to search'),
          ),
        ],
      );
}
